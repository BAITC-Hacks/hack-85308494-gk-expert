"""Exercise the real WebView2 UI without recording microphone input."""
import json
from pathlib import Path
import sys
import time
import traceback


def run_gui_check(window, bridge):
    report_path = Path(sys.argv[sys.argv.index("--ui-self-test") + 1])
    report = {"ok": False}
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                ready = window.evaluate_js("document.readyState === 'complete' && typeof devicesLoaded !== 'undefined' && devicesLoaded")
                if ready:
                    break
            except Exception:
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("UI or audio device initialization did not finish")
        first = window.evaluate_js("document.getElementById('waveformCanvas').toDataURL()")
        time.sleep(0.5)
        second = window.evaluate_js("document.getElementById('waveformCanvas').toDataURL()")
        assert first == second, "Waveform moves when idle"
        assert window.evaluate_js("document.getElementById('engineSelect').value") == "offline"
        assert window.evaluate_js("!document.getElementById('btnStartRec').disabled")
        assert window.evaluate_js("document.getElementById('btnStopRec').disabled")
        # Render a synthetic meeting, including characters that must stay text.
        meeting = bridge.nlp.process_transcript({"text": "Әлия, подготовьте отчёт завтра. Бюджет < 5 & план > 2."})
        meeting["id"] = "ui_test_only"
        window.evaluate_js("renderMeeting(" + json.dumps(meeting, ensure_ascii=False) + ")")
        assert window.evaluate_js("document.getElementById('tabContentTranscript').classList.contains('active')")
        rendered = window.evaluate_js("document.getElementById('dialogueChat').innerText")
        assert "Әлия" in rendered and "Бюджет < 5 & план > 2." in rendered
        window.evaluate_js("switchTab('tasks')")
        assert window.evaluate_js("document.getElementById('tasksTableBody').innerText.includes('Әлия')")
        window.evaluate_js("setProcessing(true)")
        assert window.evaluate_js("document.getElementById('btnStartRec').disabled && document.getElementById('audioFileInput').disabled")
        window.evaluate_js("setProcessing(false)")
        report.update(ok=True, engine="offline", idle_waveform_static=True, unicode_transcript=True,
                      task_table=True, processing_controls=True, actual_webview2=True)
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        window.destroy()
