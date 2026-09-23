import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from core.speaker_engine import SpeakerEngine, align_speakers, voice_for_interval


class AcousticTests(unittest.TestCase):
    def test_missing_models_do_not_download_or_claim_diarization(self):
        with tempfile.TemporaryDirectory() as directory, patch('socket.socket.connect', side_effect=AssertionError('network')):
            engine = SpeakerEngine(directory)
            self.assertFalse(engine.ready())
            with self.assertRaisesRegex(RuntimeError, 'Нет локальных моделей'):
                engine.turns(np.zeros(16000))

    def test_words_split_at_voice_change_inside_one_asr_segment(self):
        segments = [{'text': 'Анна, вам слово. Спасибо.', 'start': 0, 'end': 4,
                     'words': [{'word': 'Анна,', 'start': .1, 'end': 1}, {'word': ' вам слово.', 'start': 1, 'end': 2},
                               {'word': ' Спасибо.', 'start': 2.2, 'end': 3.5}]}]
        turns = [{'speaker_id': 'a', 'start': 0, 'end': 2.1}, {'speaker_id': 'b', 'start': 2.2, 'end': 4}]
        annotated = align_speakers(segments, turns)
        self.assertEqual([s['speaker_id'] for s in annotated], ['a', 'b'])
        self.assertEqual(annotated[1]['text'], 'Спасибо.')

    def test_overlapping_voices_are_ambiguous(self):
        turns = [{'speaker_id': 'a', 'start': 0, 'end': 4}, {'speaker_id': 'b', 'start': 1, 'end': 3}]
        self.assertEqual(voice_for_interval(1.2, 2.5, turns), (None, True))

    def test_silence_does_not_inherit_previous_voice(self):
        turns = [{'speaker_id': 'a', 'start': 0, 'end': 1}]
        self.assertEqual(voice_for_interval(4, 5, turns), (None, False))
