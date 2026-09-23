import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np

from core.audio_pipeline import MediaStore, WindowTranscriber, ProcessingJobs, LiveTranscriber, RATE
from core.audio_recorder import AudioRecorder


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.media = MediaStore(Path(self.temporary.name) / 'playback')

    def test_windows_include_short_tail_and_global_timestamps(self):
        samples = np.zeros(RATE * 43, dtype=np.float32)
        calls = []
        engine = SimpleNamespace(DEFAULT_PROMPT='контекст')
        def transcribe(path, **kwargs):
            self.assertTrue(Path(path).is_file())
            calls.append(path)
            return {'segments': [{'text': 'Әлия отчёт', 'start': 1, 'end': 2, 'words': [{'start': 1, 'end': 2}]}]}
        engine.transcribe = transcribe
        updates = []
        with patch('faster_whisper.audio.decode_audio', return_value=samples):
            result = WindowTranscriber(engine, self.media).process('input.wav', {}, lambda **fields: updates.append(fields))
        self.assertEqual(len(calls), 3)
        self.assertEqual([s['start'] for s in result['segments']], [1, 21, 41])
        self.assertEqual(result['segments'][-1]['words'][0]['end'], 42)
        intervals = [u['current_chunk'] for u in updates if 'current_chunk' in u]
        self.assertEqual([(x['start'], x['end']) for x in intervals], [(0, 20), (20, 40), (40, 43)])
        self.assertEqual(updates[-1]['completed_seconds'], 43)

    def test_live_completes_while_file_worker_is_blocked(self):
        file_started, release = threading.Event(), threading.Event()
        def process(*args):
            file_started.set()
            release.wait(5)
            return {'meeting': {'id': 'complete'}}
        jobs = ProcessingJobs(process)
        job = jobs.submit('file.wav', {})
        recorder = SimpleNamespace(available_seconds=lambda: 8, read_live_window=lambda *_: np.zeros(RATE * 8))
        engine = SimpleNamespace(transcribe=lambda *a, **kw: {'segments': [{'start': 0, 'end': 1, 'text': 'Живая речь'}]})
        live = LiveTranscriber(recorder, engine, self.media, {})
        try:
            self.assertTrue(file_started.wait(2))
            live.start()
            until = time.monotonic() + 3
            while not live.status()['segments'] and time.monotonic() < until:
                time.sleep(.02)
            self.assertEqual(live.status()['segments'][0]['text'], 'Живая речь')
            self.assertEqual(jobs.get(job['id'])['state'], 'running')
            self.assertTrue(live.status()['current_chunk']['audio_url'].startswith('/media/'))
        finally:
            live.stop()
            live._thread.join(2)
            release.set()
            jobs._queue.join()
            jobs._queue.put(None)
            jobs._worker.join(2)

    def test_live_window_uses_actual_callback_offsets(self):
        recorder = AudioRecorder(self.temporary.name)
        values = np.arange(80, dtype='<i2')
        frames = [values[:13].tobytes(), values[13:45].tobytes(), values[45:].tobytes()]
        recorder._live_tracks = [{'frames': frames, 'offsets': [0, 13, 45], 'frame_count': 80, 'rate': 16, 'channels': 1}]
        self.assertEqual(recorder.available_seconds(), 5)
        np.testing.assert_allclose(recorder.read_live_window(1, 4, sample_rate=16), values[16:64] / 32768)

    def test_media_rejects_paths_outside_audio_directories(self):
        outside = Path(self.temporary.name) / 'secret.txt'
        outside.write_text('private')
        with self.assertRaises(ValueError):
            self.media.register(outside)
        self.assertIsNone(self.media.resolve('../../secret.txt'))

    def test_fragment_contains_only_its_words_and_relative_timestamps(self):
        _, url = self.media.save(np.zeros(RATE * 8), 'live')
        self.media.publish_fragment(url, 8, 16, source='Live')
        self.assertFalse(self.media.fragment(url.split('/')[-1])['fragment_ready'])
        segments = [{'start': 7, 'end': 18, 'text': 'старый текущий лишний', 'speaker_id': 'a',
                     'words': [{'word': 'старый', 'start': 7, 'end': 7.8},
                               {'word': ' текущий', 'start': 9, 'end': 10},
                               {'word': ' лишний', 'start': 17, 'end': 18}]}]
        self.media.publish_fragment(url, 8, 16, segments, source='Live', diarization='performed')
        fragment = self.media.fragment(url.split('/')[-1])
        self.assertTrue(fragment['fragment_ready'])
        self.assertEqual(fragment['duration'], 8)
        self.assertEqual(fragment['segments'][0]['text'], 'текущий')
        self.assertEqual(fragment['segments'][0]['words'][0]['start'], 1)
        self.assertEqual(fragment['audio_url'], url)
