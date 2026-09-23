import os
import sys
import time
import json
import re
import uuid
import email
import email.parser
import email.policy
import threading
from typing import Dict, Optional

# Set UTF-8 encoding for Windows terminal or mock stream if GUI windowed
class NullWriter:
    def write(self, *args, **kwargs): pass
    def flush(self, *args, **kwargs): pass
    def isatty(self): return False

if sys.stdout is None:
    sys.stdout = NullWriter()
else:
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

if sys.stderr is None:
    sys.stderr = NullWriter()
else:
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from http.server import HTTPServer, ThreadingHTTPServer, SimpleHTTPRequestHandler
import urllib.parse
from dotenv import load_dotenv

IS_FROZEN = getattr(sys, 'frozen', False)
if IS_FROZEN:
    RESOURCE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    DATA_DIR = os.path.dirname(sys.executable)
else:
    RESOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = RESOURCE_DIR

sys.path.insert(0, RESOURCE_DIR)
sys.path.insert(0, DATA_DIR)

# Keep packaged resources read-only; each Windows user owns their data.
APP_HOME = DATA_DIR
if IS_FROZEN:
    DATA_DIR = os.path.join(os.getenv("LOCALAPPDATA", os.path.expanduser("~")), "QazaqProtocol")
DATA_DIR = os.path.abspath(os.getenv("QAZAQ_DATA_DIR", DATA_DIR))
os.makedirs(DATA_DIR, exist_ok=True)
ENV_PATH = os.path.join(DATA_DIR, ".env")
load_dotenv(ENV_PATH)
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

from core.audio_recorder import AudioRecorder
from core.stt_engine import SpeechToTextEngine
from core.nlp_extractor import NLPExtractor
from core.export_service import ExportService
from core.sed_integrator import SEDIntegrator
from core.meeting_manager import MeetingManager
from core.company_group_manager import CompanyGroupManager
from core.audio_pipeline import MediaStore, WindowTranscriber, ProcessingJobs, LiveTranscriber, now_iso
from core.save_dialog import ExportSaver

try:
    import webview
except ImportError:
    webview = None


