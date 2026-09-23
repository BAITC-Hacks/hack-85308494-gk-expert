"""Local acoustic speaker turns and conservative word/voice alignment."""
from pathlib import Path
from copy import deepcopy
import os
import sys
import threading
import tempfile
import subprocess
import json


class SpeakerEngine:
    def __init__(self, model_dir=None, *, in_process=False):
        base = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent
        self.model_dir = Path(model_dir or os.getenv('SPEAKER_MODEL_DIR') or base / 'models' / 'speakers')
        self._lock = threading.Lock()
        self._models = {}
        self._extractor = None
        self._in_process = in_process

    def ready(self):
        return all((self.model_dir / name).is_file() for name in ('segmentation.onnx', 'embedding.onnx'))

    def turns(self, samples, num_speakers=0, progress=None):
        import numpy as np
        if not self.ready():
            raise RuntimeError('Нет локальных моделей голосов. Запустите scripts/prepare_speaker_models.py.')
        if not self._in_process:
            return self._isolated_turns(samples, num_speakers, progress)
        import sherpa_onnx as sherpa
        samples = np.ascontiguousarray(samples, dtype=np.float32)
        if len(samples) < 1600 or float(np.max(np.abs(samples))) < .0001:
            return []
        count = int(num_speakers or 0)
        if not 0 <= count <= 32:
            raise ValueError('Количество участников: от 0 (авто) до 32.')
        with self._lock:
            if count not in self._models:
                config = sherpa.OfflineSpeakerDiarizationConfig(
                    segmentation=sherpa.OfflineSpeakerSegmentationModelConfig(
                        pyannote=sherpa.OfflineSpeakerSegmentationPyannoteModelConfig(
                            model=str(self.model_dir / 'segmentation.onnx'), window_shift_ratio=.1), num_threads=2),
                    embedding=sherpa.SpeakerEmbeddingExtractorConfig(model=str(self.model_dir / 'embedding.onnx'), num_threads=2),
                    clustering=sherpa.FastClusteringConfig(num_clusters=count if count else -1, threshold=.5),
                    min_duration_on=.2, min_duration_off=.3)
                if not config.validate():
                    raise RuntimeError('Не удалось проверить конфигурацию локальных моделей голосов.')
                self._models = {count: sherpa.OfflineSpeakerDiarization(config)}
            def report(done, total):
                if progress:
                    progress(speaker_progress=round(100 * done / max(1, total)))
                return 0
            result = self._models[count].process(samples, callback=report).sort_by_start_time()
        mapping, turns = {}, []
        for item in result:
            key = int(item.speaker)
            mapping.setdefault(key, f'speaker_{len(mapping) + 1}')
            turns.append({'start': round(float(item.start), 3), 'end': round(float(item.end), 3),
                          'speaker_id': mapping[key]})
        return turns

    def _isolated_turns(self, samples, num_speakers, progress):
        """The native diarization binding holds the GIL: isolate it from capture callbacks."""
        import numpy as np
        temporary_root = Path(tempfile.gettempdir()).resolve()
        with tempfile.TemporaryDirectory(prefix='qazaq_voices_', dir=temporary_root) as directory:
            directory = Path(directory).resolve()
            if not directory.is_relative_to(temporary_root):
                raise RuntimeError('Unexpected temporary audio directory')
            input_path, output_path = directory / 'audio.npy', directory / 'turns.json'
            np.save(input_path, np.asarray(samples, dtype=np.float32), allow_pickle=False)
            command = [sys.executable]
            if not getattr(sys, 'frozen', False):
                command.append(str(Path(__file__).resolve().parent.parent / 'app.py'))
            command += ['--speaker-worker', str(input_path), str(output_path), str(self.model_dir.resolve()), str(int(num_speakers or 0))]
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                    timeout=max(120, len(samples) / 16000 * 4))
            if not output_path.is_file():
                raise RuntimeError(f'Процесс различения голосов завершился без результата (код {result.returncode}).')
            payload = json.loads(output_path.read_text(encoding='utf-8'))
            if payload.get('error'):
                raise RuntimeError(payload['error'])
            if progress:
                progress(speaker_progress=100)
            return payload['turns']


    def embedding(self, samples):
        import sherpa_onnx as sherpa
        import numpy as np
        if len(samples) < 19200:
            return None
        with self._lock:
            if self._extractor is None:
                config = sherpa.SpeakerEmbeddingExtractorConfig(model=str(self.model_dir / 'embedding.onnx'), num_threads=1)
                self._extractor = sherpa.SpeakerEmbeddingExtractor(config)
            stream = self._extractor.create_stream()
            stream.accept_waveform(16000, np.ascontiguousarray(samples, dtype=np.float32))
            stream.input_finished()
            if not self._extractor.is_ready(stream):
                return None
            vector = np.asarray(self._extractor.compute(stream), dtype=np.float32)
            return vector / max(float(np.linalg.norm(vector)), 1e-8)


