package kz.samruk.meetingai.capture

import android.annotation.SuppressLint
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.projection.MediaProjection
import java.io.ByteArrayOutputStream
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.sqrt

data class AudioPacket(val sequence: Int, val offset: Double, val pcm: ByteArray)

/** Copies allowed phone playback, never the microphone. Run on an IO thread. */
class PlaybackCapture(private val projection: MediaProjection) {
    private val running = AtomicBoolean(true)
    @Volatile private var recorder: AudioRecord? = null
    @SuppressLint("MissingPermission")
    fun run(onStarted: () -> Unit, onLevel: (Double, Int) -> Unit, onPacket: (AudioPacket) -> Unit) {
        if (!running.get()) return
        val config = AudioPlaybackCaptureConfiguration.Builder(projection)
            .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
            .addMatchingUsage(AudioAttributes.USAGE_GAME)
            .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN).build()
        val format = AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setSampleRate(RATE).setChannelMask(AudioFormat.CHANNEL_IN_MONO).build()
        val minimum = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        check(minimum > 0) { "Телефон не поддерживает захват звука 16 кГц" }
        val audio = AudioRecord.Builder().setAudioFormat(format)
            .setBufferSizeInBytes(maxOf(minimum * 2, RATE)).setAudioPlaybackCaptureConfig(config).build()
        recorder = audio
        val pending = ByteArrayOutputStream(CHUNK_BYTES)
        var offsetBytes = 0L
        var totalBytes = 0L
        var sequence = 0
        fun flush() {
            if (pending.size() == 0) return
            val bytes = pending.toByteArray()
            onPacket(AudioPacket(sequence++, offsetBytes.toDouble() / (RATE * 2), bytes))
            offsetBytes += bytes.size
            pending.reset()
        }
        try {
            check(audio.state == AudioRecord.STATE_INITIALIZED) { "Android не открыл источник звука" }
            if (!running.get()) return
            audio.startRecording()
            check(audio.recordingState == AudioRecord.RECORDSTATE_RECORDING) { "Запись не началась" }
            onStarted()
            val buffer = ByteArray(3200)
            while (running.get()) {
                val count = audio.read(buffer, 0, buffer.size, AudioRecord.READ_BLOCKING)
                if (count < 0) {
                    if (!running.get()) break
                    error("Источник звука отключился ($count)")
                }
                if (count == 0) continue
                pending.write(buffer, 0, count)
                totalBytes += count
                var sum = 0.0
                for (i in 0 until count - 1 step 2) {
                    val sample = ((buffer[i].toInt() and 255) or (buffer[i + 1].toInt() shl 8)).toShort().toDouble() / 32768
                    sum += sample * sample
                }
                onLevel(sqrt(sum / maxOf(1, count / 2)), (totalBytes / (RATE * 2)).toInt())
                if (pending.size() >= CHUNK_BYTES) flush()
            }
            flush()
        } finally {
            running.set(false)
            runCatching { if (audio.recordingState == AudioRecord.RECORDSTATE_RECORDING) audio.stop() }
            audio.release()
            recorder = null
        }
    }
    fun stop() {
        running.set(false)
        runCatching { recorder?.stop() }
    }
    companion object {
        const val RATE = 16000
        const val CHUNK_BYTES = RATE * 2 * 4
    }
}