class ProtocolApiBridge:
    """
    Exposes desktop methods to JavaScript in the GUI window.
    """

    def __init__(self):
        self.settings = {"company": os.getenv("DEFAULT_COMPANY", "Организация"), "prompt": "", "language": "auto"}
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as stream:
                self.settings.update(json.load(stream))
        except (OSError, ValueError):
            pass
        os.environ["DEFAULT_COMPANY"] = self.settings["company"]
        self._processing_lock = threading.Lock()
        self._processing = {"busy": False, "stage": "Готов", "started_at": 0}

        self.recorder = AudioRecorder(output_dir=os.path.join(DATA_DIR, "storage", "recordings"))
        self.stt = SpeechToTextEngine()
        self.live_stt = SpeechToTextEngine(live=True)
        self.media = MediaStore(os.path.join(DATA_DIR, "storage", "playback"))
        self.windows = WindowTranscriber(self.stt, self.media)
        self.live = None
        self.nlp = NLPExtractor()
        self.exporter = ExportService(output_dir=os.path.join(DATA_DIR, "storage", "protocols"))
        self.sed = SEDIntegrator(export_dir=os.path.join(DATA_DIR, "storage", "sed_exports"))
        self.manager = MeetingManager(data_dir=os.path.join(DATA_DIR, "storage", "meetings"))
        self.comp_mgr = CompanyGroupManager(data_dir=os.path.join(DATA_DIR, "storage"))
        self.jobs = ProcessingJobs(self._process_audio)
        self._record_control = threading.Lock()
        self._window = None
        self._export_saver = ExportSaver()
        self._downloads = {}

    def get_audio_devices(self, params=None):
        return self.recorder.get_audio_devices()

    def start_recording(self, params=None):
        params = params or {}
        mode = params.get("mode", "mix")
        mic_idx = params.get("mic_index")
        loop_idx = params.get("loopback_index")
        with self._record_control:
            if self.recorder.is_recording:
                raise RuntimeError("Запись уже идёт")
            result = self.recorder.start_recording(mode=mode, mic_index=mic_idx, loopback_index=loop_idx)
            self.recorder.set_muted('mic', params.get('mic_muted', False))
            self.recorder.set_muted('sys', params.get('sys_muted', False))
            self.live = LiveTranscriber(self.recorder, self.live_stt, self.media, self.settings)
            self.live.start()
            return result

    def set_source_muted(self, params):
        if self.recorder.is_recording and not params.get('muted'):
            with self.recorder._frames_lock:
                active = [track['kind'] for track in self.recorder._live_tracks]
            if params.get('source') not in active:
                raise ValueError('Этот источник не был включён при старте. Остановите запись и включите его перед новой записью.')
        return self.recorder.set_muted(params.get('source'), params.get('muted'))

    def get_live_status(self, params=None):
        return self.live.status() if self.live else {'state': 'idle', 'segments': [], 'current_chunk': None}

    def stop_recording_job(self, params=None):
        with self._record_control:
            if self.live:
                self.live.stop()
            tracks = self.recorder.stop_recording()
            settings = dict(self.settings, recording_saved_at=now_iso())
            job = self.jobs.submit(tracks['master'], settings, tracks,
                                   'Запись ' + time.strftime('%d.%m.%Y %H:%M:%S'))
            return {'job': job}

    def submit_existing_audio(self, filepath, title=None):
        settings = dict(self.settings, recording_saved_at=now_iso())
        return {'job': self.jobs.submit(filepath, settings, title=title)}

    def get_jobs(self, params=None):
        return self.jobs.list()

    def get_job(self, params):
        return self.jobs.get(params.get('id'))

    def pause_recording(self, params=None):
        self.recorder.pause_recording()
        return {"status": "ok"}

    def get_recording_status(self, params=None):
        return self.recorder.get_status()

    def stop_and_process(self, params=None):
        params = params or {}
        model = params.get("model", "offline")

        # 1. Stop recording (returns master, mic, sys files)
        rec_files = self.recorder.stop_recording()
        master_file = rec_files.get("master")

        if not master_file or not os.path.exists(master_file):
            # Fallback to mic or sys
            master_file = rec_files.get("sys") or rec_files.get("mic")

        if not master_file or not os.path.exists(master_file):
            raise FileNotFoundError("Аудиофайл записи не найден")

        if self.live:
            self.live.stop()
        return self._process_audio(master_file, tracks=rec_files)

    def process_existing_audio(self, filepath: str, model: str = "offline"):
        if isinstance(filepath, dict):
            model = filepath.get("model", model)
            filepath = filepath.get("filepath", "")
        return self._process_audio(filepath)

    def _process_audio(self, filepath, settings=None, tracks=None, progress=None):
        settings = dict(settings or self.settings)
        progress = progress or (lambda **fields: None)
        if not os.path.isfile(filepath):
            raise FileNotFoundError("Аудиофайл не найден. Запишите звук или загрузите файл.")
        if not self._processing_lock.acquire(blocking=False):
            raise RuntimeError("Уже обрабатывается другое совещание. Дождитесь завершения.")
        self._processing = {"busy": True, "stage": "Распознавание речи на этом компьютере", "started_at": time.time()}
        try:
            transcript = self.windows.process(filepath, settings, progress)
            self._processing["stage"] = "Выделение поручений и подготовка протокола"
            progress(stage=self._processing["stage"])
            meeting = self.nlp.process_transcript(transcript)
            meeting["id"] = "meeting_" + uuid.uuid4().hex[:16]
            meeting["date"] = time.strftime("%d.%m.%Y")
            meeting["audio_filename"] = os.path.basename(filepath)
            meeting["playback_file"] = transcript['playback_file']
            meeting["recording_saved_at"] = settings.get('recording_saved_at', now_iso())
            meeting["company"] = settings.get('company', 'Организация')
            if tracks:
                meeting["audio_mic_track"] = tracks.get("mic", "")
                meeting["audio_sys_track"] = tracks.get("sys", "")
            self._processing["stage"] = "Сохранение Word и PDF"
            progress(stage=self._processing['stage'])
            self.exporter.export_to_docx(meeting, f"Протокол_{meeting['id']}.docx")
            self.exporter.export_to_pdf(meeting, f"Протокол_{meeting['id']}.pdf")
            self.exporter.export_to_txt(meeting, f"Стенограмма_{meeting['id']}.txt")
            self.sed.export_to_sed_json(meeting, f"СЭД_{meeting['id']}.json")
            self.manager.save_meeting(meeting)
            return {"meeting": meeting}
        finally:
            self._processing = {"busy": False, "stage": "Готов", "started_at": 0}
            self._processing_lock.release()

    def get_processing_status(self, params=None):
        return dict(self._processing)

    def get_settings(self, params=None):
        return dict(self.settings, data_dir=DATA_DIR, processing_location="local",
                    model_ready=all((self.stt.model_dir / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")))

    def get_meeting(self, params):
        meeting_id = params.get("id")
        meeting = self.manager.get_meeting(meeting_id)
        if meeting and meeting.get('playback_file'):
            try:
                meeting['audio_url'] = self.media.register(self.media.root / meeting['playback_file'])
            except ValueError:
                meeting['audio_url'] = None
        elif meeting and meeting.get('audio_filename'):
            from pathlib import Path
            previous_audio = Path(DATA_DIR) / 'storage' / 'recordings' / Path(meeting['audio_filename']).name
            if previous_audio.is_file():
                meeting['audio_url'] = self.media.register(previous_audio)
        return meeting

    def list_meetings(self, params=None):
        return self.manager.list_meetings()

    def update_task_status(self, params):
        meeting_id = params.get("meeting_id")
        task_id = params.get("task_id")
        status = params.get("status")
        success = self.manager.update_task_status(meeting_id, task_id, status)
        return {"success": success}

    # Companies & Groups
    def get_companies(self, params=None):
        return self.comp_mgr.get_companies()

    def get_groups(self, params=None):
        comp_id = params.get("company_id") if params else None
        return self.comp_mgr.get_groups(comp_id)

    def add_company(self, params):
        name = params.get("name")
        comp_type = params.get("type", "Дочерняя компания")
        code = params.get("code", "")
        return self.comp_mgr.add_company(name, comp_type, code)

    def add_group(self, params):
        name = params.get("name")
        company_id = params.get("company_id")
        return self.comp_mgr.add_group(name, company_id)

    # Notes & Memos
    def add_note(self, params):
        meeting_id = params.get("meeting_id")
        text = params.get("text")
        timestamp_str = params.get("timestamp", "00:00")
        category = params.get("category", "Заметка")
        author = params.get("author", "Секретарь")
        return self.comp_mgr.add_note(meeting_id, text, timestamp_str, category, author)

    def get_notes(self, params):
        meeting_id = params.get("meeting_id")
        return self.comp_mgr.get_notes(meeting_id)

    def export_docx(self, params):
        meeting_id = params.get("meeting_id")
        meeting = self.manager.get_meeting(meeting_id)
        if not meeting:
            return {"error": "Meeting not found"}
        path = self.exporter.export_to_docx(meeting, f"Протокол_{meeting_id}.docx")
        return {"filepath": os.path.abspath(path)}

    def export_pdf(self, params):
        meeting_id = params.get("meeting_id")
        meeting = self.manager.get_meeting(meeting_id)
        if not meeting:
            return {"error": "Meeting not found"}
        path = self.exporter.export_to_pdf(meeting, f"Протокол_{meeting_id}.pdf")
        return {"filepath": os.path.abspath(path)}

    def export_sed(self, params):
        meeting_id = params.get("meeting_id")
        meeting = self.manager.get_meeting(meeting_id)
        if not meeting:
            return {"error": "Meeting not found"}
        path = self.sed.export_to_sed_json(meeting, f"СЭД_{meeting_id}.json")
        return {"filepath": os.path.abspath(path)}

    def export_transcript(self, params):
        meeting = self.manager.get_meeting(params.get("meeting_id"))
        if not meeting:
            raise ValueError("Сначала выберите совещание.")
        path = self.exporter.export_to_txt(meeting, f"Стенограмма_{meeting['id']}.txt")
        return {"filepath": os.path.abspath(path)}

    def save_export(self, params):
        methods = {'docx': self.export_docx, 'pdf': self.export_pdf,
                   'txt': self.export_transcript, 'json': self.export_sed}
        method = methods.get(params.get('format'))
        if method is None:
            raise ValueError('Неизвестный формат документа')
        exported = method(params)
        if exported.get('error'):
            raise ValueError(exported['error'])
        path = exported['filepath']
        if self._window is not None:
            return self._export_saver.save(self._window, path)
        token = uuid.uuid4().hex
        self._downloads[token] = path
        return {'download_url': '/download/' + token, 'filename': os.path.basename(path)}

    def save_settings(self, params):
        company = str(params.get("company", self.settings["company"])).strip()[:200] or "Организация"
        prompt = str(params.get("prompt", self.settings.get("prompt", ""))).strip()[:4000]
        language = params.get("language", self.settings.get("language", "auto"))
        if language not in ("auto", "ru", "kk"):
            raise ValueError("Неизвестный язык распознавания")
        settings = {"company": company, "prompt": prompt, "language": language}
        temporary = SETTINGS_PATH + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(settings, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, SETTINGS_PATH)
        self.settings = settings
        os.environ["DEFAULT_COMPANY"] = company
        return {"success": True}


class AppHttpServer(SimpleHTTPRequestHandler):
    """Local HTTP Server for assets, API calls, and file uploads."""
    bridge = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.join(RESOURCE_DIR, "ui"), **kwargs)

    def do_GET(self):
        if self.path.startswith('/media/'):
            self._serve_media()
        elif self.path.startswith('/download/'):
            token = urllib.parse.urlsplit(self.path).path.removeprefix('/download/')
            path = self.bridge._downloads.get(token)
            if not path or not os.path.isfile(path):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', self.guess_type(path))
            self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + urllib.parse.quote(os.path.basename(path)))
            self.send_header('Content-Length', str(os.path.getsize(path)))
            self.end_headers()
            with open(path, 'rb') as stream:
                self.copyfile(stream, self.wfile)
        else:
            super().do_GET()

    def do_HEAD(self):
        if self.path.startswith('/media/'):
            self._serve_media(head=True)
        else:
            super().do_HEAD()

    def _serve_media(self, head=False):
        token = urllib.parse.urlsplit(self.path).path.removeprefix('/media/')
        path = self.bridge.media.resolve(token)
        if not path or not path.is_file():
            self.send_error(404, 'Audio not found')
            return
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        requested = self.headers.get('Range')
        if requested:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested.strip())
            if not match or not any(match.groups()):
                self.send_error(416)
                return
            left, right = match.groups()
            if left:
                start = int(left)
                end = min(size - 1, int(right)) if right else size - 1
            else:
                start = max(0, size - int(right))
            if start >= size or end < start:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        self.send_header('Content-Type', self.guess_type(str(path)))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', 'private, no-store')
        self.send_header('Content-Length', str(end - start + 1))
        if status == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if head:
            return
        try:
            with path.open('rb') as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    block = stream.read(min(65536, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_POST(self):
        if not self.path.startswith("/api/"):
            self.send_error(404)
            return

        method_name = self.path.replace("/api/", "")
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            content_length = 0

        if method_name == "upload_audio":
            try:
                raw_body = self.rfile.read(content_length) if content_length > 0 else b""
                upload_dir = os.path.join(DATA_DIR, "storage", "recordings")
                os.makedirs(upload_dir, exist_ok=True)

                content_type = self.headers.get("Content-Type", "")
                model = "offline"
                filename = None
                file_content = None

                if "multipart/form-data" in content_type:
                    msg_bytes = f"Content-Type: {content_type}\r\n\r\n".encode("latin1") + raw_body
                    msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(msg_bytes)
                    for part in msg.iter_parts():
                        cd_name = part.get_param("name", header="Content-Disposition")
                        if cd_name == "audio":
                            file_content = part.get_payload(decode=True)
                            filename = part.get_filename()
                        elif cd_name in ("engine", "model"):
                            try:
                                payload = part.get_payload(decode=True)
                                if payload:
                                    model = payload.decode("utf-8", errors="ignore").strip()
                            except Exception:
                                pass
                else:
                    file_content = raw_body

                if not file_content:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Аудиофайл не передан или пуст."}, ensure_ascii=False).encode("utf-8"))
                    return

                ext = ".wav"
                if filename:
                    ext = os.path.splitext(filename)[1] or ".wav"

                unique_name = f"upload_{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"
                save_path = os.path.join(upload_dir, unique_name)
                with open(save_path, "wb") as f:
                    f.write(file_content)

                if hasattr(self.bridge, 'submit_existing_audio'):
                    res = self.bridge.submit_existing_audio(save_path, title=filename)
                else:
                    res = self.bridge.process_existing_audio(save_path, model=model)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                import traceback
                traceback.print_exc()
                err_res = {"error": str(e)}
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(err_res, ensure_ascii=False).encode("utf-8"))
            return

        try:
            body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""
            params = json.loads(body) if body else {}
        except Exception as e:
            self.send_response(400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Некорректный JSON: {e}"}, ensure_ascii=False).encode("utf-8"))
            return

        if not method_name.startswith("_") and callable(getattr(self.bridge, method_name, None)):
            func = getattr(self.bridge, method_name)
            try:
                result = func(params)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return


def start_http_server(preferred_port=8000, bridge=None):
    AppHttpServer.bridge = bridge
    server = None
    actual_port = preferred_port
    for p in range(preferred_port, preferred_port + 20):
        try:
            ThreadingHTTPServer.allow_reuse_address = False
            server = ThreadingHTTPServer(("127.0.0.1", p), AppHttpServer)
            actual_port = server.server_address[1]
            break
        except OSError:
            continue
    if server is None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), AppHttpServer)
        actual_port = server.server_address[1]

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"[Server] Local API & Web server running at http://127.0.0.1:{actual_port}")
    return server, actual_port


