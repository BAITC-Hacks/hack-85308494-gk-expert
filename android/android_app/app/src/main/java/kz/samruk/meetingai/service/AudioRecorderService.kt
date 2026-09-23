package kz.samruk.meetingai.service

import android.app.*
import android.content.Intent
import android.content.pm.ServiceInfo
import android.content.res.Configuration
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.*
import android.provider.Settings
import androidx.core.app.NotificationCompat
import com.google.gson.Gson
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kz.samruk.meetingai.MainActivity
import kz.samruk.meetingai.capture.AudioPacket
import kz.samruk.meetingai.capture.PlaybackCapture
import kz.samruk.meetingai.network.*
import kz.samruk.meetingai.overlay.FloatingTranscript
import kz.samruk.meetingai.session.*
import java.io.File
import java.io.IOException
import java.util.concurrent.atomic.AtomicBoolean

/** Owns capture, upload and the overlay; the Activity is only a permission bridge. */
class AudioRecorderService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val mainHandler = Handler(Looper.getMainLooper())
    private var overlay: FloatingTranscript? = null
    private var pipeline: Job? = null
    private var capture: PlaybackCapture? = null
    private var projection: MediaProjection? = null
    private var projectionCallback: MediaProjection.Callback? = null
    private val stopRequested = AtomicBoolean(false)
    private var closing = false
    private var releasingProjection = false
    private var stopReason: String? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, "Плавающая транскрипция", NotificationManager.IMPORTANCE_LOW))
        scope.launch {
            OverlaySession.state.collect { state -> overlay?.render(state) }
        }
        scope.launch(Dispatchers.IO) {
            val saved = runCatching {
                Gson().fromJson(File(filesDir, "last-transcript.json").readText(), Transcript::class.java)
            }.getOrNull()
            if (saved != null) OverlaySession.change {
                if (it.transcript.id.isEmpty()) it.copy(transcript = saved) else it
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_CLOSE -> {
                closing = true
                overlay?.close(); overlay = null
                stopCapture()
                if (pipeline == null) stopSelf()
            }
            ACTION_STOP -> stopCapture()
            ACTION_START -> {
                showOverlay()
                val data = if (Build.VERSION.SDK_INT >= 33)
                    intent.getParcelableExtra(EXTRA_RESULT_DATA, Intent::class.java)
                else @Suppress("DEPRECATION") intent.getParcelableExtra(EXTRA_RESULT_DATA)
                if (data != null && pipeline == null) beginCapture(intent.getIntExtra(EXTRA_RESULT_CODE, Activity.RESULT_CANCELED), data)
            }
            else -> {
                if (pipeline == null) foreground(false)
                showOverlay()
            }
        }
        // Never restart a recording or reuse a projection grant after process death.
        return START_NOT_STICKY
    }

    private fun showOverlay() {
        if (overlay != null) return
        if (!Settings.canDrawOverlays(this)) {
            OverlaySession.change { it.copy(phase = CapturePhase.ERROR, message = "Разрешите окно поверх приложений") }
            stopSelf(); return
        }
        overlay = FloatingTranscript(this,
            onToggleRecording = {
                if (OverlaySession.state.value.recording) stopCapture()
                else if (!OverlaySession.state.value.busy) launchActivity(MainActivity.ACTION_CAPTURE)
            },
            onSettings = { launchActivity(MainActivity.ACTION_SETTINGS) },
            onClose = {
                closing = true
                overlay?.close(); overlay = null
                stopCapture()
                if (pipeline == null) stopSelf()
            }
        ).also { it.show() }
    }

    private fun launchActivity(action: String) {
        startActivity(Intent(this, MainActivity::class.java).apply {
            this.action = action
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        })
    }

    private fun beginCapture(resultCode: Int, data: Intent) {
        stopRequested.set(false); stopReason = null; releasingProjection = false
        OverlaySession.change { it.copy(phase = CapturePhase.STARTING, message = "Подключаюсь к распознаванию…", elapsed = 0) }
        try {
            foreground(true)
            projection = getSystemService(MediaProjectionManager::class.java).getMediaProjection(resultCode, data)
            projectionCallback = object : MediaProjection.Callback() {
                override fun onStop() {
                    if (!releasingProjection) stopCapture("Android остановил доступ к звуку")
                }
            }.also { projection!!.registerCallback(it, mainHandler) }
        } catch (error: Exception) {
            OverlaySession.change { it.copy(phase = CapturePhase.ERROR, message = "Не удалось открыть звук: " + error.message) }
            foreground(false)
            return
        }
        val settings = MobileSettings(this)
        pipeline = scope.launch {
            var api: TranscriptionApi? = null
            var remoteId: String? = null
            var failure: String? = null
            val didStart = AtomicBoolean(false)
            try {
                val connection = TranscriptionClient.create(settings.serverUrl)
                api = connection
                val sessionId = withContext(Dispatchers.IO) { connection.create(StartMobileSession(settings.language)).id }
                remoteId = sessionId
                if (!stopRequested.get()) {
                    val packets = Channel<AudioPacket>(8)
                    val playback = PlaybackCapture(requireNotNull(projection))
                    capture = playback
                    coroutineScope {
                        val sender = launch(Dispatchers.IO) {
                            for (packet in packets) {
                                val result = try {
                                    connection.append(sessionId, packet.sequence, packet.offset, TranscriptionClient.pcmBody(packet.pcm))
                                } catch (error: IOException) {
                                    // Sequences make one retry safe after a lost HTTP response.
                                    connection.append(sessionId, packet.sequence, packet.offset, TranscriptionClient.pcmBody(packet.pcm))
                                }
                                OverlaySession.change { it.copy(transcript = result) }
                                persist(result)
                            }
                        }
                        try {
                            withContext(Dispatchers.IO) {
                                val captureContext = coroutineContext
                                var lastAudible = 0
                                playback.run(
                                    onStarted = {
                                        if (stopRequested.get()) playback.stop()
                                        else {
                                            didStart.set(true)
                                            OverlaySession.change { it.copy(phase = CapturePhase.RECORDING,
                                                message = "Записываю звук телефона", transcript = Transcript(id = sessionId), elapsed = 0) }
                                        }
                                    },
                                    onLevel = { level, elapsed ->
                                        captureContext.ensureActive()
                                        if (level > .004) lastAudible = elapsed
                                        OverlaySession.change {
                                            it.copy(elapsed = elapsed, message = if (it.phase != CapturePhase.RECORDING) it.message
                                            else if (elapsed - lastAudible >= 8) "Нет доступного звука. Приложение может запрещать захват."
                                            else "Записываю звук телефона")
                                        }
                                    },
                                    onPacket = {
                                        check(packets.trySend(it).isSuccess) { "Распознавание не успевает. Проверьте связь с ПК и повторите." }
                                    }
                                )
                            }
                        } finally {
                            playback.stop()
                            packets.close()
                            OverlaySession.change {
                                if (it.phase == CapturePhase.RECORDING) it.copy(phase = CapturePhase.STOPPING, message = "Дописываю последние фразы…") else it
                            }
                        }
                        sender.join()
                    }
                }
            } catch (error: CancellationException) {
                failure = "Запись остановлена"
                throw error
            } catch (error: Exception) {
                failure = when (error) {
                    is IOException -> "Нет связи с ПК. Проверьте адрес, Wi-Fi и сервер распознавания."
                    is retrofit2.HttpException -> "Сервер не обработал звук (" + error.code() + "). Перезапустите запись."
                    else -> error.message ?: "Не удалось обработать звук"
                }
            } finally {
                capture?.stop(); capture = null
                releasingProjection = true
                projectionCallback?.let { projection?.unregisterCallback(it) }
                projection?.stop(); projection = null; projectionCallback = null
                val finalApi = api
                val finalId = remoteId
                if (finalApi != null && finalId != null) {
                    withContext(NonCancellable + Dispatchers.IO) {
                        runCatching {
                            withTimeout(8000) {
                                val final = finalApi.stop(finalId)
                                if (didStart.get()) {
                                    OverlaySession.change { it.copy(transcript = final) }
                                    persist(final)
                                }
                            }
                        }
                    }
                }
                OverlaySession.change { it.copy(
                    phase = if (failure == null) CapturePhase.OFF else CapturePhase.ERROR,
                    message = failure ?: stopReason ?: "Запись выключена. Текст сохранён."
                ) }
                pipeline = null
                if (closing) stopSelf() else {
                    stopForeground(STOP_FOREGROUND_REMOVE)
                    foreground(false)
                }
            }
        }
    }

    private fun stopCapture(reason: String? = null) {
        stopReason = reason
        stopRequested.set(true)
        capture?.stop()
        if (pipeline != null) OverlaySession.change {
            it.copy(phase = CapturePhase.STOPPING, message = "Запись выключена. Дописываю последние фразы…")
        }
    }

    private fun persist(transcript: Transcript) {
        val target = File(filesDir, "last-transcript.json")
        val temporary = File(filesDir, "last-transcript.tmp")
        temporary.writeText(Gson().toJson(transcript))
        check(temporary.renameTo(target)) { "Не удалось сохранить текст на телефоне" }
    }

    private fun foreground(recording: Boolean) {
        val show = PendingIntent.getActivity(this, 10, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val stop = PendingIntent.getService(this, 11,
            Intent(this, AudioRecorderService::class.java).setAction(if (recording) ACTION_STOP else ACTION_CLOSE),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val notification = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Транскрипт · " + if (recording) "звук телефона" else "запись выключена")
            .setContentText(if (recording) "Расшифровка на вашем ПК. Нажмите «Остановить», чтобы выключить." else "Небольшая панель поверх приложений")
            .setContentIntent(show).setOngoing(true).setSilent(true)
            .addAction(0, if (recording) "Остановить" else "Закрыть панель", stop).build()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE or
                if (recording) ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION else 0)
        } else {
            startForeground(NOTIFICATION_ID, notification,
                if (recording) ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION else 0)
        }
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        overlay?.resize()
    }
    override fun onDestroy() {
        closing = true
        capture?.stop()
        releasingProjection = true
        projectionCallback?.let { projection?.unregisterCallback(it) }
        projection?.stop()
        scope.cancel()
        overlay?.close(); overlay = null
        OverlaySession.change { it.copy(phase = CapturePhase.OFF, message = "Панель закрыта") }
        super.onDestroy()
    }
    companion object {
        const val ACTION_SHOW = "kz.samruk.meetingai.SHOW"
        const val ACTION_START = "kz.samruk.meetingai.START"
        const val ACTION_STOP = "kz.samruk.meetingai.STOP"
        const val ACTION_CLOSE = "kz.samruk.meetingai.CLOSE"
        const val EXTRA_RESULT_CODE = "projection_result_code"
        const val EXTRA_RESULT_DATA = "projection_result_data"
        const val CHANNEL = "FloatingTranscript"
        const val NOTIFICATION_ID = 1001
    }
}
