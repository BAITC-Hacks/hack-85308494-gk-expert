import os
import time
import math
import struct
import wave
import threading
import uuid
from functools import wraps
from bisect import bisect_right
from typing import List, Dict, Optional

try:
    import pyaudiowpatch as pyaudio
except ImportError:
    try:
        import pyaudio
    except ImportError:
        pyaudio = None


# PortAudio has process-global initialization/device tables. HTTP handlers and
# pywebview callbacks run on different threads, so serialize native API calls.
_PORTAUDIO_LOCK = threading.RLock()


def _serialized_audio_call(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _PORTAUDIO_LOCK:
            return function(*args, **kwargs)
    return wrapped


class AudioRecorder:
    """

    OBS-style Multi-Track Audio Recorder for Windows supporting:
    - Independent Microphone Track (user's voice)
    - Independent System / Conference Track (Zoom, Discord, Meet via WASAPI loopback)
    - Synchronized Master Mix (combining both tracks)
    - Real-time dual VU meters (MIC & SYS)
    - Separate track isolation for enhanced speaker attribution ("Я" vs "Собеседники")
    """

    START_TIMEOUT = 10.0
    STOP_TIMEOUT = 20.0

    def __init__(self, output_dir: str = "storage/recordings"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

        self.is_recording = False
        self.is_paused = False
        self.base_filename = ""
        self.mic_filename = ""
        self.sys_filename = ""
        self.master_filename = ""

        self.record_thread = None
        self.start_time = 0.0
        self.elapsed_time = 0.0

        # Audio levels for VU meter (0.0 to 1.0)
        self.mic_level = 0.0
        self.system_level = 0.0
        self.master_level = 0.0

        self.chunk_size = 1024
        self._lifecycle_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._startup_complete = threading.Event()
        self._last_error = ""
        self._paused_total = 0.0
        self._pause_started = None
        self._level_updated = {"mic_level": 0.0, "system_level": 0.0}
        self._frames_lock = threading.Lock()
        self._live_tracks = []
        self._muted = {"mic": False, "sys": False}

    def set_muted(self, source, muted):
        if source not in self._muted:
            raise ValueError("Неизвестный источник звука")
        self._muted[source] = bool(muted)
        if muted:
            setattr(self, "mic_level" if source == "mic" else "system_level", 0.0)
        return dict(self._muted)

    def available_seconds(self):
        with self._frames_lock:
            return max((t["frame_count"] / t["rate"] for t in self._live_tracks), default=0.0)

    def read_live_window(self, start, end, sample_rate=16000):
        """Copy only the requested interval; no inference or resampling in callbacks."""
        import numpy as np
        from scipy.signal import resample_poly
        length = max(0, round((end - start) * sample_rate))
        mixed = np.zeros(length, dtype=np.float32)
        snapshots = []
        with self._frames_lock:
            for track in self._live_tracks:
                first, last = int(start * track["rate"]), int(end * track["rate"])
                index = max(0, bisect_right(track["offsets"], first) - 1)
                stop = bisect_right(track["offsets"], last)
                if index < len(track["frames"]):
                    snapshots.append((track["rate"], track["channels"], first - track["offsets"][index],
                                      last - first, tuple(track["frames"][index:stop])))
        for rate, channels, offset, count, frames in snapshots:
            samples = np.frombuffer(b"".join(frames), dtype='<i2').reshape(-1, channels)
            samples = samples[offset:offset + count].astype(np.float32).mean(axis=1) / 32768.0
            if len(samples) and rate != sample_rate:
                divisor = math.gcd(rate, sample_rate)
                samples = resample_poly(samples, sample_rate // divisor, rate // divisor)
            used = min(len(samples), length)
            mixed[:used] += samples[:used]
        return np.clip(mixed, -1.0, 1.0)

    @_serialized_audio_call
    def get_audio_devices(self) -> Dict[str, List[Dict]]:
        """Enumerate all input and loopback audio devices on Windows."""
        input_devices = []
        loopback_devices = []

        if pyaudio is None:
            return {"microphones": [], "loopbacks": []}

        p = pyaudio.PyAudio()
        try:
            default_mic = default_loopback = None
            try:
                default_mic = int(p.get_default_input_device_info()["index"])
            except Exception:
                pass
            try:
                default_loopback = int(p.get_default_wasapi_loopback()["index"])
            except Exception:
                pass
            for i in range(p.get_device_count()):
                dev = p.get_device_info_by_index(i)
                name = dev.get("name", f"Device {i}")
                is_loopback = dev.get("isLoopbackDevice", False)

                if is_loopback:
                    clean_name = name.replace(" [Loopback]", "").strip()
                    loopback_devices.append({
                        "index": i,
                        "name": f"Конференция / Динамики: {clean_name}",
                        "raw_name": name,
                        "channels": dev.get("maxInputChannels", 2),
                        "rate": int(dev.get("defaultSampleRate", 48000)),
                        "type": "loopback",
                        "is_default": i == default_loopback,
                    })
                elif dev.get("maxInputChannels", 0) > 0:
                    input_devices.append({
                        "index": i,
                        "name": f"Микрофон: {name}",
                        "raw_name": name,
                        "channels": dev.get("maxInputChannels", 1),
                        "rate": int(dev.get("defaultSampleRate", 44100)),
                        "type": "mic",
                        "is_default": i == default_mic,
                    })
        finally:
            p.terminate()

        return {
            "microphones": input_devices,
            "loopbacks": loopback_devices
        }

    def start_recording(self, mode: str = "mix", mic_index: Optional[int] = None, loopback_index: Optional[int] = None) -> Dict[str, str]:
        """Report success only after every requested device has opened."""
        with self._lifecycle_lock:
            if self.is_recording:
                return self._recording_paths()
            if self.record_thread and self.record_thread.is_alive():
                raise RuntimeError("Предыдущая запись еще завершается. Дождитесь сохранения аудио.")
            if mode not in ("mic", "system", "mix"):
                raise ValueError("Неизвестный режим записи. Выберите микрофон, систему или оба источника.")
            if pyaudio is None:
                raise RuntimeError("Не установлен модуль аудиозаписи PyAudioWPatch.")

            timestamp = time.strftime("%Y%m%d_%H%M%S")
            self.base_filename = os.path.join(self.output_dir, f"meeting_{timestamp}_{uuid.uuid4().hex[:8]}")
            self.mic_filename = f"{self.base_filename}_mic.wav"
            self.sys_filename = f"{self.base_filename}_sys.wav"
            self.master_filename = f"{self.base_filename}_master.wav"
            self._last_error = ""
            self._stop_event.clear()
            self._startup_complete.clear()
            self._paused_total = 0.0
            self._pause_started = None
            self._level_updated = {"mic_level": 0.0, "system_level": 0.0}
            self.is_recording = True
            self.is_paused = False
            self.start_time = time.monotonic()
            self.elapsed_time = 0.0
            with self._frames_lock:
                self._live_tracks = []
            self.record_thread = threading.Thread(
                target=self._unified_record_worker,
                args=(mode, mic_index, loopback_index),
                daemon=True,
            )
            self.record_thread.start()
            if not self._startup_complete.wait(self.START_TIMEOUT):
                self._last_error = "Аудиоустройство не ответило при запуске. Проверьте выбранные источники."
                self._stop_event.set()
                self.is_recording = False
                raise RuntimeError(self._last_error)
            if self._last_error:
                self._stop_event.set()
                self.record_thread.join(timeout=self.STOP_TIMEOUT)
                raise RuntimeError(self._last_error)
            return self._recording_paths()

    def _recording_paths(self) -> Dict[str, str]:
        return {"master": self.master_filename, "mic": self.mic_filename, "sys": self.sys_filename}

    def _elapsed_now(self):
        if not self.start_time:
            return 0.0
        end = self._pause_started if self._pause_started is not None else time.monotonic()
        return max(0.0, end - self.start_time - self._paused_total)

    def pause_recording(self):
        with self._lifecycle_lock:
            if not self.is_recording:
                raise RuntimeError("Нет активной записи.")
            if self.is_paused:
                self._paused_total += time.monotonic() - self._pause_started
                self._pause_started = None
                self.is_paused = False
            else:
                self._pause_started = time.monotonic()
                self.is_paused = True
                self.mic_level = self.system_level = 0.0

    def stop_recording(self) -> Dict[str, str]:
        with self._lifecycle_lock:
            self._stop_event.set()
            if self.record_thread and self.record_thread.is_alive():
                self.record_thread.join(timeout=self.STOP_TIMEOUT)
                if self.record_thread.is_alive():
                    raise RuntimeError("Аудиоустройство еще завершает запись. Повторите остановку через несколько секунд.")
            self.is_recording = False
            self.mic_level = self.system_level = self.master_level = 0.0
            if self._last_error:
                raise RuntimeError(self._last_error)
            self._ensure_master_mix()
            if not self.master_filename or not os.path.isfile(self.master_filename):
                raise RuntimeError("Аудиоданные не получены. Проверьте микрофон и источник системного звука и повторите запись.")
            with wave.open(self.master_filename, "rb") as recording:
                if recording.getnframes() == 0:
                    raise RuntimeError("Запись пуста: аудиоустройство не передало данные.")
            return {
                name: path if path and os.path.isfile(path) else ""
                for name, path in self._recording_paths().items()
            }

    def get_status(self) -> Dict:
        if self.is_recording:
            self.elapsed_time = self._elapsed_now()
        for level, updated in self._level_updated.items():
            if time.monotonic() - updated > 0.25:
                setattr(self, level, 0.0)

        return {
            "is_recording": self.is_recording,
            "is_paused": self.is_paused,
            "elapsed_seconds": round(self.elapsed_time, 1),
            "filename": os.path.basename(self.master_filename) if self.master_filename else "",
            "has_mic_track": os.path.exists(self.mic_filename),
            "has_sys_track": os.path.exists(self.sys_filename),
            "mic_level": round(self.mic_level, 2),
            "system_level": round(self.system_level, 2),
            "master_level": round(max(self.mic_level, self.system_level), 2),
            "error": self._last_error,
        }

    def _calc_rms(self, audio_data: bytes) -> float:
        if not audio_data:
            return 0.0
        try:
            count = len(audio_data) // 2
            if count == 0:
                return 0.0
            shorts = struct.unpack(f"<{count}h", audio_data[:count*2])
            sum_squares = sum(s * s for s in shorts)
            rms = math.sqrt(sum_squares / count)
            return min(1.0, (rms / 32768.0) * 4.0)
        except Exception:
            return 0.0

    def _select_device(self, audio, kind, selected_index):
        label = "микрофон" if kind == "mic" else "системный звук (WASAPI Loopback)"
        if selected_index is not None:
            device = audio.get_device_info_by_index(int(selected_index))
        else:
            try:
                device = (
                    audio.get_default_input_device_info()
                    if kind == "mic" else audio.get_default_wasapi_loopback()
                )
            except Exception:
                device = None
                for index in range(audio.get_device_count()):
                    candidate = audio.get_device_info_by_index(index)
                    if candidate.get("maxInputChannels", 0) > 0 and bool(candidate.get("isLoopbackDevice", False)) == (kind == "sys"):
                        device = candidate
                        break
        if not device or device.get("maxInputChannels", 0) <= 0:
            raise RuntimeError(f"Не найден источник: {label}. Выберите доступное аудиоустройство.")
        if bool(device.get("isLoopbackDevice", False)) != (kind == "sys"):
            raise RuntimeError(f"Неверный тип устройства для источника: {label}.")
        return device

    def _unified_record_worker(self, mode: str, mic_idx: Optional[int], loop_idx: Optional[int]):
        """Own the audio context and use independent callbacks for both inputs."""
        audio = None
        tracks = []

        def make_callback(track):
            def callback(data, frame_count, time_info, status_flags):
                try:
                    if self._stop_event.is_set():
                        return (None, pyaudio.paComplete)
                    if not self.is_paused and data:
                        if self._muted[track["kind"]]:
                            data = bytes(len(data))
                        with self._frames_lock:
                            track["offsets"].append(track["frame_count"])
                            track["frames"].append(data)
                            track["frame_count"] += len(data) // (2 * track["channels"])
                        setattr(self, track["level"], self._calc_rms(data))
                        self._level_updated[track["level"]] = time.monotonic()
                    else:
                        setattr(self, track["level"], 0.0)
                    return (None, pyaudio.paContinue)
                except Exception as exc:
                    self._last_error = f"Ошибка получения аудио: {exc}"
                    self._stop_event.set()
                    return (None, pyaudio.paAbort)
            return callback

        try:
            with _PORTAUDIO_LOCK:
                audio = pyaudio.PyAudio()
            requested = []
            if mode in ("mic", "mix"):
                requested.append(("mic", mic_idx, self.mic_filename, "mic_level"))
            if mode in ("system", "mix"):
                requested.append(("sys", loop_idx, self.sys_filename, "system_level"))

            for kind, selected_index, filename, level in requested:
                with _PORTAUDIO_LOCK:
                    device = self._select_device(audio, kind, selected_index)
                # Use the device's native rate, instead of forcing 16 kHz on a
                # device that may reject it. The final mix resamples as needed.
                track = {
                    "path": filename,
                    "rate": int(device["defaultSampleRate"]),
                    "channels": min(2, int(device["maxInputChannels"])),
                    "frames": [],
                    "offsets": [],
                    "frame_count": 0,
                    "kind": kind,
                    "level": level,
                    "stream": None,
                }
                tracks.append(track)
                with self._frames_lock:
                    self._live_tracks.append(track)
                try:
                    with _PORTAUDIO_LOCK:
                        track["stream"] = audio.open(
                            format=pyaudio.paInt16,
                            channels=track["channels"],
                            rate=track["rate"],
                            input=True,
                            input_device_index=int(device["index"]),
                            frames_per_buffer=self.chunk_size,
                            stream_callback=make_callback(track),
                            start=False,
                        )
                except Exception as exc:
                    raise RuntimeError(f"Не удалось открыть {device.get('name', kind)}: {exc}") from exc

            if self._stop_event.is_set():
                return
            self.start_time = time.monotonic()
            for track in tracks:
                with _PORTAUDIO_LOCK:
                    track["stream"].start_stream()
            self._startup_complete.set()
            while not self._stop_event.wait(0.05):
                for track in tracks:
                    with _PORTAUDIO_LOCK:
                        active = track["stream"].is_active()
                    if not active:
                        raise RuntimeError("Аудиоустройство остановило поток. Проверьте подключение выбранных источников.")
        except Exception as exc:
            self._last_error = str(exc)
            self._stop_event.set()
        finally:
            # Close streams before accessing their accumulated frames. stop()
            # waits for this entire block before mixing or starting inference.
            for track in tracks:
                stream = track["stream"]
                if stream is not None:
                    try:
                        with _PORTAUDIO_LOCK:
                            stream.stop_stream()
                    except Exception as exc:
                        self._last_error = self._last_error or f"Ошибка остановки аудиопотока: {exc}"
                    try:
                        with _PORTAUDIO_LOCK:
                            stream.close()
                    except Exception as exc:
                        self._last_error = self._last_error or f"Ошибка закрытия аудиопотока: {exc}"
                if track["frames"]:
                    try:
                        with wave.open(track["path"], "wb") as output:
                            output.setnchannels(track["channels"])
                            output.setsampwidth(2)
                            output.setframerate(track["rate"])
                            output.writeframes(b"".join(track["frames"]))
                    except Exception as exc:
                        self._last_error = self._last_error or f"Не удалось сохранить аудиозапись: {exc}"
            if audio is not None:
                try:
                    with _PORTAUDIO_LOCK:
                        audio.terminate()
                except Exception as exc:
                    self._last_error = self._last_error or f"Ошибка освобождения аудиоустройства: {exc}"
            self.elapsed_time = self._elapsed_now()
            self.is_recording = False
            self.is_paused = False
            self.mic_level = self.system_level = self.master_level = 0.0
            self._startup_complete.set()


    def _ensure_master_mix(self):
        """Mix the available PCM16 tracks without dropping microphone speech."""
        import shutil

        tracks = []
        for path in (self.mic_filename, self.sys_filename):
            if not path or not os.path.isfile(path):
                continue
            with wave.open(path, "rb") as source:
                if source.getnframes() == 0:
                    continue
                if source.getsampwidth() != 2:
                    raise ValueError("Master mixing requires 16-bit PCM audio")
                tracks.append((path, source.getframerate(), source.getnchannels()))

        if not tracks:
            return
        if len(tracks) == 1:
            shutil.copyfile(tracks[0][0], self.master_filename)
            return

        import numpy as np
        from scipy.signal import resample_poly

        output_rate = max(rate for _, rate, _ in tracks)
        output_channels = max(channels for _, _, channels in tracks)
        samples = []
        for path, rate, channels in tracks:
            with wave.open(path, "rb") as source:
                data = np.frombuffer(
                    source.readframes(source.getnframes()), dtype="<i2"
                ).astype(np.float32).reshape(-1, channels)
            if rate != output_rate:
                divisor = math.gcd(rate, output_rate)
                data = resample_poly(
                    data, output_rate // divisor, rate // divisor, axis=0
                )
            if channels != output_channels:
                mono = data.mean(axis=1, keepdims=True)
                data = np.repeat(mono, output_channels, axis=1)
            samples.append(data)

        mixed = np.zeros(
            (max(len(data) for data in samples), output_channels), dtype=np.float32
        )
        for data in samples:
            mixed[:len(data)] += data
        peak = float(np.max(np.abs(mixed)))
        if peak > 32767.0:
            mixed *= 32767.0 / peak
        pcm = np.clip(np.rint(mixed), -32768, 32767).astype("<i2")
        with wave.open(self.master_filename, "wb") as output:
            output.setnchannels(output_channels)
            output.setsampwidth(2)
            output.setframerate(output_rate)
            output.writeframes(pcm.tobytes())
