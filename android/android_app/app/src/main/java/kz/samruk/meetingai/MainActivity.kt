package kz.samruk.meetingai

import android.Manifest
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.media.projection.MediaProjectionConfig
import android.media.projection.MediaProjectionManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.*
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import kz.samruk.meetingai.network.MobileSettings
import kz.samruk.meetingai.service.AudioRecorderService
import kz.samruk.meetingai.session.CapturePhase
import kz.samruk.meetingai.session.OverlaySession

/** Only permissions/settings use an Activity. Normal launches finish into the overlay. */
class MainActivity : ComponentActivity() {
    private var handled = false
    private val settings by lazy { MobileSettings(this) }

    private val overlayPermission = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        if (Settings.canDrawOverlays(this)) proceed() else denied("Не разрешено окно поверх приложений")
    }
    private val notifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) {
        afterNotifications()
    }
    private val audioPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) requestCapture() else denied("Android не разрешил захват звука")
    }
    private val capturePermission = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val data = result.data
        if (result.resultCode == RESULT_OK && data != null) {
            ContextCompat.startForegroundService(this, Intent(this, AudioRecorderService::class.java).apply {
                action = AudioRecorderService.ACTION_START
                putExtra(AudioRecorderService.EXTRA_RESULT_CODE, result.resultCode)
                putExtra(AudioRecorderService.EXTRA_RESULT_DATA, data)
            })
            finish()
        } else denied("Запись не включена: разрешение отменено")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        handled = savedInstanceState?.getBoolean("handled") ?: false
        if (!handled) {
            handled = true
            if (Settings.canDrawOverlays(this)) proceed() else {
                AlertDialog.Builder(this).setTitle("Небольшое окно поверх приложений")
                    .setMessage("Разрешите показ поверх других приложений. После этого останется только свёрнутая панель сверху. Запись включается отдельно.")
                    .setPositiveButton("Разрешить") { _, _ ->
                        overlayPermission.launch(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
                    }.setNegativeButton("Отмена") { _, _ -> finish() }
                    .setOnCancelListener { finish() }.show()
            }
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putBoolean("handled", handled)
        super.onSaveInstanceState(outState)
    }

    private fun proceed() {
        if (!settings.configured || intent.action == ACTION_SETTINGS) {
            showSettings()
            return
        }
        requestNotifications()
    }

    private fun requestNotifications() {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            notifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else afterNotifications()
    }

    private fun afterNotifications() {
        ContextCompat.startForegroundService(this, Intent(this, AudioRecorderService::class.java).apply {
            action = AudioRecorderService.ACTION_SHOW
        })
        if (intent.action == ACTION_CAPTURE) {
            if (OverlaySession.state.value.phase in setOf(CapturePhase.STARTING, CapturePhase.RECORDING, CapturePhase.STOPPING)) {
                finish(); return
            }
            OverlaySession.change { it.copy(phase = CapturePhase.PERMISSION, message = "Подтвердите доступ к звуку в окне Android") }
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED)
                requestCapture()
            else audioPermission.launch(Manifest.permission.RECORD_AUDIO)
        } else finish()
    }

    private fun requestCapture() {
        val manager = getSystemService(MediaProjectionManager::class.java)
        val request = if (Build.VERSION.SDK_INT >= 34)
            manager.createScreenCaptureIntent(MediaProjectionConfig.createConfigForDefaultDisplay())
        else manager.createScreenCaptureIntent()
        capturePermission.launch(request)
    }

    private fun denied(message: String) {
        OverlaySession.change { it.copy(phase = CapturePhase.OFF, message = message) }
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
        finish()
    }

    private fun showSettings() {
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(40, 12, 40, 8)
        }
        content.addView(TextView(this).apply {
            text = "Распознавание выполняется на вашем ПК. Звук телефона передаётся на указанный сервер.\n\nАдрес сервера:"
            textSize = 13f
        })
        val server = EditText(this).apply {
            setSingleLine(true); setText(settings.serverUrl)
            hint = "http://192.168.1.10:8770/"
            inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_URI
        }
        content.addView(server)
        val language = Spinner(this)
        language.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item,
            listOf("Язык: автоматически", "Русский", "Қазақша", "English"))
        language.setSelection(listOf("", "ru", "kk", "en").indexOf(settings.language).coerceAtLeast(0))
        content.addView(language)
        content.addView(TextView(this).apply {
            text = "Захватывается только разрешённый приложениями звук. Звонки и защищённое аудио могут быть недоступны. Микрофон не записывается."
            textSize = 11f
        })
        val dialog = AlertDialog.Builder(this).setTitle("Подключение к распознаванию")
            .setView(content).setPositiveButton("Сохранить", null)
            .setNegativeButton("Отмена") { _, _ -> finish() }
            .setOnCancelListener { finish() }.create()
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val url = server.text.toString().trim()
                if (!MobileSettings.validUrl(url)) {
                    server.error = "Введите http://адрес:порт/ без пути и пароля"
                    return@setOnClickListener
                }
                settings.serverUrl = url
                settings.language = listOf("", "ru", "kk", "en")[language.selectedItemPosition]
                settings.configured = true
                dialog.dismiss()
                // A settings action never implicitly starts recording.
                requestNotifications()
            }
        }
        dialog.show()
    }

    companion object {
        const val ACTION_CAPTURE = "kz.samruk.meetingai.CAPTURE"
        const val ACTION_SETTINGS = "kz.samruk.meetingai.SETTINGS"
    }
}
