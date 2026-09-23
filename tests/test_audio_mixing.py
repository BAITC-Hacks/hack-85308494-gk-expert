import os
import sys
import struct
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.audio_recorder import AudioRecorder


class TestAudioMixing(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="test_mix_")
        self.addCleanup(self.temp_dir.cleanup)
        self.recorder = AudioRecorder(output_dir=self.temp_dir.name)
        self.recorder.mic_filename = os.path.join(self.temp_dir.name, "mic.wav")
        self.recorder.sys_filename = os.path.join(self.temp_dir.name, "system.wav")
        self.recorder.master_filename = os.path.join(self.temp_dir.name, "master.wav")

    def write_track(self, path, sample, rate=16000, frames=1600):
        channels = len(sample) if isinstance(sample, tuple) else 1
        sample = sample if isinstance(sample, tuple) else (sample,)
        with wave.open(path, "wb") as output:
            output.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
            output.writeframes(struct.pack("<" + "h" * channels, *sample) * frames)

    def test_microphone_survives_silent_system(self):
        self.write_track(self.recorder.mic_filename, 1200)
        self.write_track(self.recorder.sys_filename, 0)
        self.recorder._ensure_master_mix()
        with wave.open(self.recorder.master_filename, "rb") as output:
            metadata = (output.getframerate(), output.getnchannels(), output.getnframes())
            data = list(struct.iter_unpack("<h", output.readframes(output.getnframes())))
        samples = [sample[0] for sample in data]
        self.assertEqual(metadata, (16000, 1, 1600))
        self.assertEqual(set(samples), {1200})

    def test_both_sources_are_mixed(self):
        self.write_track(self.recorder.mic_filename, 1000)
        self.write_track(self.recorder.sys_filename, 2000)
        self.recorder._ensure_master_mix()
        with wave.open(self.recorder.master_filename, "rb") as output:
            data = list(struct.iter_unpack("<h", output.readframes(output.getnframes())))
        samples = [sample[0] for sample in data]
        self.assertEqual(set(samples), {3000})

    def read_master(self):
        self.recorder._ensure_master_mix()
        with wave.open(self.recorder.master_filename, 'rb') as output:
            metadata = (output.getframerate(), output.getnchannels(), output.getnframes())
            samples = [sample[0] for sample in struct.iter_unpack('<h', output.readframes(output.getnframes()))]
        return metadata, samples

    def test_different_rates_and_channels_keep_microphone_tail(self):
        self.write_track(self.recorder.mic_filename, 1000, rate=16000, frames=3200)
        self.write_track(self.recorder.sys_filename, (2000, 3000), rate=48000, frames=4800)
        metadata, samples = self.read_master()
        self.assertEqual(metadata, (48000, 2, 9600))
        self.assertAlmostEqual(samples[2000], 3000, delta=5)
        self.assertAlmostEqual(samples[2001], 4000, delta=5)
        self.assertAlmostEqual(samples[14000], 1000, delta=5)
        self.assertAlmostEqual(samples[14001], 1000, delta=5)

    def test_longer_system_track_keeps_tail(self):
        self.write_track(self.recorder.mic_filename, 1000, frames=800)
        self.write_track(self.recorder.sys_filename, 2000, frames=1600)
        metadata, samples = self.read_master()
        self.assertEqual(metadata[2], 1600)
        self.assertEqual(samples[400], 3000)
        self.assertEqual(samples[1200], 2000)

    def test_overload_does_not_wrap(self):
        for value in (30000, -30000):
            with self.subTest(value=value):
                self.write_track(self.recorder.mic_filename, value)
                self.write_track(self.recorder.sys_filename, value)
                _, samples = self.read_master()
                self.assertTrue(all(-32768 <= sample <= 32767 for sample in samples))
                self.assertTrue(all(sample * value > 0 for sample in samples))
                self.assertGreaterEqual(max(abs(sample) for sample in samples), 32766)

    def test_single_track_is_byte_identical(self):
        for source in (self.recorder.mic_filename, self.recorder.sys_filename):
            with self.subTest(source=source):
                self.write_track(source, (700, -800), rate=44100, frames=4410)
                self.recorder._ensure_master_mix()
                self.assertEqual(Path(source).read_bytes(), Path(self.recorder.master_filename).read_bytes())
                Path(source).unlink()

    def test_no_tracks_creates_no_master(self):
        self.recorder._ensure_master_mix()
        self.assertFalse(Path(self.recorder.master_filename).exists())

    def test_empty_system_track_does_not_hide_microphone(self):
        self.write_track(self.recorder.mic_filename, 1200)
        self.write_track(self.recorder.sys_filename, (0, 0), rate=48000, frames=0)
        metadata, samples = self.read_master()
        self.assertEqual(metadata, (16000, 1, 1600))
        self.assertEqual(set(samples), {1200})


if __name__ == "__main__":
    unittest.main()
