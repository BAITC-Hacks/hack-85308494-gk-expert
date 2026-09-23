"""Packaged end-to-end diagnostic: real local STT, HTTP upload, DOCX and PDF."""
import argparse
import json
from pathlib import Path
import socket
import time
import traceback
import urllib.request
import zipfile
from unittest.mock import patch


def run(bridge, start_server):
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--audio", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--live-parallel", action="store_true")
    options = parser.parse_args()
    report = {"ok": False, "external_connection_attempts": 0}
    server = None
    live = None
    original_connect = socket.socket.connect

    def local_only(connection, address):
        if address[0] not in ("127.0.0.1", "::1", "localhost"):
            report["external_connection_attempts"] += 1
            raise RuntimeError("External network connection forbidden during self-test")
        return original_connect(connection, address)

    started = time.monotonic()
    try:
        with patch.object(socket.socket, "connect", local_only):
            server, port = start_server(preferred_port=0, bridge=bridge)
            # preferred_port=0 asks the OS for a unique free port.
            port = server.server_address[1]
            audio = Path(options.audio)
            boundary = "QazaqProtocolSelfTestBoundary"
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="test{audio.suffix}"\r\n'
                    'Content-Type: application/octet-stream\r\n\r\n').encode() + audio.read_bytes()
            body += f'\r\n--{boundary}--\r\n'.encode()
            request = urllib.request.Request(f"http://127.0.0.1:{port}/api/upload_audio", data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=600) as response:
                result = json.load(response)
            if options.live_parallel:
                from faster_whisper.audio import decode_audio
                from types import SimpleNamespace
                from core.audio_pipeline import LiveTranscriber, RATE
                samples = decode_audio(str(audio), sampling_rate=RATE)[:RATE * 8]
                recorder = SimpleNamespace(available_seconds=lambda: len(samples) / RATE,
                    read_live_window=lambda *_: samples)
                live = LiveTranscriber(recorder, bridge.live_stt, bridge.media, bridge.settings)
                live.start()
            if "error" in result:
                raise RuntimeError(result["error"])
            if 'job' in result:
                identifier = result['job']['id']
                deadline = time.monotonic() + 900
                while time.monotonic() < deadline:
                    job = bridge.get_job({'id': identifier})
                    if live and live.status()['segments'] and job['state'] == 'running':
                        report['live_text_during_file_processing'] = True
                    if job['state'] == 'error':
                        raise RuntimeError(job['error'])
                    if job['state'] == 'done':
                        result = {'meeting': bridge.get_meeting({'id': job['meeting_id']})}
                        report['chunked_job'] = True
                        break
                    time.sleep(0.2)
                else:
                    raise TimeoutError('File transcription job did not finish')
            meeting = result["meeting"]
            assert meeting["transcript"].strip(), "No speech detected in the test recording"
            assert meeting["processing_location"] == "local"
            assert bridge.manager.get_meeting(meeting["id"])["transcript"] == meeting["transcript"]
            if live:
                assert report.get('live_text_during_file_processing'), 'No live result while file worker was active'
                report['live_characters'] = sum(len(s['text']) for s in live.status()['segments'])
            if meeting.get('audio_url'):
                request = urllib.request.Request(f"http://127.0.0.1:{port}" + meeting['audio_url'], headers={'Range': 'bytes=0-43'})
                with opener.open(request) as response:
                    assert response.status == 206 and response.read().startswith(b'RIFF')
                report['playable_audio_range'] = True
            exported = bridge.export_transcript({'meeting_id': meeting['id']})
            assert meeting['transcript'][:40] in Path(exported['filepath']).read_text(encoding='utf-8-sig')
            report['txt_utf8'] = True
            for format_name in ("docx", "pdf"):
                exported = getattr(bridge, "export_" + format_name)({"meeting_id": meeting["id"]})
                path = Path(exported["filepath"])
                assert path.stat().st_size > 500
                if format_name == "docx":
                    with zipfile.ZipFile(path) as document:
                        assert b"word/document.xml" in b" ".join(n.encode() for n in document.namelist())
                else:
                    assert path.read_bytes().startswith(b"%PDF")
                report[format_name + "_bytes"] = path.stat().st_size
            devices = bridge.get_audio_devices()
            report.update(ok=True, transcript_characters=len(meeting["transcript"]),
                          dialogue_segments=len(meeting["dialogue"]), tasks=len(meeting["tasks"]),
                          duration=meeting["audio_duration"], local_model_ready=bridge.get_settings()["model_ready"],
                          microphones=len(devices.get("microphones", [])), loopbacks=len(devices.get("loopbacks", [])))
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if live:
            live.stop()
            live._thread.join(20)
        if server:
            server.shutdown()
            server.server_close()
        report["seconds"] = round(time.monotonic() - started, 2)
        Path(options.report).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if report["ok"] else 1
