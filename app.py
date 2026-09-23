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
if sys.stdout is None:
    class NullWriter:
        def write(self, *args, **kwargs): pass
        def flush(self, *args, **kwargs): pass
    sys.stdout = NullWriter()
else:
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

if sys.stderr is None:
    class NullWriter:
        def write(self, *args, **kwargs): pass
        def flush(self, *args, **kwargs): pass
    sys.stderr = NullWriter()
else:
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass
from http.server import HTTPServer, SimpleHTTPRequestHandler
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

ENV_PATH = os.path.join(DATA_DIR, ".env")
if not os.path.isfile(ENV_PATH):
    src_env = os.path.join(RESOURCE_DIR, ".env")
    src_example = os.path.join(RESOURCE_DIR, ".env.example")
    copied = False
    import shutil
    if os.path.isfile(src_env):
        try:
            shutil.copyfile(src_env, ENV_PATH)
            copied = True
        except Exception:
            pass
    elif os.path.isfile(src_example):
        try:
            shutil.copyfile(src_example, ENV_PATH)
            copied = True
        except Exception:
            pass
    
    if not copied and not os.path.isfile(ENV_PATH):
        try:
            with open(ENV_PATH, "w", encoding="utf-8") as f:
                f.write("# QazaqProtocol AI Environment Configuration\n")
                f.write("OPENAI_API_KEY=\n")
                f.write("PORT=8000\n")
                f.write("DEFAULT_COMPANY=Организация\n")
        except Exception:
            pass

load_dotenv(ENV_PATH)

# Seed storage if running as frozen executable
if IS_FROZEN:
    import shutil
    for folder in ["meetings", "recordings", "protocols", "sed_exports"]:
        target_folder = os.path.join(DATA_DIR, "storage", folder)
        os.makedirs(target_folder, exist_ok=True)
        src_folder = os.path.join(RESOURCE_DIR, "storage", folder)
        if os.path.exists(src_folder):
            for item in os.listdir(src_folder):
                s_item = os.path.join(src_folder, item)
                t_item = os.path.join(target_folder, item)
                if not os.path.exists(t_item) and os.path.isfile(s_item):
                    shutil.copyfile(s_item, t_item)
    # Seed companies.json
    src_comp = os.path.join(RESOURCE_DIR, "storage", "companies.json")
    tgt_comp = os.path.join(DATA_DIR, "storage", "companies.json")
    if os.path.exists(src_comp) and not os.path.exists(tgt_comp):
        shutil.copyfile(src_comp, tgt_comp)

from core.audio_recorder import AudioRecorder
from core.screen_stream import ScreenStreamer
from core.stt_engine import SpeechToTextEngine
from core.nlp_extractor import NLPExtractor
from core.export_service import ExportService
from core.sed_integrator import SEDIntegrator
from core.meeting_manager import MeetingManager
from core.company_group_manager import CompanyGroupManager

try:
    import webview
except ImportError:
    webview = None


