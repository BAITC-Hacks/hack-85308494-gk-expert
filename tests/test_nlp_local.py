import socket
import unittest
from unittest.mock import patch

from core.nlp_extractor import NLPExtractor


class LocalExtractionTests(unittest.TestCase):
    def extract(self, text, **kwargs):
        with patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")):
            return NLPExtractor(api_key="your_openai_placeholder").process_transcript({"text": text}, **kwargs)

    def test_legacy_cloud_selection_is_local(self):
        for model in ("gpt-4o", "codex-astra", "offline"):
            self.assertEqual(self.extract("Здравствуйте.", model=model)["processing_location"], "local")

    def test_russian_assignee_deadline_and_quote(self):
        text = "Анна, подготовьте отчёт до 25.09.2026."
        task = self.extract(text)["tasks"][0]
        self.assertEqual(task["assignee"], "Анна")
        self.assertEqual(task["deadline"], "до 25.09.2026")
        self.assertEqual(task["source_quote"], text)

    def test_kazakh_task(self):
        task = self.extract("Әлия, есепті жұмаға дейін дайындаңыз.")["tasks"][0]
        self.assertEqual(task["assignee"], "Әлия")
        self.assertEqual(task["deadline"], "жұмаға дейін")

    def test_mixed_speech(self):
        task = self.extract("Айдар, подготовьте смету ертең.")["tasks"][0]
        self.assertEqual(task["assignee"], "Айдар")
        self.assertEqual(task["deadline"], "ертең")

    def test_unknown_people_dates_are_not_invented(self):
        protocol = self.extract("Нужно подготовить отчёт.")
        self.assertEqual(protocol["tasks"][0]["assignee"], "Не указан")
        self.assertEqual(protocol["tasks"][0]["deadline"], "Не указан")
        self.assertEqual(protocol["participants"], [])
        self.assertEqual(protocol["summary"][0]["risks"], [])
        self.assertEqual(protocol["summary"][0]["decisions"], [])

    def test_non_meeting_has_no_fabricated_tasks(self):
        protocol = self.extract("Привет! Как дела? Всё хорошо.")
        self.assertEqual(protocol["tasks"], [])
        self.assertEqual(protocol["transcript"], "Привет! Как дела? Всё хорошо.")

    def test_empty_audio_has_no_generic_fake_protocol_content(self):
        protocol = self.extract("")
        self.assertEqual(protocol["dialogue"], [])
        self.assertEqual(protocol["summary"][0]["key_points"], [])
        self.assertTrue(any("не обнаружена" in w for w in protocol["warnings"]))

    def test_adjacent_whisper_segments_keep_assignee_and_deadline(self):
        data = {"segments": [{"start": 1, "text": "Анна, подготовьте отчёт"},
                             {"start": 3, "text": "до пятницы."}]}
        protocol = NLPExtractor().process_transcript(data)
        self.assertEqual(len(protocol["tasks"]), 1)
        self.assertEqual(protocol["tasks"][0]["deadline"], "до пятницы")
        self.assertEqual(len(protocol["dialogue"]), 2)

    def test_label_preserved_without_claiming_voice_recognition(self):
        protocol = self.extract("Анна: Я подготовлю отчёт завтра.")
        self.assertEqual(protocol["tasks"][0]["assignee"], "Анна")
        self.assertEqual(protocol["diarization"], "not_performed")


if __name__ == "__main__":
    unittest.main()
