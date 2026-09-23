package kz.samruk.meetingai

import kz.samruk.meetingai.session.*
import org.junit.Assert.*
import org.junit.Test

class OverlayStateTest {
    @Test fun redIndicatorOnlyMeansCaptureIsActuallyRunning() {
        CapturePhase.entries.forEach { phase ->
            assertEquals(phase == CapturePhase.RECORDING, OverlayState(phase = phase).recording)
        }
    }
    @Test fun transitionStatesPreventDuplicateStartRequests() {
        listOf(CapturePhase.PERMISSION, CapturePhase.STARTING, CapturePhase.STOPPING).forEach {
            assertTrue(OverlayState(phase = it).busy)
        }
        assertFalse(OverlayState(phase = CapturePhase.OFF).busy)
    }
    @Test fun unknownVoiceDoesNotGetAnInventedSpeakerNumber() {
        assertEquals("Голос не определён", SpeakerLabels.label("unknown", emptyMap()))
        assertEquals("Спикер 2", SpeakerLabels.label("speaker_2", emptyMap()))
        assertEquals("Спикер 1 · Әсел", SpeakerLabels.label("speaker_1",
            mapOf("speaker_1" to Speaker("Әсел", "self"))))
    }
}
