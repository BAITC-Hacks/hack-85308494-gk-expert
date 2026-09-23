"""Local STT contract and privacy checks; no model downloads or cloud calls."""
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.stt_engine import SpeechToTextEngine


class TestOfflineSTT(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="stt_contract_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.audio = self.root / "sample.wav"
        self.audio.write_bytes(b"synthetic test input consumed by a stub")
        self.model_dir = self.root / "model"
        self.model_dir.mkdir()
        for name in ("config.json", "model.bin", "tokenizer.json"):
            (self.model_dir / name).write_bytes(b"stub")

        self.model = Mock()
        self.model.model.is_multilingual = True
        word = SimpleNamespace(start=0.5, end=1.0, word=" Сәлем")
        segment = SimpleNamespace(id=1, start=0.5, end=2.0, text=" Сәлем. Обсудим задачу. ", words=[word])
        self.model.transcribe.side_effect = lambda *args, **kwargs: (
            iter([segment]), SimpleNamespace(duration=3.14159, language="kk")
        )
        self.factory = Mock(return_value=self.model)
        stub = ModuleType("faster_whisper")
        stub.WhisperModel = self.factory
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {"faster_whisper": stub}).start()
        patch.dict(os.environ, {
            "OPENAI_API_KEY": "unused-test-value",
            "STT_DEVICE": "cpu", "STT_COMPUTE_TYPE": "int8",
        }).start()
        self.network = patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")).start()

    def engine(self):
        return SpeechToTextEngine(api_key="unused", model_dir=str(self.model_dir))

    def test_local_only_loading_and_existing_output_contract(self):
        result = self.engine().transcribe(str(self.audio))
        self.factory.assert_called_once_with(
            str(self.model_dir.resolve()), device="cpu", compute_type="int8", local_files_only=True
        )
        self.assertEqual(result["text"], "Сәлем. Обсудим задачу.")
        self.assertEqual(result["duration"], 3.14)
        self.assertEqual(result["language"], "kk")
        self.assertEqual(result["segments"][0]["duration"], 1.5)
        self.assertEqual(result["segments"][0]["words"][0]["word"], " Сәлем")
        self.assertEqual(result["filename"], "sample.wav")
        self.assertEqual(result["diarization"], "not_performed")
        self.network.assert_not_called()

    def test_auto_and_mixed_detect_language_per_segment(self):
        engine = self.engine()
        for language in (None, "auto", "mixed"):
            with self.subTest(language=language):
                engine.transcribe(str(self.audio), language=language)
                options = self.model.transcribe.call_args.kwargs
                self.assertIsNone(options["language"])
                self.assertTrue(options["multilingual"])
                self.assertEqual(options["task"], "transcribe")
        self.factory.assert_called_once()

    def test_explicit_russian_and_kazakh_languages(self):
        engine = self.engine()
        for language, expected in (("ru", "ru"), ("kk", "kk"), ("kz", "kk")):
            with self.subTest(language=language):
                engine.transcribe(str(self.audio), language=language)
                self.assertEqual(self.model.transcribe.call_args.kwargs["language"], expected)
                self.assertFalse(self.model.transcribe.call_args.kwargs["multilingual"])

    def test_missing_model_fails_before_loading_or_network(self):
        (self.model_dir / "model.bin").unlink()
        with self.assertRaisesRegex(RuntimeError, "STT_MODEL_DIR"):
            self.engine().transcribe(str(self.audio))
        self.factory.assert_not_called()
        self.network.assert_not_called()

    def test_default_missing_model_with_legacy_key_never_uses_cloud(self):
        with patch.dict(os.environ, {"STT_MODEL_DIR": str(self.root / "missing")}, clear=False):
            engine = SpeechToTextEngine(api_key="your_openai_placeholder")
            with self.assertRaisesRegex(RuntimeError, "STT_MODEL_DIR"):
                engine.transcribe(str(self.audio))
        self.network.assert_not_called()

    def test_missing_tokenizer_cannot_trigger_download(self):
        (self.model_dir / "tokenizer.json").unlink()
        with self.assertRaisesRegex(RuntimeError, "tokenizer.json"):
            self.engine().transcribe(str(self.audio))
        self.factory.assert_not_called()
        self.network.assert_not_called()

    def test_english_only_model_is_rejected(self):
        self.model.model.is_multilingual = False
        with self.assertRaisesRegex(RuntimeError, "многоязычная"):
            self.engine().transcribe(str(self.audio))
        self.model.transcribe.assert_not_called()

    def test_missing_and_empty_audio_fail_before_model_loading(self):
        engine = self.engine()
        with self.assertRaises(FileNotFoundError):
            engine.transcribe(str(self.root / "missing.wav"))
        self.audio.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "пуст"):
            engine.transcribe(str(self.audio))
        self.factory.assert_not_called()

    def test_inference_failure_has_no_cloud_fallback(self):
        self.model.transcribe.side_effect = RuntimeError("local decoder failure")
        with self.assertRaisesRegex(RuntimeError, "local decoder failure"):
            self.engine().transcribe(str(self.audio))
        self.network.assert_not_called()

    def test_empty_result_has_no_invented_speech(self):
        self.model.transcribe.side_effect = lambda *args, **kwargs: (
            iter([]), SimpleNamespace(duration=2.0, language="ru")
        )
        result = self.engine().transcribe(str(self.audio))
        self.assertEqual(result["text"], "")
        self.assertEqual(result["segments"], [])

    def test_custom_context_and_api_key_do_not_enable_cloud(self):
        engine = self.engine()
        engine.set_api_key("another-unused-test-value")
        engine.transcribe(str(self.audio), custom_prompt="План школьного проекта")
        self.assertEqual(self.model.transcribe.call_args.kwargs["initial_prompt"], "План школьного проекта")
        self.network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
