"""Independent live and file workers with explicit, playable audio windows."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import queue
import threading
import time
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from core.speaker_engine import align_speakers, StreamingSpeakers
from core.speaker_context import resolve_names, roster_names

RATE = 16000


def now_iso():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def write_wav(path, samples):
    import numpy as np
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
        stream.writeframes(np.clip(samples * 32767, -32768, 32767).astype('<i2').tobytes())


class MediaStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._paths = {}
        self._fragments = {}
        self._lock = threading.Lock()

    def register(self, path):
        path = Path(path).resolve()
        if not any(path.is_relative_to(folder) for folder in (self.root, self.root.parent / 'recordings')) or not path.is_file():
            raise ValueError('Аудиофайл недоступен')
        with self._lock:
            token = uuid.uuid4().hex
            self._paths[token] = path
        return '/media/' + token

    def resolve(self, token):
        with self._lock:
            return self._paths.get(token)

    def save(self, samples, prefix='chunk'):
        path = self.root / (prefix + '_' + uuid.uuid4().hex + '.wav')
        write_wav(path, samples)
        return path, self.register(path)

    def publish_fragment(self, url, start, end, segments=None, source='Фрагмент', diarization='not_performed'):
        token = url.removeprefix('/media/')
        with self._lock:
            if token not in self._paths:
                raise ValueError('Неизвестный аудиофрагмент')
            selected = []
            for segment in segments or []:
                if segment.get('end', segment.get('start', 0)) <= start or segment.get('start', 0) >= end:
                    continue
                item = deepcopy(segment)
                # Word-level clipping prevents neighbouring phrases from leaking into a fragment.
                if item.get('words'):
                    item['words'] = [w for w in item['words'] if start <= (w['start'] + w['end']) / 2 < end]
                    if not item['words']:
                        continue
                    item['text'] = ''.join(w.get('word', '') for w in item['words']).strip()
                    item['start'] = item['words'][0]['start']
                    item['end'] = item['words'][-1]['end']
                item['start'] = max(0, item.get('start', start) - start)
                item['end'] = min(end - start, item.get('end', end) - start)
                for word in item.get('words', []):
                    word['start'] = max(0, word['start'] - start)
                    word['end'] = min(end - start, word['end'] - start)
                selected.append(item)
            self._fragments[token] = {'segments': selected, 'duration': round(end - start, 3),
                'audio_url': url, 'source_start': start, 'source_end': end, 'source_label': source,
                'fragment_ready': segments is not None, 'diarization': diarization,
                'fragment_version': self._fragments.get(token, {}).get('fragment_version', 0) + 1}

    def fragment(self, token):
        with self._lock:
            return deepcopy(self._fragments.get(token))


def shifted_segments(result, offset, existing):
    segments = []
    for original in result.get('segments', []):
        segment = deepcopy(original)
        segment['id'] = existing + len(segments)
        for field in ('start', 'end'):
            segment[field] = round(float(segment.get(field, 0)) + offset, 2)
        for word in segment.get('words', []):
            for field in ('start', 'end'):
                word[field] = round(float(word.get(field, 0)) + offset, 2)
        segments.append(segment)
    return segments


class WindowTranscriber:
    """Process each advertised interval exactly once, including the short tail."""
    def __init__(self, engine, media, seconds=20, speakers=None):
        self.engine, self.media, self.seconds = engine, media, seconds
        self.speakers = speakers

    def process(self, filepath, settings, progress):
        from faster_whisper.audio import decode_audio
        progress(stage='Подготовка аудио', state='running')
        samples = decode_audio(str(filepath), sampling_rate=RATE)
        if not len(samples):
            raise ValueError('Аудиофайл не содержит звука')
        full_path, full_url = self.media.save(samples, 'recording')
        duration = len(samples) / RATE
        progress(total_seconds=duration, audio_url=full_url)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='speaker-diarization') if self.speakers else None
        voices = executor.submit(self.speakers.turns, samples, settings.get('speaker_count', 0), progress) if executor else None
        segments = []
        fragments = []
        detected_language = 'auto'
        size = int(self.seconds * RATE)
        for index in range(0, len(samples), size):
            end = min(index + size, len(samples))
            path, url = self.media.save(samples[index:end])
            current = {'start': round(index / RATE, 2), 'end': round(end / RATE, 2), 'audio_url': url}
            fragments.append(current)
            self.media.publish_fragment(url, current['start'], current['end'], source=settings.get('source_label', Path(filepath).name))
            progress(stage='Распознавание фрагмента', current_chunk=current)
            context = settings.get('prompt') or self.engine.DEFAULT_PROMPT
            if settings.get('participants'):
                context += ' Участники: ' + ', '.join(roster_names(settings['participants'])) + '.'
            if segments:
                context += ' ' + ' '.join(s['text'] for s in segments)[-250:]
            result = self.engine.transcribe(str(path), custom_prompt=context, language=settings.get('language', 'auto'))
            segments.extend(shifted_segments(result, index / RATE, len(segments)))
            self.media.publish_fragment(url, current['start'], current['end'], segments, source=settings.get('source_label', Path(filepath).name))
            detected_language = result.get('language', detected_language)
            progress(completed_seconds=end / RATE, segments=segments, stage='Фрагмент распознан')
        speaker_data = {'diarization': 'not_performed', 'speakers': []}
        if voices:
            progress(stage='Сопоставление голосов, имён и реплик')
            try:
                turns = voices.result()
                segments, profiles = resolve_names(align_speakers(segments, turns), settings.get('participants'))
                speaker_data = {'diarization': 'performed', 'speakers': profiles, 'speaker_turns': turns,
                                'speaker_engine': 'sherpa-onnx / pyannote-3.0 + TitaNet-small'}
            except Exception as exc:
                speaker_data['speaker_error'] = str(exc)
            finally:
                executor.shutdown(wait=True)
        progress(segments=segments, speakers=speaker_data['speakers'], completed_seconds=duration)
        for fragment in fragments:
            self.media.publish_fragment(fragment['audio_url'], fragment['start'], fragment['end'], segments,
                source=settings.get('source_label', Path(filepath).name), diarization=speaker_data['diarization'])
        return {'text': ' '.join(s['text'] for s in segments), 'segments': segments,
                'duration': duration, 'language': detected_language, 'filename': Path(filepath).name,
                'processing_location': 'local', 'engine': 'faster-whisper-local', **speaker_data,
                'playback_file': full_path.name, 'audio_url': full_url}


class ProcessingJobs:
    """One ordered file worker, independent from recording and live recognition."""
    def __init__(self, processor):
        self.processor = processor
        self._lock = threading.RLock()
        self._jobs = {}
        self._queue = queue.Queue()
        self._worker = threading.Thread(target=self._run, name='file-transcription', daemon=True)
        self._worker.start()

    def submit(self, filepath, settings, tracks=None, title=None):
        identifier = uuid.uuid4().hex
        job = {'id': identifier, 'name': title or Path(filepath).name, 'state': 'queued', 'stage': 'В очереди',
               'created_at': now_iso(), 'completed_seconds': 0, 'total_seconds': 0, 'segments': [], 'current_chunk': None}
        with self._lock:
            self._jobs[identifier] = job
        self._queue.put((identifier, filepath, dict(deepcopy(settings), source_label=job['name']), tracks))
        return self.get(identifier)

    def get(self, identifier):
        with self._lock:
            if identifier not in self._jobs:
                raise ValueError('Задача обработки не найдена')
            return deepcopy(self._jobs[identifier])

    def list(self):
        with self._lock:
            return [{key: deepcopy(value) for key, value in job.items() if key not in ('segments', 'meeting')}
                    for job in self._jobs.values()]

    def _run(self):
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            identifier, filepath, settings, tracks = item
            def progress(**fields):
                with self._lock:
                    self._jobs[identifier].update(deepcopy(fields))
            try:
                progress(state='running', stage='Подготовка', started_at=now_iso())
                result = self.processor(filepath, settings, tracks, progress)
                progress(state='done', stage='Сохранено', meeting_id=result['meeting']['id'], saved_at=now_iso())
            except Exception as exc:
                progress(state='error', stage='Ошибка', error=str(exc))
            finally:
                self._queue.task_done()


class LiveTranscriber:
    def __init__(self, recorder, engine, media, settings, seconds=8, speakers=None):
        self.recorder, self.engine, self.media = recorder, engine, media
        self.settings, self.seconds = deepcopy(settings), seconds
        self.voices = StreamingSpeakers(speakers) if speakers else None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._state = {'state': 'listening', 'segments': [], 'completed_seconds': 0, 'current_chunk': None,
                       'stage': 'Ожидание первого фрагмента', 'session_id': uuid.uuid4().hex}
        self._thread = threading.Thread(target=self._run, name='live-transcription', daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def status(self):
        with self._lock:
            state = deepcopy(self._state)
        state['available_seconds'] = self.recorder.available_seconds()
        state['lag_seconds'] = max(0, round(state['available_seconds'] - state['completed_seconds'], 1))
        return state

    def _update(self, **fields):
        with self._lock:
            self._state.update(fields)

    def _run(self):
        cursor, segments, fragments = 0.0, [], []
        try:
            while not self._stop.wait(0.15):
                available = self.recorder.available_seconds()
                if available - cursor < self.seconds:
                    continue
                end = cursor + self.seconds
                samples = self.recorder.read_live_window(cursor, end)
                path, url = self.media.save(samples, 'live')
                fragments.append({'audio_url': url, 'start': cursor, 'end': end})
                self.media.publish_fragment(url, cursor, end, source='Live-запись')
                self._update(state='transcribing', stage='Распознавание live-фрагмента',
                             current_chunk={'start': cursor, 'end': end, 'audio_url': url})
                context = self.settings.get('prompt') or getattr(self.engine, 'DEFAULT_PROMPT', None)
                if self.settings.get('participants'):
                    context = (context or '') + ' Участники: ' + ', '.join(roster_names(self.settings['participants']))
                result = self.engine.transcribe(str(path), custom_prompt=context,
                                                language=self.settings.get('language', 'auto'))
                incoming = shifted_segments(result, cursor, len(segments))
                if self.voices:
                    try:
                        incoming = self.voices.annotate(samples, incoming, cursor)
                    except Exception as exc:
                        self._update(speaker_error=str(exc))
                segments.extend(incoming)
                segments, profiles = resolve_names(segments, self.settings.get('participants'))
                for fragment in fragments:
                    self.media.publish_fragment(fragment['audio_url'], fragment['start'], fragment['end'], segments,
                        source='Live-запись', diarization='performed' if self.voices and not self._state.get('speaker_error') else 'not_performed')
                cursor = end
                self._update(state='listening', segments=deepcopy(segments), speakers=profiles, completed_seconds=cursor,
                             stage='Live-текст обновлён')
        except Exception as exc:
            self._update(state='error', error=str(exc), stage='Live недоступен; исходная запись продолжается')
        finally:
            if self._state['state'] != 'error':
                self._update(state='stopped', stage='Запись сохранена; итоговый протокол обрабатывается отдельно')
