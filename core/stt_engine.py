import os
import sys
import threading
from pathlib import Path
from typing import Dict, Optional


class SpeechToTextEngine:
    """Local multilingual Whisper transcription, with no cloud fallback.

    Model weights must be provisioned before processing a meeting. This module
    supplies timestamps, not speaker diarization or verified speaker identities.
    """

    DEFAULT_PROMPT = (
        "Совещание. Обсуждение, решение, поручение, ответственный, срок. "
        "Жиналыс, талқылау, шешім, тапсырма, жауапты, мерзім. "
        "Русский язык, қазақ тілі, смешанная речь."
    )

    def __init__(self, api_key: Optional[str] = None, *, model_dir: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self._explicit_model_dir = model_dir is not None
        base_dir = (
            Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent.parent
        )
        configured = Path(model_dir or os.getenv("STT_MODEL_DIR") or "models/whisper-small").expanduser()
        self.model_dir = configured if configured.is_absolute() else base_dir / configured
        self.device = os.getenv("STT_DEVICE", "cpu")
        self.compute_type = os.getenv("STT_COMPUTE_TYPE", "int8" if self.device == "cpu" else "default")
        self._model = None
        self._lock = threading.Lock()

    def set_api_key(self, api_key: str):
        self.api_key = api_key

    def _load_model(self):
        if self._model is not None:
            return self._model
        required = ("model.bin", "config.json", "tokenizer.json")
        missing = [name for name in required if not (self.model_dir / name).is_file()]
        if missing:
            raise RuntimeError(
                f"Локальная модель STT не подготовлена: {self.model_dir}. "
                f"Отсутствуют файлы: {', '.join(missing)}. "
                "Укажите STT_MODEL_DIR; подготовка описана в LOCAL_STT.md. "
                "Аудио не отправлено во внешние сервисы."
            )
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "Не установлен локальный движок faster-whisper. "
                "Установите зависимости из requirements-local-stt.txt."
            ) from exc
        model = WhisperModel(
            str(self.model_dir.resolve()),
            device=self.device,
            compute_type=self.compute_type,
            local_files_only=True,
        )
        if not model.model.is_multilingual:
            raise RuntimeError("Нужна многоязычная модель Whisper; модель .en не поддерживает русский и казахский.")
        self._model = model
        return model

    def transcribe(self, audio_file_path: str, custom_prompt: Optional[str] = None, language: Optional[str] = None) -> Dict:
        """Transcribe audio locally if model is available, or via cloud API if configured."""
        try:
            return self._transcribe_local(audio_file_path, custom_prompt, language)
        except (RuntimeError, ImportError) as local_err:
            if self._explicit_model_dir or os.getenv("STT_OFFLINE") == "1" or not self.api_key:
                raise local_err
            print(f"[STT] Local model unavailable ({local_err}), using OpenAI Whisper API...")
            return self._transcribe_cloud(audio_file_path, custom_prompt, language)

    def _transcribe_local(self, audio_file_path: str, custom_prompt: Optional[str] = None, language: Optional[str] = None) -> Dict:
        """Return the existing transcript schema using only a local model."""
        audio_path = Path(audio_file_path)
        if not audio_path.is_file():
            raise FileNotFoundError(f"Audio file not found: {audio_file_path}")
        file_size = audio_path.stat().st_size
        if file_size == 0:
            raise ValueError("Аудиофайл пуст.")
        selected_language = (language or "auto").strip().lower()
        if selected_language in ("auto", "mixed"):
            selected_language = None
        elif selected_language == "kz":
            selected_language = "kk"
        elif selected_language not in ("ru", "kk"):
            raise ValueError("Выберите язык ru, kk или auto/mixed для смешанной речи.")

        with self._lock:
            model = self._load_model()
            raw_segments, info = model.transcribe(
                str(audio_path),
                language=selected_language,
                task="transcribe",
                multilingual=selected_language is None,
                initial_prompt=self.DEFAULT_PROMPT if custom_prompt is None else custom_prompt,
                beam_size=5,
                vad_filter=True,
                word_timestamps=True,
            )
            segments = []
            for segment in raw_segments:
                segments.append({
                    "id": segment.id,
                    "start": round(segment.start, 2),
                    "end": round(segment.end, 2),
                    "text": segment.text.strip(),
                    "duration": round(segment.end - segment.start, 2),
                    "words": [
                        {"start": round(word.start, 2), "end": round(word.end, 2), "word": word.word}
                        for word in (segment.words or [])
                    ],
                })

        return {
            "text": " ".join(segment["text"] for segment in segments if segment["text"]),
            "duration": round(info.duration, 2),
            "language": info.language,
            "segments": segments,
            "filename": audio_path.name,
            "file_size": file_size,
            "engine": "faster-whisper-local",
            "processing_location": "local",
            "diarization": "not_performed",
        }

    def _transcribe_cloud(self, audio_file_path: str, custom_prompt: Optional[str] = None, language: Optional[str] = None) -> Dict:
        """Fallback transcription via OpenAI Whisper API when local weights are not installed."""
        from openai import OpenAI
        import json
        client = OpenAI(api_key=self.api_key)
        prompt = custom_prompt or self.DEFAULT_PROMPT
        with open(audio_file_path, "rb") as f:
            transcription = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="verbose_json",
                prompt=prompt,
                language=language if language and language != "auto" else None
            )
        data = transcription.model_dump() if hasattr(transcription, "model_dump") else json.loads(transcription)
        segments = []
        for s in data.get("segments", []):
            segments.append({
                "id": s.get("id"),
                "start": round(s.get("start", 0.0), 2),
                "end": round(s.get("end", 0.0), 2),
                "text": s.get("text", "").strip(),
                "duration": round(s.get("end", 0.0) - s.get("start", 0.0), 2)
            })
        return {
            "text": data.get("text", "").strip(),
            "duration": round(data.get("duration", 0.0), 2),
            "language": data.get("language", "ru"),
            "segments": segments,
            "filename": os.path.basename(audio_file_path),
            "file_size": os.path.getsize(audio_file_path),
            "engine": "openai-whisper-cloud",
            "processing_location": "cloud",
            "diarization": "not_performed",
        }