class ProtocolApiBridge:
    """
    Exposes desktop methods to JavaScript in the GUI window.
    """

    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.recorder = AudioRecorder(output_dir=os.path.join(DATA_DIR, "storage", "recordings"))
        self.screen = ScreenStreamer()
        self.stt = SpeechToTextEngine(api_key=self.api_key)
        self.nlp = NLPExtractor(api_key=self.api_key)
        self.exporter = ExportService(output_dir=os.path.join(DATA_DIR, "storage", "protocols"))
        self.sed = SEDIntegrator(export_dir=os.path.join(DATA_DIR, "storage", "sed_exports"))
        self.manager = MeetingManager(data_dir=os.path.join(DATA_DIR, "storage", "meetings"))
        self.comp_mgr = CompanyGroupManager(data_dir=os.path.join(DATA_DIR, "storage"))

    def get_audio_devices(self, params=None):
        return self.recorder.get_audio_devices()

    def start_recording(self, params=None):
        params = params or {}
        mode = params.get("mode", "mix")
        mic_idx = params.get("mic_index")
        loop_idx = params.get("loopback_index")
        return self.recorder.start_recording(mode=mode, mic_index=mic_idx, loopback_index=loop_idx)

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

        print(f"[App] Dual-track recording complete. Transcribing master: {master_file}...")

        # 2. Transcribe master audio
        transcript_data = self.stt.transcribe(master_file)

        # 3. Extract NLP (tasks, key moments, executive memo)
        meeting = self.nlp.process_transcript(transcript_data, model=model)
        meeting["id"] = f"meeting_{int(time.time())}"
        meeting["date"] = time.strftime("%d.%m.%Y")
        meeting["audio_mic_track"] = rec_files.get("mic", "")
        meeting["audio_sys_track"] = rec_files.get("sys", "")

        # 4. Save to DB
        self.manager.save_meeting(meeting)

        # 5. Pre-generate exports
        self.exporter.export_to_docx(meeting, f"Протокол_{meeting['id']}.docx")
        self.exporter.export_to_pdf(meeting, f"Протокол_{meeting['id']}.pdf")
        self.sed.export_to_sed_json(meeting, f"СЭД_{meeting['id']}.json")

        return {"meeting": meeting}

    def process_existing_audio(self, filepath: str, model: str = "offline"):
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")

        transcript_data = self.stt.transcribe(filepath)
        meeting = self.nlp.process_transcript(transcript_data, model=model)
        meeting["id"] = f"meeting_{int(time.time())}"
        meeting["date"] = time.strftime("%d.%m.%Y")
        meeting["audio_filename"] = os.path.basename(filepath)

        self.manager.save_meeting(meeting)
        self.exporter.export_to_docx(meeting, f"Протокол_{meeting['id']}.docx")
        self.exporter.export_to_pdf(meeting, f"Протокол_{meeting['id']}.pdf")
        self.sed.export_to_sed_json(meeting, f"СЭД_{meeting['id']}.json")

        return {"meeting": meeting}

    def get_meeting(self, params):
        meeting_id = params.get("id")
        return self.manager.get_meeting(meeting_id)

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

    def save_settings(self, params):
        company = params.get("company", "").strip()
        prompt = params.get("prompt", "").strip()
        if company:
            os.environ["DEFAULT_COMPANY"] = company
        try:
            with open(ENV_PATH, "w", encoding="utf-8") as f:
                f.write(f"PORT=8000\nDEFAULT_COMPANY={company or 'Организация'}\nSTT_OFFLINE=1\n")
        except Exception:
            pass
        return {"success": True}


class AppHttpServer(SimpleHTTPRequestHandler):
    """Local HTTP Server for assets, API calls, and file uploads."""
    bridge = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.join(RESOURCE_DIR, "ui"), **kwargs)

    def do_POST(self):
        if not self.path.startswith("/api/"):
            super().do_POST()
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

        if hasattr(self.bridge, method_name):
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
            HTTPServer.allow_reuse_address = False
            server = HTTPServer(("127.0.0.1", p), AppHttpServer)
            actual_port = p
            break
        except OSError:
            continue
    if server is None:
        server = HTTPServer(("127.0.0.1", 0), AppHttpServer)
        actual_port = server.server_address[1]

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"[Server] Local API & Web server running at http://127.0.0.1:{actual_port}")
    return server, actual_port


def main():
    bridge = ProtocolApiBridge()
    port = int(os.getenv("PORT", 8000))
    server, actual_port = start_http_server(preferred_port=port, bridge=bridge)

    print("\n=======================================================")
    print("  QAZAQPROTOCOL PRO — СИСТЕМА АВТОПРОТОКОЛИРОВАНИЯ")
    print("  АО «Самрук-Қазына Өңдеу»")
    print(f"  Локальный адрес: http://127.0.0.1:{actual_port}")
    print("=======================================================\n")

    use_desktop = "--web" not in sys.argv
    if use_desktop and webview is not None:
        try:
            print("[Desktop] Запуск нативного окна программы...")
            window = webview.create_window(
                title="QazaqProtocol AI — Студия автопротоколирования совещаний",
                url=f"http://127.0.0.1:{actual_port}/index.html",
                js_api=bridge,
                width=1420,
                height=920,
                min_size=(1024, 700),
                text_select=True
            )
            webview.start(gui="edgechromium", debug=False)
            return
        except Exception as e:
            print(f"[Desktop] Окно WebView не удалось открыть ({e}). Доступен веб-режим: http://127.0.0.1:{port}")

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
