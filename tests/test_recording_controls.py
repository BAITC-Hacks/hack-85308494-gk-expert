"""Public bridge contracts, using synthetic devices and no model or desktop startup."""
import ast
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import numpy as np
import test_audio_lifecycle as audio_fixture
from core.audio_pipeline import MediaStore
from core.meeting_manager import MeetingManager
from core.nlp_extractor import NLPExtractor


def bridge_class():
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'app.py').read_text(encoding='utf-8-sig'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ProtocolApiBridge')
    namespace = {'Dict': dict, 'Optional': __import__('typing').Optional, 'LiveTranscriber': Mock()}
    exec(compile(ast.Module(body=[cls], type_ignores=[]), 'app.py', 'exec'), namespace)
    return namespace['ProtocolApiBridge']


class BridgeMuteTests(unittest.TestCase):
    def setUp(self):
        # Reuse only the device fixture, not its inherited tests.
        fixture = audio_fixture.TestAudioLifecycle()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.recorder, self.audio = fixture.recorder, fixture.audio
        cls = bridge_class()
        self.bridge = cls.__new__(cls)
        self.bridge.recorder = self.recorder
        self.bridge._record_control = threading.Lock()
        self.bridge.live_stt = self.bridge.media = self.bridge.live_speakers = None
        self.bridge.settings = {}

    def test_api_default_records_only_system_sound(self):
        self.bridge.start_recording()
        self.assertTrue(self.recorder.get_status()['muted']['mic'])
        self.assertEqual([s.options['input_device_index'] for s in self.audio.streams], [1])
        with self.assertRaisesRegex(ValueError, 'не был включён'):
            self.bridge.set_source_muted({'source': 'mic', 'muted': False})
        self.recorder.stop_recording()

    def test_api_applies_mute_before_first_mixed_callback(self):
        self.bridge.start_recording({'mode': 'mix', 'mic_muted': True})
        with self.recorder._frames_lock:
            mic = next(t for t in self.recorder._live_tracks if t['kind'] == 'mic')
            self.assertTrue(mic['frames'])
            self.assertTrue(all(not any(block) for block in mic['frames']))
        self.bridge.set_source_muted({'source': 'mic', 'muted': False})
        self.assertFalse(self.recorder.get_status()['muted']['mic'])
        self.recorder.stop_recording()


class FullProtocolTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        cls = bridge_class()
        self.bridge = cls.__new__(cls)
        self.bridge.media = MediaStore(Path(temporary.name) / 'playback')
        self.bridge.manager = MeetingManager(str(Path(temporary.name) / 'meetings'))
        self.bridge.nlp = NLPExtractor()
        self.jobs = {'one': {'state': 'running'}, 'two': {'state': 'done', 'meeting_id': 'unrelated'}}
        self.bridge.jobs = SimpleNamespace(get=lambda key: self.jobs[key])

    def test_fragment_never_extracts_tasks_or_uses_an_unrelated_full_file(self):
        _, url = self.bridge.media.save(np.zeros(16000))
        self.bridge.media.publish_fragment(url, 0, 1, [{'start': 0, 'end': 1, 'text': 'Айдар, подготовьте отчёт завтра.'}], job_id='one')
        identifier = 'fragment_' + url.split('/')[-1]
        result = self.bridge.get_meeting({'id': identifier})
        self.assertEqual(result['tasks'], [])
        self.assertEqual(result['key_moments'], [])
        self.assertEqual(result['full_job_id'], 'one')
        self.assertIsNone(result['full_meeting_id'])
        with self.assertRaisesRegex(ValueError, 'ещё готовится'):
            self.bridge._full_export_meeting({'meeting_id': identifier})
        parent = self.bridge.nlp.process_transcript({'text': 'Айдар, подготовьте отчёт завтра.'})
        parent['id'] = 'correct_full'
        self.bridge.manager.save_meeting(parent)
        self.jobs['one'] = {'state': 'done', 'meeting_id': parent['id']}
        result = self.bridge.get_meeting({'id': identifier})
        self.assertEqual(result['full_meeting_id'], parent['id'])
        self.assertEqual(len(self.bridge.get_meeting({'id': parent['id']})['tasks']), 1)
        self.assertEqual(self.bridge._full_export_meeting({'meeting_id': identifier})['id'], parent['id'])

    def test_late_live_result_cannot_replace_final_full_recording_speaker_alignment(self):
        _, url = self.bridge.media.save(np.zeros(16000))
        self.bridge.media.publish_fragment(url, 0, 1, session_id='live1')
        self.bridge.media.bind_session('live1', 'one')
        final = {'segments': [{'start': 0, 'end': 1, 'text': 'Итоговый текст', 'speaker_id': 'final_voice'}], 'diarization': 'performed'}
        self.bridge.media.complete_job('one', final)
        self.bridge.media.publish_fragment(url, 0, 1, [{'start': 0, 'end': 1, 'text': 'Черновик', 'speaker_id': 'wrong_voice'}])
        result = self.bridge.media.fragment(url.split('/')[-1])
        self.assertEqual(result['segments'][0]['speaker_id'], 'final_voice')
        self.assertEqual(result['segments'][0]['text'], 'Итоговый текст')
