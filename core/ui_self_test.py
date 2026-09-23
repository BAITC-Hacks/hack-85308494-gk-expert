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
        # Selecting a new file must retire the old meeting even during delayed HTTP responses.
        from core.speaker_context import resolve_names
        rows, profiles = resolve_names([
            {'speaker_id': 'speaker_1', 'start': 0, 'end': 2, 'text': 'Айдар, вам слово.'},
            {'speaker_id': 'speaker_2', 'start': 3, 'end': 6, 'text': 'Я подготовлю новый отчёт завтра.'}])
        newer = bridge.nlp.process_transcript({'segments': rows, 'speakers': profiles, 'diarization': 'performed', 'duration': 19})
        newer['id'] = 'ui_test_new_file'
        bridge.manager.save_meeting(newer)
        with bridge.jobs._lock:
            bridge.jobs._jobs['ui_job_new'] = {'id': 'ui_job_new', 'state': 'done', 'stage': 'Сохранено', 'name': 'Новый образец', 'meeting_id': newer['id']}
            bridge.jobs._jobs['ui_job_pending'] = {'id': 'ui_job_pending', 'state': 'queued', 'stage': 'В очереди', 'name': 'Ещё обрабатывается'}
        try:
            window.evaluate_js("selectFileJob({id:'ui_job_pending',state:'queued'}); toggleProtocolPanel(); exportTranscript()")
            time.sleep(.25)
            assert window.evaluate_js("currentMeetingData === null && !document.getElementById('protocolPanel').classList.contains('open')")
            window.evaluate_js("window.originalTestApi = callApi; callApi = async (method, params = {}) => { if (method === 'get_meeting' && params.id === 'ui_test_only') await new Promise(r => setTimeout(r, 600)); return window.originalTestApi(method, params); }; openDemoMeeting('ui_test_only'); selectFileJob({id:'ui_job_new',state:'done'}); toggleProtocolPanel()")
            wait_js(window, "currentMeetingData?.id === 'ui_test_new_file'")
            time.sleep(.8)
            assert window.evaluate_js("currentMeetingData.id === 'ui_test_new_file' && currentMeetingData.audio_duration === 19")
            window.evaluate_js("callApi = window.originalTestApi")
            report['selected_file_protocol'] = True
            report['stale_response_ignored'] = True
            assert window.evaluate_js("document.querySelectorAll('.speaker-card').length === 2")
            window.evaluate_js("let card = document.querySelectorAll('.speaker-card')[1]; card.querySelector('input').value = 'Әлия'; card.querySelector('button').click()")
            wait_js(window, "currentMeetingData?.speakers?.[1]?.name_status === 'confirmed'")
            saved = bridge.manager.get_meeting(newer['id'])
            assert saved['dialogue'][1]['speaker'] == 'Әлия'
            assert saved['tasks'][0]['assignee'] == 'Әлия'
            txt = Path(bridge.export_transcript({'meeting_id': newer['id']})['filepath']).read_text(encoding='utf-8-sig')
            assert 'Әлия' in txt
            report['speaker_name_correction'] = True
            _, fragment_url = bridge.media.save(np.zeros(16000 * 8, dtype=np.float32), 'ui_fragment')
            bridge.media.publish_fragment(fragment_url, 8, 16, source='Live-запись')
            chunk = {'start': 8, 'end': 16, 'audio_url': fragment_url}
            window.evaluate_js('lastLive.current_chunk = ' + json.dumps(chunk) + "; listenCurrentChunk('live')")
            wait_js(window, "currentMeetingData?.is_fragment === true && currentMeetingData.audio_duration === 8")
            assert window.evaluate_js("currentMeetingData.fragment_ready === false && currentMeetingData.transcript === ''")
            bridge.media.publish_fragment(fragment_url, 8, 16, [{'start': 9, 'end': 12, 'text': 'Только короткий фрагмент.'}], source='Live-запись')
            wait_js(window, "currentMeetingData.fragment_ready === true && document.getElementById('dialogueChat').innerText.includes('Только короткий фрагмент.')")
            wait_js(window, "document.getElementById('archiveAudio').duration === 8")
            assert window.evaluate_js("!document.getElementById('dialogueChat').innerText.includes('новый отчёт')")
            fragment_id = 'fragment_' + fragment_url.split('/')[-1]
            exported = Path(bridge.export_transcript({'meeting_id': fragment_id})['filepath']).read_text(encoding='utf-8-sig')
            assert 'Только короткий фрагмент.' in exported and 'новый отчёт' not in exported
            report['live_fragment_exact_audio_and_text'] = True
            report['fragment_export'] = True
        finally:
            with bridge.jobs._lock:
                bridge.jobs._jobs.pop('ui_job_new', None)
                bridge.jobs._jobs.pop('ui_job_pending', None)
        report.update(ok=True, engine="offline", idle_waveform_static=True, unicode_transcript=True,
                      task_table=True, parallel_controls=True, actual_webview2=True)
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        window.destroy()