def run_speaker_worker(arguments):
    """Internal entry point, including inside the frozen Windows EXE."""
    import numpy as np
    import socket
    def no_network(*args, **kwargs):
        raise RuntimeError('Сетевые обращения в процессе обработки голосов запрещены.')
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    input_path, output_path, models, count = arguments
    payload = {}
    try:
        samples = np.load(input_path, allow_pickle=False)
        payload['turns'] = SpeakerEngine(models, in_process=True).turns(samples, int(count))
    except Exception as exc:
        payload['error'] = str(exc)
    Path(output_path).write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    return 1 if 'error' in payload else 0


class StreamingSpeakers:
    """Session-only voice embeddings; no cross-meeting identity database."""
    def __init__(self, engine):
        self.engine = engine
        self.centers = {}

    def annotate(self, samples, segments, offset):
        import numpy as np
        turns = self.engine.turns(samples)
        mapping, used = {}, set()
        for key in dict.fromkeys(t['speaker_id'] for t in turns):
            clean = [t for t in turns if t['speaker_id'] == key and not any(
                other['speaker_id'] != key and min(t['end'], other['end']) > max(t['start'], other['start']) for other in turns)]
            pieces = [samples[round(t['start'] * 16000):round(t['end'] * 16000)] for t in clean]
            vector = self.engine.embedding(np.concatenate(pieces)[:16000 * 12]) if pieces else None
            if vector is None:
                mapping[key] = None
                continue
            ranked = sorted(((label, float(np.dot(center, vector))) for label, center in self.centers.items() if label not in used), key=lambda item: item[1], reverse=True)
            if ranked and ranked[0][1] >= .65 and (len(ranked) == 1 or ranked[0][1] - ranked[1][1] >= .08):
                label = ranked[0][0]
                merged = .8 * self.centers[label] + .2 * vector
                self.centers[label] = merged / np.linalg.norm(merged)
            else:
                label = f'speaker_{len(self.centers) + 1}'
                self.centers[label] = vector
            used.add(label)
            mapping[key] = label
        for turn in turns:
            turn.update(start=turn['start'] + offset, end=turn['end'] + offset, speaker_id=mapping.get(turn['speaker_id']))
        return align_speakers(segments, [t for t in turns if t['speaker_id']])


def voice_for_interval(start, end, turns):
    end = max(start + .02, end)
    scores = {}
    for turn in turns:
        overlap = max(0, min(end, turn['end']) - max(start, turn['start']))
        if overlap:
            key = turn['speaker_id']
            scores[key] = scores.get(key, 0) + overlap
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if not ranked:
        return None, False
    top = ranked[0]
    competing = ranked[1][1] if len(ranked) > 1 else 0
    ambiguous = competing >= .3 * (end - start) and top[1] - competing < .25 * (end - start)
    if top[1] < .4 * (end - start) or ambiguous:
        return None, ambiguous
    return top[0], False


def align_speakers(segments, turns):
    """Split even a single ASR sentence when its words cross a voice change."""
    output = []
    for segment in segments:
        words = segment.get('words') or []
        if not words:
            current = deepcopy(segment)
            key, overlap = voice_for_interval(float(current.get('start', 0)), float(current.get('end', current.get('start', 0) + .1)), turns)
            current.update(speaker_id=key, overlap=overlap, alignment='segment')
            output.append(current)
            continue
        group = None
        for word in words:
            start, end = float(word['start']), float(word['end'])
            key, overlap = voice_for_interval(start, end, turns)
            if group is None or group['speaker_id'] != key or group['overlap'] != overlap or start - group['end'] > 1.0:
                group = {'start': start, 'end': end, 'text': '', 'words': [], 'speaker_id': key,
                         'overlap': overlap, 'alignment': 'word'}
                output.append(group)
            group['words'].append(deepcopy(word))
            # Whisper word values contain their original leading spaces.
            group['text'] += word.get('word', '')
            group['end'] = end
    for index, segment in enumerate(output):
        segment['id'] = index
        segment['text'] = segment.get('text', '').strip()
        segment['duration'] = max(0, segment.get('end', 0) - segment.get('start', 0))
    return [segment for segment in output if segment['text']]
