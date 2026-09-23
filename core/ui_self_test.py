"""Exercise the real WebView2 UI without recording microphone input."""
import json
from pathlib import Path
import sys
import time
import traceback
import threading
import os


def wait_js(window, expression, timeout=10):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if window.evaluate_js(expression):
            return True
        time.sleep(.1)
    raise AssertionError(expression)


def cancel_native_dialog(window, bridge, report):
    """Close only this test process's native dialog, without keyboard input."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    seen = threading.Event()
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(handle, _):
        process = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(process))
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(handle, name, 256)
        if process.value == os.getpid() and name.value == '#32770' and user32.IsWindowVisible(handle):
            seen.set()
            user32.PostMessageW(handle, 0x0010, 0, 0)  # WM_CLOSE
        return True

    def close():
        until = time.monotonic() + 15
        while time.monotonic() < until and not seen.is_set():
            user32.EnumWindows(visit, 0)
            time.sleep(.1)

    closer = threading.Thread(target=close, daemon=True)
    closer.start()
    result = bridge.save_export({'meeting_id': 'ui_test_only', 'format': 'txt'})
    closer.join(2)
    assert seen.is_set(), 'Native Save As dialog did not open'
    assert result.get('cancelled'), result
    report['native_save_dialog'] = True


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
        assert window.evaluate_js("currentMeetingData === null && !document.getElementById('protocolPanel').classList.contains('open') && document.getElementById('historyList').hidden")
        report['empty_startup'] = True
        window.evaluate_js("lockSource('mic')")
        assert window.evaluate_js("document.getElementById('micDeviceSelect').disabled")
        window.evaluate_js("lockSource('mic'); toggleSource('mic')")
        wait_js(window, "!sourceState.mic.enabled")
        assert bridge.recorder._muted['mic']
        window.evaluate_js("toggleSource('mic')")
        wait_js(window, "sourceState.mic.enabled")
        report['source_buttons'] = True
        # Render a synthetic meeting, including characters that must stay text.
        meeting = bridge.nlp.process_transcript({"text": "Әлия, подготовьте отчёт завтра. Бюджет < 5 & план > 2."})
        meeting["id"] = "ui_test_only"
        bridge.manager.save_meeting(meeting)
        window.evaluate_js("renderMeeting(" + json.dumps(meeting, ensure_ascii=False) + ")")
        assert window.evaluate_js("document.getElementById('tabContentTranscript').classList.contains('active')")
        rendered = window.evaluate_js("document.getElementById('dialogueChat').innerText")
        assert "Әлия" in rendered and "Бюджет < 5 & план > 2." in rendered
        window.evaluate_js("switchTab('tasks')")
        assert window.evaluate_js("document.getElementById('tasksTableBody').innerText.includes('Әлия')")
        window.evaluate_js("setProcessing(true)")
        assert window.evaluate_js("!document.getElementById('btnStartRec').disabled && !document.getElementById('audioFileInput').disabled")
        window.evaluate_js("setProcessing(false)")
        window.evaluate_js("document.getElementById('protocolPanel').classList.remove('open'); showWorkspaceTab('history')")
        wait_js(window, "document.querySelectorAll('#historyList .history-item').length > 0")
        assert window.evaluate_js("!document.getElementById('protocolPanel').classList.contains('open')")
        window.evaluate_js("document.querySelector('#historyList .history-item').click()")
        wait_js(window, "document.getElementById('protocolPanel').classList.contains('open')")
        report['explicit_history'] = True
        import numpy as np
        _, url = bridge.media.save((.04 * np.sin(2 * np.pi * 800 * np.arange(32000) / 16000)).astype(np.float32), 'ui_tone')
        window.evaluate_js("let p = document.getElementById('monitorAudio'); p.src = " + json.dumps(url) + "; p.play().catch(e => window.playbackTestError = e.message)")
        wait_js(window, "activePlayback !== null && audioContext.state === 'running'")
        wait_js(window, "(() => {let s = audioNodes.get(activePlayback); s.analyser.getByteFrequencyData(s.bins); return s.bins.some(v => v > 0)})()")
        moving = window.evaluate_js("document.getElementById('waveformCanvas').toDataURL()")
        assert moving != first
        window.evaluate_js("document.getElementById('monitorAudio').pause()")
        time.sleep(.15)
        assert window.evaluate_js("document.getElementById('waveformCanvas').toDataURL()") == first
        report['playback_equalizer'] = True
        cancel_native_dialog(window, bridge, report)
        report.update(ok=True, engine="offline", idle_waveform_static=True, unicode_transcript=True,
                      task_table=True, parallel_controls=True, actual_webview2=True)
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        window.destroy()
