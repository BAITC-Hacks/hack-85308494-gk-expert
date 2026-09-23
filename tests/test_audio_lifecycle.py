"""Recorder lifecycle regression tests without opening microphones or speakers."""
from pathlib import Path
import struct
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.audio_recorder import AudioRecorder


class FakeStream:
    def __init__(self, options, emit_audio=True):
        self.options = options
        self.emit_audio = emit_audio
        self.active = False
        self.closed = False
        self.close_delay = 0

    def emit(self, value=1200):
        raw = struct.pack('<h', value) * 64 * self.options['channels']
        return self.options['stream_callback'](raw, 64, {}, 0)

    def start_stream(self):
        self.active = True
        if self.emit_audio:
            self.emit()

    def is_active(self):
        return self.active

    def stop_stream(self):
        self.active = False

    def close(self):
        time.sleep(self.close_delay)
        self.closed = True


class FakeAudio:
    def __init__(self):
        self.devices = [
            {'index': 0, 'name': 'Mic', 'maxInputChannels': 2, 'defaultSampleRate': 48000},
            {'index': 1, 'name': 'Speakers', 'maxInputChannels': 2, 'defaultSampleRate': 44100, 'isLoopbackDevice': True},
        ]
        self.streams = []
        self.terminated = False
        self.fail_index = None
        self.emit_audio = True
        self.open_delay = 0

    def get_device_count(self):
        return len(self.devices)

    def get_device_info_by_index(self, index):
        return self.devices[index]

    def get_default_input_device_info(self):
        return self.devices[0]

    def get_default_wasapi_loopback(self):
        return self.devices[1]

    def open(self, **options):
        time.sleep(self.open_delay)
        if options['input_device_index'] == self.fail_index:
            raise OSError('device unavailable')
        stream = FakeStream(options, self.emit_audio)
        self.streams.append(stream)
        return stream

    def terminate(self):
        self.terminated = True


class TestAudioLifecycle(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='audio_lifecycle_')
        self.addCleanup(temp.cleanup)
        self.audio = FakeAudio()
        self.factory = Mock(return_value=self.audio)
        module = SimpleNamespace(PyAudio=self.factory, paInt16=8, paContinue=0, paComplete=1, paAbort=2)
        self.patcher = patch('core.audio_recorder.pyaudio', module)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.recorder = AudioRecorder(temp.name)
        self.addCleanup(self.finish_worker)

    def finish_worker(self):
        self.recorder._stop_event.set()
        if self.recorder.record_thread:
            self.recorder.record_thread.join(timeout=2)

    def test_device_open_failure_is_reported_at_start(self):
        self.audio.fail_index = 1
        with self.assertRaisesRegex(RuntimeError, 'device unavailable'):
            self.recorder.start_recording('mix')
        self.assertFalse(self.recorder.is_recording)
        self.assertTrue(self.audio.terminated)
        self.assertTrue(self.audio.streams[0].closed)
        self.assertIn('device unavailable', self.recorder.get_status()['error'])

    def test_success_waits_for_open_uses_native_rates_and_index_zero(self):
        paths = self.recorder.start_recording('mix', mic_index=0, loopback_index=1)
        self.assertEqual(len(self.audio.streams), 2)
        self.assertTrue(all(stream.is_active() for stream in self.audio.streams))
        self.assertEqual([stream.options['rate'] for stream in self.audio.streams], [48000, 44100])
        self.assertEqual(self.audio.streams[0].options['input_device_index'], 0)
        self.factory.assert_called_once()
        saved = self.recorder.stop_recording()
        self.assertEqual(saved['master'], paths['master'])
        with wave.open(saved['master'], 'rb') as result:
            self.assertGreater(result.getnframes(), 0)
        self.assertTrue(all(stream.closed for stream in self.audio.streams))

    def test_stop_waits_until_files_are_finalized(self):
        self.recorder.start_recording('mic')
        self.audio.streams[0].close_delay = 0.07
        started = time.monotonic()
        result = self.recorder.stop_recording()
        self.assertGreaterEqual(time.monotonic() - started, 0.06)
        self.assertFalse(self.recorder.record_thread.is_alive())
        with wave.open(result['mic'], 'rb') as recording:
            self.assertEqual(recording.getnframes(), 64)

    def test_empty_device_data_is_not_reported_as_success(self):
        self.audio.emit_audio = False
        self.recorder.start_recording('system')
        with self.assertRaisesRegex(RuntimeError, 'Аудиоданные не получены'):
            self.recorder.stop_recording()
        self.assertFalse(self.recorder.is_recording)

    def test_requested_system_does_not_silently_fall_back_to_microphone(self):
        self.audio.devices = self.audio.devices[:1]
        with self.assertRaisesRegex(RuntimeError, 'Не найден источник'):
            self.recorder.start_recording('system')
        self.assertEqual(self.audio.streams, [])

    def test_pause_discards_audio_and_clears_levels(self):
        self.recorder.start_recording('mic')
        stream = self.audio.streams[0]
        self.recorder.pause_recording()
        stream.emit(20000)
        self.assertEqual(self.recorder.get_status()['mic_level'], 0)
        self.recorder.pause_recording()
        stream.emit(4000)
        saved = self.recorder.stop_recording()
        with wave.open(saved['mic'], 'rb') as recording:
            self.assertEqual(recording.getnframes(), 128)

    def test_stalled_level_returns_to_zero(self):
        self.recorder.start_recording('mic')
        self.assertGreater(self.recorder.get_status()['mic_level'], 0)
        self.recorder._level_updated['mic_level'] -= 1
        self.assertEqual(self.recorder.get_status()['mic_level'], 0)
        self.recorder.stop_recording()

    def test_start_timeout_cannot_overwrite_an_active_worker(self):
        self.recorder.START_TIMEOUT = 0.01
        self.audio.open_delay = 0.15
        with self.assertRaisesRegex(RuntimeError, 'не ответило'):
            self.recorder.start_recording('mic')
        with self.assertRaisesRegex(RuntimeError, 'еще завершается'):
            self.recorder.start_recording('mic')
        self.recorder.record_thread.join(timeout=1)
        self.assertFalse(self.recorder.record_thread.is_alive())

    def test_repeated_recordings_have_unique_paths(self):
        first = self.recorder.start_recording('mic')
        self.recorder.stop_recording()
        second = self.recorder.start_recording('mic')
        self.recorder.stop_recording()
        self.assertNotEqual(first['master'], second['master'])
        self.assertTrue(Path(first['master']).exists())

    def test_no_library_gives_explicit_error(self):
        with patch('core.audio_recorder.pyaudio', None):
            with self.assertRaisesRegex(RuntimeError, 'PyAudioWPatch'):
                self.recorder.start_recording('mic')

    def test_invalid_mode_does_not_open_device(self):
        with self.assertRaises(ValueError):
            self.recorder.start_recording('invalid')
        self.factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