def main():
    bridge = ProtocolApiBridge()
    if "--self-test" in sys.argv:
        from core.self_test import run
        raise SystemExit(run(bridge, start_http_server))
    port = int(os.getenv("PORT", 8000))
    server, actual_port = start_http_server(preferred_port=port, bridge=bridge)

    print("\n=======================================================")
    print("  QAZAQPROTOCOL PRO — СИСТЕМА АВТОПРОТОКОЛИРОВАНИЯ")
    print("  Локальная обработка • без API-ключей")
    print(f"  Локальный адрес: http://127.0.0.1:{actual_port}")
    print("=======================================================\n")

    use_desktop = "--web" not in sys.argv
    if use_desktop and webview is not None:
        try:
            print("[Desktop] Запуск нативного окна программы...")
            window = webview.create_window(
                title="QazaqProtocol AI — Студия автопротоколирования совещаний",
                url=f"http://127.0.0.1:{actual_port}/index.html",
                width=1420,
                height=920,
                min_size=(1024, 700),
                text_select=True
            )
            bridge._window = window

            def can_close():
                if bridge.recorder.is_recording or any(job['state'] in ('queued', 'running') for job in bridge.jobs.list()):
                    window.evaluate_js("updateAiThought('Сначала остановите запись и дождитесь завершения обработки. Затем закройте приложение.')")
                    return False
                return True

            window.events.closing += can_close
            if "--ui-self-test" in sys.argv:
                from core.ui_self_test import run_gui_check
                webview.start(run_gui_check, (window, bridge), gui="edgechromium", debug=False)
            else:
                webview.start(gui="edgechromium", debug=False)
            if bridge.recorder.is_recording:
                bridge.recorder.stop_recording()
            if bridge.live:
                bridge.live.stop()
            server.shutdown()
            server.server_close()
            return
        except Exception as e:
            bridge._window = None
            print(f"[Desktop] Окно WebView не удалось открыть ({e}). Открываем браузер: http://127.0.0.1:{actual_port}")
            try:
                import webbrowser
                webbrowser.open(f"http://127.0.0.1:{actual_port}/index.html")
            except Exception:
                pass
    else:
        try:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{actual_port}/index.html")
        except Exception:
            pass

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nОстановка приложения.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        try:
            log_path = os.path.join(DATA_DIR, "crash.log")
            with open(log_path, "w", encoding="utf-8") as f:
                traceback.print_exc(file=f)
        except Exception:
            pass
