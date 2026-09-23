package kz.samruk.meetingai.overlay

import android.content.Context
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.text.style.StyleSpan
import android.text.style.UnderlineSpan
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.*
import kz.samruk.meetingai.session.*

/** Outside touches continue to reach the underlying app. */
class FloatingTranscript(
    private val context: Context,
    private val onToggleRecording: () -> Unit,
    private val onSettings: () -> Unit,
    private val onClose: () -> Unit
) {
    private val windows = context.getSystemService(WindowManager::class.java)
    private val root = LinearLayout(context)
    private val body = LinearLayout(context)
    private val dot = TextView(context)
    private val title = TextView(context)
    private val fold = Button(context)
    private val power = Button(context)
    private val status = TextView(context)
    private val text = TextView(context)
    private val scroll = ScrollView(context)
    private val tabs = ArrayList<Button>()
    private var expanded = false
    private var important = false
    private var lastTextKey = ""
    private var state = OverlayState()
    private var attached = false
    private val width: Int get() = minOf(dp(338), context.resources.displayMetrics.widthPixels - dp(16))
    private val params = WindowManager.LayoutParams(
        dp(254), WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
        PixelFormat.TRANSLUCENT
    ).apply { gravity = Gravity.TOP or Gravity.END; x = dp(8); y = dp(10) }

    init {
        root.orientation = LinearLayout.VERTICAL
        root.setPadding(dp(8), dp(4), dp(8), dp(7))
        root.background = GradientDrawable(GradientDrawable.Orientation.TL_BR,
            intArrayOf(0xF224192F.toInt(), 0xF2162033.toInt())).apply {
            cornerRadius = dp(17).toFloat(); setStroke(dp(1), 0x775F4E7B)
        }
        root.elevation = dp(9).toFloat()
        val header = LinearLayout(context).apply { gravity = Gravity.CENTER_VERTICAL }
        dot.text = "●"; dot.textSize = 15f; dot.contentDescription = "Запись выключена"
        header.addView(dot, LinearLayout.LayoutParams(dp(19), dp(40)))
        title.setTextColor(0xFFEAE0F5.toInt()); title.textSize = 12f
        title.typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        title.text = "Транскрипт"; title.gravity = Gravity.CENTER_VERTICAL
        header.addView(title, LinearLayout.LayoutParams(0, dp(40), 1f))
        title.setOnClickListener { toggleExpanded() }
        styleButton(fold, "⌄"); fold.contentDescription = "Развернуть окно"
        fold.setOnClickListener { toggleExpanded() }
        header.addView(fold, LinearLayout.LayoutParams(dp(40), dp(42)))
        styleButton(power, "Вкл"); power.contentDescription = "Включить запись"
        power.setOnClickListener { onToggleRecording() }
        header.addView(power, LinearLayout.LayoutParams(dp(52), dp(42)))
        val close = Button(context)
        styleButton(close, "×"); close.contentDescription = "Выключить запись и закрыть окно"
        close.setOnClickListener { onClose() }
        header.addView(close, LinearLayout.LayoutParams(dp(36), dp(42)))
        root.addView(header)
        body.orientation = LinearLayout.VERTICAL; body.visibility = View.GONE
        val tabRow = LinearLayout(context)
        for ((index, name) in listOf("Транскрипт", "Важное").withIndex()) {
            val button = Button(context); styleButton(button, name)
            button.setOnClickListener { important = index == 1; lastTextKey = ""; render(state) }
            tabs.add(button)
            tabRow.addView(button, LinearLayout.LayoutParams(0, dp(42), 1f))
        }
        body.addView(tabRow)
        text.textSize = 13f; text.setTextColor(0xFFE7DEEF.toInt())
        text.setLineSpacing(dp(4).toFloat(), 1.0f); text.setPadding(dp(8), dp(12), dp(8), dp(12))
        scroll.addView(text)
        body.addView(scroll, LinearLayout.LayoutParams(-1, dp(248)))
        status.textSize = 10f; status.setTextColor(0xFFAFA4C2.toInt())
        status.setPadding(dp(8), dp(7), dp(8), dp(3)); body.addView(status)
        val settings = Button(context); styleButton(settings, "Настройки подключения"); settings.textSize = 10f
        settings.setOnClickListener { onSettings() }
        body.addView(settings, LinearLayout.LayoutParams(-1, dp(38)))
        root.addView(body)
    }
    fun show() {
        if (!attached) { windows.addView(root, params); attached = true }
        render(state)
    }
    fun close() { if (attached) { windows.removeView(root); attached = false } }
    fun resize() {
        if (!attached) return
        params.width = if (expanded) width else minOf(dp(254), width)
        val availableHeight = context.resources.displayMetrics.heightPixels - dp(200)
        scroll.layoutParams = LinearLayout.LayoutParams(-1, minOf(dp(248), maxOf(dp(90), availableHeight)))
        windows.updateViewLayout(root, params)
    }
    private fun toggleExpanded() {
        expanded = !expanded
        body.visibility = if (expanded) View.VISIBLE else View.GONE
        fold.text = if (expanded) "⌃" else "⌄"
        fold.contentDescription = if (expanded) "Свернуть окно" else "Развернуть окно"
        resize()
        if (expanded) { lastTextKey = ""; render(state) }
    }
    fun render(next: OverlayState) {
        state = next
        dot.setTextColor(if (next.recording) 0xFFF16B82.toInt() else 0xFF645C72.toInt())
        dot.contentDescription = if (next.recording) "Запись включена" else "Запись выключена"
        title.text = if (next.recording) "%02d:%02d".format(next.elapsed / 60, next.elapsed % 60) else "Транскрипт"
        power.text = if (next.recording) "Стоп" else if (next.busy) "…" else "Вкл"
        power.contentDescription = if (next.recording) "Выключить запись" else "Включить запись"
        power.isEnabled = !next.busy; power.alpha = if (next.busy) .45f else 1f
        status.text = next.message
        tabs.forEachIndexed { i, button ->
            button.setTextColor(if ((i == 1) == important) 0xFFD9BBFF.toInt() else 0xFF9D92AE.toInt())
            button.isSelected = (i == 1) == important
        }
        if (!expanded) return
        val data = next.transcript
        val key = data.id + ":" + data.revision + ":" + important
        if (key == lastTextKey) return
        lastTextKey = key
        val follow = scroll.scrollY + scroll.height >= text.height - dp(60)
        val content = SpannableStringBuilder()
        val lines = data.transcript.filter { !important || it.highlights.isNotEmpty() }
        for (line in lines) {
            if (content.isNotEmpty()) content.append("\n\n")
            val nameStart = content.length
            content.append(SpeakerLabels.label(line.speaker, data.speakers))
            content.setSpan(StyleSpan(Typeface.BOLD), nameStart, content.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            content.setSpan(UnderlineSpan(), nameStart, content.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            content.setSpan(ForegroundColorSpan(0xFFC9ACEF.toInt()), nameStart, content.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            content.append("\n")
            val textStart = content.length; content.append(line.text)
            for (range in line.highlights) {
                if (range.size != 2) continue
                val length = line.text.codePointCount(0, line.text.length)
                val start = line.text.offsetByCodePoints(0, range[0].coerceIn(0, length))
                val end = line.text.offsetByCodePoints(0, range[1].coerceIn(range[0].coerceIn(0, length), length))
                if (end > start) {
                    content.setSpan(UnderlineSpan(), textStart + start, textStart + end, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                    content.setSpan(ForegroundColorSpan(0xFFECD5AF.toInt()), textStart + start, textStart + end, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                }
            }
        }
        text.text = if (content.isEmpty())
            if (important) "Здесь появятся важные фразы разговора." else "Включите запись. Здесь появится речь из звука телефона."
        else content
        if (follow) scroll.post { scroll.fullScroll(View.FOCUS_DOWN) }
    }
    private fun styleButton(button: Button, label: String) {
        button.text = label; button.isAllCaps = false; button.textSize = 12f
        button.minWidth = 0; button.minimumWidth = 0; button.minHeight = 0; button.minimumHeight = 0
        button.setPadding(0, 0, 0, 0); button.setTextColor(0xFFCDBFE0.toInt())
        button.background = GradientDrawable().apply { setColor(0x163B3157); cornerRadius = dp(10).toFloat() }
    }
    private fun dp(value: Int) = (value * context.resources.displayMetrics.density).toInt()
}
