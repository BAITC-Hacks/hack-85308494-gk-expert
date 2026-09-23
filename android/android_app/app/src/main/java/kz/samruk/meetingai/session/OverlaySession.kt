package kz.samruk.meetingai.session

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

enum class CapturePhase { OFF, PERMISSION, STARTING, RECORDING, STOPPING, ERROR }
data class Speaker(val name: String = "", val name_source: String = "unknown")
data class SpeechLine(
    val id: String = "", val speaker: String = "", val text: String = "",
    val start: String = "", val highlights: List<List<Int>> = emptyList()
)
data class Transcript(
    val id: String = "", val revision: Int = 0,
    val transcript: List<SpeechLine> = emptyList(),
    val speakers: Map<String, Speaker> = emptyMap()
)
data class OverlayState(
    val phase: CapturePhase = CapturePhase.OFF,
    val message: String = "Запись выключена",
    val elapsed: Int = 0,
    val transcript: Transcript = Transcript()
) {
    val recording: Boolean get() = phase == CapturePhase.RECORDING
    val busy: Boolean get() = phase in setOf(CapturePhase.PERMISSION, CapturePhase.STARTING, CapturePhase.STOPPING)
}
object OverlaySession {
    private val mutable = MutableStateFlow(OverlayState())
    val state = mutable.asStateFlow()
    fun change(block: (OverlayState) -> OverlayState) = mutable.update(block)
}
object SpeakerLabels {
    fun label(id: String, speakers: Map<String, Speaker>): String {
        if (id == "unknown") return "Голос не определён"
        val number = id.removePrefix("speaker_").toIntOrNull()
        val label = if (number != null) "Спикер $number" else "Участник"
        val speaker = speakers[id]
        return if (speaker != null && speaker.name_source in setOf("self", "manual", "role"))
            label + " · " + speaker.name else label
    }
}
