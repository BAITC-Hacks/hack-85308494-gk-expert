/**
 * QAZAQPROTOCOL PRO — FRONTEND ENGINE & OBS CONTROLLER (EXTENDED)
 * With Dual-Track Recording, Key Moments, Notes & Memos, and Company/Group Filter
 */

let currentMeetingData = null;
let isRecording = false;
let isPaused = false;
let recTimerInterval = null;
let vuMeterInterval = null;
let waveformAnimationId = null;
let currentFilter = 'all';
let currentCompanyFilter = 'all';
let screenStream = null;
let companiesList = [];

// Audio wave canvas state
const canvas = document.getElementById('waveformCanvas');
const ctx = canvas.getContext('2d');
let latestAudioLevel = 0;

// Unified API Caller (pywebview or REST API)
async function callApi(method, params = {}) {
  if (window.pywebview && window.pywebview.api && typeof window.pywebview.api[method] === 'function') {
    return await window.pywebview.api[method](params);
  }
  try {
    const res = await fetch(`/api/${method}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params)
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn(`API call ${method} fallback error:`, err);
    throw err;
  }
}

// --------------------------------------------------------------------------
// INITIALIZATION
// --------------------------------------------------------------------------
window.addEventListener('DOMContentLoaded', async () => {
  startWaveformAnimation();
  initAudioDevices();
  loadCompanies();
  loadHistoryList();

  setTimeout(() => {
    openDemoMeeting('meeting_01');
  }, 300);
});

window.addEventListener('pywebviewready', () => {
  initAudioDevices();
  loadCompanies();
  loadHistoryList();
});

// --------------------------------------------------------------------------
// COMPANIES & GROUPS
// --------------------------------------------------------------------------
async function loadCompanies() {
  try {
    const comps = await callApi('get_companies');
    if (comps && comps.length > 0) {
      companiesList = comps;
      const select = document.getElementById('companyFilterSelect');
      select.innerHTML = '<option value="all">🏢 Все компании холдинга</option>';
      comps.forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.id;
        opt.textContent = `${c.name} (${c.type})`;
        select.appendChild(opt);
      });
    }
  } catch (e) {
    console.error("Failed to load companies:", e);
  }
}

function onCompanyFilterChange() {
  currentCompanyFilter = document.getElementById('companyFilterSelect').value;
  loadHistoryList();
  if (currentCompanyFilter !== 'all') {
    const comp = companiesList.find(c => c.id === currentCompanyFilter);
    updateAiThought(`Фильтр по компании: ${comp ? comp.name : currentCompanyFilter}`);
  } else {
    updateAiThought("Отображаются совещания всех компаний холдинга.");
  }
}

function openCompanyModal() {
  document.getElementById('companyModal').style.display = 'flex';
}

function closeCompanyModal() {
  document.getElementById('companyModal').style.display = 'none';
}

async function submitNewCompany() {
  const name = document.getElementById('newCompanyName').value.trim();
  const type = document.getElementById('newCompanyType').value;
  const code = document.getElementById('newCompanyCode').value.trim();

  if (!name) {
    alert("Укажите название компании");
    return;
  }

  try {
    await callApi('add_company', { name, type, code });
    await loadCompanies();
    closeCompanyModal();
    updateAiThought(`Создана новая организация в структуре: «${name}».`);
  } catch (err) {
    alert("Ошибка создания компании: " + err.message);
  }
}

// --------------------------------------------------------------------------
// OBS SCREEN CAPTURE & VIDEO
// --------------------------------------------------------------------------
async function selectScreenShare() {
  const videoEl = document.getElementById('screenVideoPreview');
  const canvasEl = document.getElementById('waveformCanvas');
  const btn = document.getElementById('btnSelectWindow');

  try {
    if (screenStream) {
      screenStream.getTracks().forEach(track => track.stop());
      screenStream = null;
      videoEl.style.display = 'none';
      canvasEl.style.display = 'block';
      btn.innerHTML = '<i class="fa-solid fa-display"></i> Окно Zoom / Discord';
      updateAiThought("Захват экрана отключен. Монитор переключен на визуализатор аудиопотока.");
      return;
    }

    screenStream = await navigator.mediaDevices.getDisplayMedia({
      video: { cursor: "always" },
      audio: false
    });

    videoEl.srcObject = screenStream;
    videoEl.style.display = 'block';
    canvasEl.style.display = 'none';
    btn.innerHTML = '<i class="fa-solid fa-stop"></i> Отключить захват';
    updateAiThought("Видеозахват окна конференции активен (OBS Monitor). Звук конференции пишется аппаратно через WASAPI Loopback.");

    screenStream.getVideoTracks()[0].onended = () => {
      videoEl.style.display = 'none';
      canvasEl.style.display = 'block';
      btn.innerHTML = '<i class="fa-solid fa-display"></i> Окно Zoom / Discord';
      screenStream = null;
    };
  } catch (err) {
    console.log("Screen share cancelled or not allowed:", err);
  }
}

// --------------------------------------------------------------------------
// AUDIO DEVICES & OBS VU-METERS
// --------------------------------------------------------------------------
async function initAudioDevices() {
  try {
    const data = await callApi('get_audio_devices');
    const loopSelect = document.getElementById('loopbackDeviceSelect');
    const micSelect = document.getElementById('micDeviceSelect');

    loopSelect.innerHTML = '';
    micSelect.innerHTML = '';

    if (data.loopbacks && data.loopbacks.length > 0) {
      let defaultSelected = false;
      data.loopbacks.forEach(dev => {
        const opt = document.createElement('option');
        opt.value = dev.index;
        opt.textContent = dev.name;
        if (dev.is_default) {
          opt.selected = true;
          defaultSelected = true;
        }
        loopSelect.appendChild(opt);
      });
      if (!defaultSelected && loopSelect.options.length > 0) {
        loopSelect.options[0].selected = true;
      }
    } else {
      const opt = document.createElement('option');
      opt.textContent = 'WASAPI Loopback (Системные динамики)';
      loopSelect.appendChild(opt);
    }

    if (data.microphones && data.microphones.length > 0) {
      let defaultSelected = false;
      data.microphones.forEach(dev => {
        const opt = document.createElement('option');
        opt.value = dev.index;
        opt.textContent = dev.name;
        if (dev.is_default) {
          opt.selected = true;
          defaultSelected = true;
        }
        micSelect.appendChild(opt);
      });
      if (!defaultSelected && micSelect.options.length > 0) {
        micSelect.options[0].selected = true;
      }
    } else {
      const opt = document.createElement('option');
      opt.textContent = 'Основной микрофон системы';
      micSelect.appendChild(opt);
    }
  } catch (e) {
    console.error("Failed to load audio devices:", e);
  }
}

function onTrackModeChange() {
  const mode = document.getElementById('audioTrackSelect').value;
  const badge = document.getElementById('dualTrackBadge');
  if (mode === 'system') {
    badge.innerHTML = '<i class="fa-solid fa-volume-high"></i> 1 ТРЕК (SYS LOOPBACK)';
    updateAiThought("Выбран режим захвата конференции (Zoom/Discord): микрофон отключен.");
  } else if (mode === 'mic') {
    badge.innerHTML = '<i class="fa-solid fa-microphone"></i> 1 ТРЕК (MIC ONLY)';
    updateAiThought("Выбран режим только микрофона: звук собеседников отключен.");
  } else {
    badge.innerHTML = '<i class="fa-solid fa-layer-group"></i> 2 ТРЕКА (MIC + SYS)';
    updateAiThought("Выбран режим РАЗДЕЛЬНЫХ ДОРОЖЕК: Микрофон (Я) и Конференция (Собеседники) пишутся параллельно.");
  }
}

// --------------------------------------------------------------------------
// RECORDING LOGIC
// --------------------------------------------------------------------------
async function toggleRecording() {
  const btn = document.getElementById('btnStartRec');
  const btnText = document.getElementById('btnRecordText');
  const pauseBtn = document.getElementById('btnPauseRec');
  const recBadge = document.getElementById('recBadge');
  const statusPill = document.getElementById('liveStatusPill');

  if (!isRecording) {
    const trackMode = document.getElementById('audioTrackSelect').value;
    const micVal = document.getElementById('micDeviceSelect').value;
    const micIndex = (micVal !== "" && !isNaN(micVal)) ? parseInt(micVal, 10) : null;
    const loopVal = document.getElementById('loopbackDeviceSelect').value;
    const loopIndex = (loopVal !== "" && !isNaN(loopVal)) ? parseInt(loopVal, 10) : null;

    try {
      updateAiThought("Запуск многодорожечной записи (микрофон + WASAPI loopback)...");
      await callApi('start_recording', { mode: trackMode, mic_index: micIndex, loopback_index: loopIndex });

      isRecording = true;
      isPaused = false;
      btn.classList.add('recording-active');
      btnText.textContent = "ОСТАНОВИТЬ И ОБРАБОТАТЬ";
      pauseBtn.disabled = false;
      recBadge.style.display = 'inline-block';
      statusPill.innerHTML = '<span class="dot" style="background:#ef4444;box-shadow:0 0 8px #ef4444"></span> Запись идет';
      statusPill.style.color = '#ef4444';

      startVuPolling();
      updateAiThought("Идет раздельная запись совещания: дорожка докладчика и дорожка Zoom/Discord фиксируются независимо.");
    } catch (err) {
      alert("Ошибка запуска записи: " + err.message);
    }
  } else {
    btn.disabled = true;
    btnText.textContent = "СВЕДЕНИЕ И АНАЛИЗ ИИ...";
    pauseBtn.disabled = true;
    recBadge.style.display = 'none';
    statusPill.innerHTML = '<span class="dot" style="background:#3b82f6;box-shadow:0 0 8px #3b82f6"></span> Анализ ИИ...';
    statusPill.style.color = '#3b82f6';
    stopVuPolling();

    updateAiThought("Сведение аудиодорожек. Запуск распознавания Whisper, диаризации и извлечения ключевых моментов...");

    try {
      const selectedEngine = document.getElementById('engineSelect') ? document.getElementById('engineSelect').value : 'offline';
      const result = await callApi('stop_and_process', { model: selectedEngine });
      isRecording = false;
      btn.classList.remove('recording-active');
      btn.disabled = false;
      btnText.textContent = "НАЧАТЬ ЗАПИСЬ СОВЕЩАНИЯ";
      statusPill.innerHTML = '<span class="dot"></span> Готов';
      statusPill.style.color = '#10b981';

      if (result && result.meeting) {
        renderMeeting(result.meeting);
        loadHistoryList();
        updateAiThought(`Протокол готов! Найдено ${result.meeting.tasks.length} поручений и ${result.meeting.key_moments.length} ключевых моментов.`);
      }
    } catch (err) {
      isRecording = false;
      isPaused = false;
      btn.classList.remove('recording-active');
      btn.disabled = false;
      btnText.textContent = "НАЧАТЬ ЗАПИСЬ СОВЕЩАНИЯ";
      statusPill.innerHTML = '<span class="dot"></span> Готов';
      statusPill.style.color = '#10b981';
      alert("Ошибка обработки аудио: " + err.message);
      updateAiThought("Ошибка при обработке записи: " + err.message);
    }
  }
}

async function togglePauseRecording() {
  if (!isRecording) return;
  const pauseBtn = document.getElementById('btnPauseRec');
  await callApi('pause_recording');
  isPaused = !isPaused;
  if (isPaused) {
    pauseBtn.innerHTML = '<i class="fa-solid fa-play"></i>';
    pauseBtn.classList.add('btn-primary');
    updateAiThought("Запись временно приостановлена.");
  } else {
    pauseBtn.innerHTML = '<i class="fa-solid fa-pause"></i>';
    pauseBtn.classList.remove('btn-primary');
    updateAiThought("Запись продолжена.");
  }
}

function startVuPolling() {
  vuMeterInterval = setInterval(async () => {
    try {
      const status = await callApi('get_recording_status');
      if (status) {
        const secs = Math.floor(status.elapsed_seconds || 0);
        const h = String(Math.floor(secs / 3600)).padStart(2, '0');
        const m = String(Math.floor((secs % 3600) / 60)).padStart(2, '0');
        const s = String(secs % 60).padStart(2, '0');
        document.getElementById('recTimer').textContent = `${h}:${m}:${s}`;

        const micVal = Math.min(100, (status.mic_level || 0) * 100);
        const sysVal = Math.min(100, (status.system_level || 0) * 100);
        latestAudioLevel = Math.max(status.mic_level || 0, status.system_level || 0);

        document.getElementById('micVuBar').style.height = `${micVal}%`;
        document.getElementById('sysVuBar').style.height = `${sysVal}%`;

        document.getElementById('micDbVal').textContent = micVal > 1 ? `${Math.round(micVal - 60)} dB` : '-inf dB';
        document.getElementById('sysDbVal').textContent = sysVal > 1 ? `${Math.round(sysVal - 60)} dB` : '-inf dB';

        if (status.error) {
          updateAiThought("Внимание: " + status.error);
        }
      }
    } catch (e) {}
  }, 100);
}

function stopVuPolling() {
  if (vuMeterInterval) clearInterval(vuMeterInterval);
  latestAudioLevel = 0;
  document.getElementById('micVuBar').style.height = '0%';
  document.getElementById('sysVuBar').style.height = '0%';
  document.getElementById('micDbVal').textContent = '-inf dB';
  document.getElementById('sysDbVal').textContent = '-inf dB';
}

// --------------------------------------------------------------------------
// WAVEFORM VISUALIZER (STATIC AT REST, REACTS ONLY TO REAL AUDIO)
// --------------------------------------------------------------------------
function startWaveformAnimation() {
  let smoothedLevel = 0;

  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const w = canvas.width;
    const h = canvas.height;
    const midY = h / 2;

    const bars = 48;
    const barWidth = Math.max(2, (w / bars) - 2);

    // Target audio level is strictly 0 when not recording or paused
    const target = (isRecording && !isPaused) ? latestAudioLevel : 0;
    smoothedLevel += (target - smoothedLevel) * 0.25;

    // Completely static flat line when idle or silent
    if (smoothedLevel < 0.01) {
      ctx.fillStyle = '#2d333b';
      for (let i = 0; i < bars; i++) {
        const x = i * (barWidth + 2);
        ctx.fillRect(x, midY - 1, barWidth, 2);
      }
      waveformAnimationId = requestAnimationFrame(draw);
      return;
    }

    // Dynamic visualization based strictly on real audio RMS
    for (let i = 0; i < bars; i++) {
      const normalizedIdx = (i - bars / 2) / (bars / 2);
      const envelope = Math.exp(-2.2 * normalizedIdx * normalizedIdx);
      const barAmp = Math.min(1.0, smoothedLevel * 2.8 * envelope);
      const barHeight = Math.max(3, barAmp * (h * 0.85));
      const x = i * (barWidth + 2);
      const y = midY - barHeight / 2;

      const grad = ctx.createLinearGradient(0, y, 0, y + barHeight);
      grad.addColorStop(0, '#00d64f');
      grad.addColorStop(0.65, '#f5c210');
      grad.addColorStop(1, '#ef4444');

      ctx.fillStyle = grad;
      ctx.fillRect(x, y, barWidth, barHeight);
    }

    waveformAnimationId = requestAnimationFrame(draw);
  }
  draw();
}

// --------------------------------------------------------------------------
// FILE IMPORT & DEMOS
// --------------------------------------------------------------------------
function triggerFileInput() {
  document.getElementById('audioFileInput').click();
}

async function handleFileSelected(event) {
  const file = event.target.files[0];
  if (!file) return;

  updateAiThought(`Загружен файл '${file.name}'. Начинаю анализ...`);
  const statusPill = document.getElementById('liveStatusPill');
  statusPill.innerHTML = '<span class="dot" style="background:#3b82f6"></span> Анализ ИИ...';

  const selectedEngine = document.getElementById('engineSelect') ? document.getElementById('engineSelect').value : 'offline';
  const formData = new FormData();
  formData.append('audio', file);
  formData.append('engine', selectedEngine);

  try {
    const res = await fetch('/api/upload_audio', {
      method: 'POST',
      body: formData
    });
    const data = await res.json();
    if (data.error) {
      alert("Ошибка обработки аудио: " + data.error);
      updateAiThought("Ошибка обработки аудио: " + data.error);
      return;
    }
    if (data.meeting) {
      renderMeeting(data.meeting);
      loadHistoryList();
      updateAiThought(`Обработка завершена! Зафиксировано ${data.meeting.tasks.length} поручений.`);
    }
  } catch (err) {
    console.error(err);
    alert("Ошибка обработки файла: " + err.message);
  } finally {
    statusPill.innerHTML = '<span class="dot"></span> Готов';
    statusPill.style.color = '#10b981';
  }
}

async function openDemoMeeting(meetingId) {
  updateAiThought(`Загрузка протокола совещания '${meetingId}'...`);
  try {
    const meeting = await callApi('get_meeting', { id: meetingId });
    if (meeting) {
      renderMeeting(meeting);
      updateAiThought(`Загружено: «${meeting.title}». Найдено ${meeting.tasks.length} поручений и ${meeting.key_moments ? meeting.key_moments.length : 0} ключевых моментов.`);
    }
  } catch (err) {
    console.error("Error loading demo meeting:", err);
  }
}

// --------------------------------------------------------------------------
// RENDER MEETING
// --------------------------------------------------------------------------
function renderMeeting(m) {
  currentMeetingData = m;

  // Header meta & Company tag
  document.getElementById('currentMeetingTitle').textContent = m.title || "Совещание";
  document.getElementById('metaCompanyTag').textContent = m.company || 'АО «Самрук-Қазына Өңдеу»';
  document.getElementById('metaDate').innerHTML = `<i class="fa-regular fa-calendar"></i> ${m.date || 'Текущая дата'}`;
  document.getElementById('metaLeader').innerHTML = `<i class="fa-solid fa-user-tie"></i> ${m.leader || 'Председатель'}`;
  
  const durM = Math.floor((m.audio_duration || 0) / 60);
  const durS = Math.floor((m.audio_duration || 0) % 60);
  document.getElementById('metaDuration').innerHTML = `<i class="fa-solid fa-stopwatch"></i> ${durM} мин ${durS} сек`;

  // Counts
  const tasks = m.tasks || [];
  const moments = m.key_moments || [];
  document.getElementById('tabTasksCount').textContent = tasks.length;
  document.getElementById('tabMomentsCount').textContent = moments.length;

  // Render components
  renderMetrics(tasks);
  renderTasksTable(tasks);
  renderKeyMoments(moments);
  renderNotesAndMemo(m);
  renderSummary(m);
  renderTranscript(m.dialogue || []);
  renderSedCard(m);
}

// --------------------------------------------------------------------------
// TAB 1: TASKS
// --------------------------------------------------------------------------
function renderMetrics(tasks) {
  const total = tasks.length;
  const high = tasks.filter(t => (t.priority || '').toLowerCase().includes('высок')).length;
  const progress = tasks.filter(t => (t.status || '').toLowerCase().includes('работ')).length;
  const done = tasks.filter(t => (t.status || '').toLowerCase().includes('выполн')).length;
  const overdue = tasks.filter(t => (t.status || '').toLowerCase().includes('просроч') || (t.priority || '').toLowerCase().includes('сроч')).length;

  document.getElementById('mTotalTasks').textContent = total;
  document.getElementById('mHighTasks').textContent = high;
  document.getElementById('mProgressTasks').textContent = progress;
  document.getElementById('mDoneTasks').textContent = done;
  document.getElementById('mOverdueTasks').textContent = overdue;
}

function renderTasksTable(tasks) {
  const tbody = document.getElementById('tasksTableBody');
  tbody.innerHTML = '';

  let filtered = tasks;
  if (currentFilter === 'high') {
    filtered = tasks.filter(t => (t.priority || '').toLowerCase().includes('высок'));
  } else if (currentFilter === 'in_progress') {
    filtered = tasks.filter(t => (t.status || '').toLowerCase().includes('работ'));
  } else if (currentFilter === 'done') {
    filtered = tasks.filter(t => (t.status || '').toLowerCase().includes('выполн'));
  } else if (currentFilter === 'overdue') {
    filtered = tasks.filter(t => (t.status || '').toLowerCase().includes('просроч') || (t.priority || '').toLowerCase().includes('сроч'));
  }

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty-state"><i class="fa-solid fa-check-double empty-icon"></i><p>Нет поручений по выбранному фильтру.</p></td></tr>`;
    return;
  }

  filtered.forEach(t => {
    const tr = document.createElement('tr');
    let pClass = 'badge-normal';
    if ((t.priority || '').toLowerCase().includes('высок')) pClass = 'badge-high';
    else if ((t.priority || '').toLowerCase().includes('сред')) pClass = 'badge-mid';

    tr.innerHTML = `
      <td style="text-align:center;font-weight:700;color:var(--text-dim)">${t.id}</td>
      <td>
        <div class="task-title">${escapeHtml(t.task)}</div>
        <div style="font-size:0.75rem;color:var(--text-muted);margin-top:2px;">${escapeHtml(t.department || '')}</div>
      </td>
      <td>
        <span class="assignee-badge"><i class="fa-regular fa-user"></i> ${escapeHtml(t.assignee)}</span>
      </td>
      <td>
        <span class="deadline-tag"><i class="fa-regular fa-clock"></i> ${escapeHtml(t.deadline || 'По графику')}</span>
      </td>
      <td>
        <span class="badge ${pClass}">${escapeHtml(t.priority || 'Обычный')}</span>
      </td>
      <td>
        <select class="status-select" onchange="onTaskStatusChange(${t.id}, this.value)">
          <option value="В работе" ${t.status === 'В работе' ? 'selected' : ''}>В работе</option>
          <option value="Выполнено" ${t.status === 'Выполнено' ? 'selected' : ''}>Выполнено</option>
          <option value="На проверке" ${t.status === 'На проверке' ? 'selected' : ''}>На проверке</option>
          <option value="Просрочено" ${t.status === 'Просрочено' ? 'selected' : ''}>Просрочено</option>
        </select>
      </td>
      <td style="text-align:center">
        <button class="btn btn-icon btn-xs" title="Посмотреть цитату" onclick="showQuoteModal(${t.id})">
          <i class="fa-solid fa-quote-right"></i>
        </button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function filterTasks(type) {
  currentFilter = type;
  if (currentMeetingData) {
    renderTasksTable(currentMeetingData.tasks || []);
  }
}

async function onTaskStatusChange(taskId, newStatus) {
  if (!currentMeetingData) return;
  try {
    await callApi('update_task_status', {
      meeting_id: currentMeetingData.id,
      task_id: taskId,
      status: newStatus
    });
    const task = currentMeetingData.tasks.find(t => t.id === taskId);
    if (task) task.status = newStatus;
    renderMetrics(currentMeetingData.tasks);
    updateAiThought(`Статус поручения №${taskId} изменен на: «${newStatus}».`);
  } catch (err) {
    alert("Не удалось обновить статус: " + err.message);
  }
}

function showQuoteModal(taskId) {
  if (!currentMeetingData) return;
  const task = currentMeetingData.tasks.find(t => t.id === taskId);
  if (!task) return;

  document.getElementById('quoteModalText').textContent = `«${task.source_quote || task.task}»`;
  document.getElementById('quoteModalAssignee').innerHTML = `<b>Ответственный:</b> ${task.assignee} | <b>Срок:</b> ${task.deadline}`;
  document.getElementById('quoteModal').style.display = 'flex';
}

function closeQuoteModal() {
  document.getElementById('quoteModal').style.display = 'none';
}

// --------------------------------------------------------------------------
// TAB 2: KEY MOMENTS & HIGHLIGHTS
// --------------------------------------------------------------------------
function renderKeyMoments(moments) {
  const container = document.getElementById('momentsTimeline');
  container.innerHTML = '';

  if (!moments || moments.length === 0) {
    container.innerHTML = '<div class="empty-state"><p>Ключевые моменты не зафиксированы.</p></div>';
    return;
  }

  moments.forEach(m => {
    const card = document.createElement('div');
    card.className = 'moment-card';

    let badgeClass = 'badge-normal';
    if (m.type.includes('Риск') || m.type.includes('Инцидент')) badgeClass = 'badge-high';
    else if (m.type.includes('Срыв')) badgeClass = 'badge-high';
    else if (m.type.includes('Решение')) badgeClass = 'badge-success';
    else if (m.type.includes('Финанс')) badgeClass = 'badge-mid';

    card.innerHTML = `
      <div class="moment-bullet"></div>
      <div class="moment-header">
        <div style="display:flex;align-items:center;gap:8px">
          <span class="badge ${badgeClass}">${escapeHtml(m.type)}</span>
          <span class="moment-time"><i class="fa-regular fa-clock"></i> ${escapeHtml(m.timestamp || '00:00')}</span>
        </div>
        <span style="font-size:0.75rem;color:var(--text-dim);font-weight:700">${escapeHtml(m.importance || 'Высокая')}</span>
      </div>
      <div class="moment-title">${escapeHtml(m.title)}</div>
      <div class="moment-desc">${escapeHtml(m.description)}</div>
    `;
    container.appendChild(card);
  });
}

// --------------------------------------------------------------------------
// TAB 3: NOTES & MEMOS
// --------------------------------------------------------------------------
async function renderNotesAndMemo(m) {
  // 1. Render Memo
  const memoEl = document.getElementById('memoBodyContent');
  const memo = m.executive_memo;
  if (memo) {
    let actionsHtml = '';
    (memo.urgent_actions || []).forEach(a => actionsHtml += `<li>${escapeHtml(a)}</li>`);

    let metricsHtml = '';
    (memo.key_metrics || []).forEach(met => metricsHtml += `<li>${escapeHtml(met)}</li>`);

    let risksHtml = '';
    (memo.risks_alert || []).forEach(r => risksHtml += `<li>${escapeHtml(r)}</li>`);

    memoEl.innerHTML = `
      <div class="memo-block">
        <div class="memo-block-title"><i class="fa-solid fa-triangle-exclamation"></i> Срочный контроль (ближайшие 48 часов):</div>
        <div class="memo-urgent-box"><ul>${actionsHtml}</ul></div>
      </div>
      <div class="memo-block">
        <div class="memo-block-title"><i class="fa-solid fa-chart-pie"></i> Ключевые показатели и цифры:</div>
        <div class="memo-metrics-box"><ul>${metricsHtml}</ul></div>
      </div>
      <div class="memo-block">
        <div class="memo-block-title"><i class="fa-solid fa-shield-virus"></i> Выявленные угрозы:</div>
        <div class="topic-risk"><ul>${risksHtml}</ul></div>
      </div>
    `;
  } else {
    memoEl.innerHTML = '<p class="empty-state">Памятка формируется автоматически по итогам совещания.</p>';
  }

  // 2. Load Notes
  loadNotesForMeeting(m.id);
}

async function loadNotesForMeeting(meetingId) {
  try {
    const notes = await callApi('get_notes', { meeting_id: meetingId });
    const feed = document.getElementById('notesFeed');
    feed.innerHTML = '';
    if (!notes || notes.length === 0) {
      feed.innerHTML = '<div style="font-size:0.75rem;color:var(--text-dim)">Заметок пока нет. Добавьте первую!</div>';
      return;
    }
    notes.forEach(n => {
      const item = document.createElement('div');
      item.className = 'note-item';

      let catClass = 'note-cat-default';
      if (n.category === 'Срочно') catClass = 'note-cat-urgent';
      else if (n.category === 'Контроль') catClass = 'note-cat-control';
      else if (n.category === 'Вопрос') catClass = 'note-cat-question';

      item.innerHTML = `
        <div class="note-top">
          <span class="note-cat ${catClass}">${escapeHtml(n.category)}</span>
          <span class="note-time"><i class="fa-regular fa-clock"></i> ${escapeHtml(n.timestamp || '00:00')}</span>
        </div>
        <div class="note-text">${escapeHtml(n.text)}</div>
        <div style="font-size:0.7rem;color:var(--text-dim);margin-top:4px">${escapeHtml(n.author || 'Секретарь')} • ${escapeHtml(n.created_at || '')}</div>
      `;
      feed.appendChild(item);
    });
  } catch (e) {
    console.error("Error loading notes:", e);
  }
}

async function addNewNote() {
  if (!currentMeetingData) return;
  const text = document.getElementById('newNoteText').value.trim();
  const category = document.getElementById('newNoteCategory').value;
  if (!text) return;

  const timerText = document.getElementById('recTimer').textContent || "00:00";
  try {
    await callApi('add_note', {
      meeting_id: currentMeetingData.id,
      text: text,
      timestamp: timerText,
      category: category,
      author: "Секретарь совещания"
    });
    document.getElementById('newNoteText').value = '';
    loadNotesForMeeting(currentMeetingData.id);
    updateAiThought(`Заметка зафиксирована: [${category}] ${text}`);
  } catch (err) {
    alert("Ошибка добавления заметки: " + err.message);
  }
}

// --------------------------------------------------------------------------
// TAB 4: SUMMARY & DOCUMENT
// --------------------------------------------------------------------------
function renderSummary(m) {
  document.getElementById('sumDocDatePlace').textContent = `${m.date || '2026 г.'} | г. Астана`;

  const agendaEl = document.getElementById('sumAgendaList');
  agendaEl.innerHTML = '';
  (m.agenda || []).forEach(item => {
    const li = document.createElement('li');
    li.textContent = item;
    agendaEl.appendChild(li);
  });

  const partEl = document.getElementById('sumParticipantsList');
  partEl.innerHTML = '';
  (m.participants || []).forEach(p => {
    const tag = document.createElement('div');
    tag.className = 'part-tag';
    tag.innerHTML = `<b>${escapeHtml(p.name)}</b> <span class="part-role">${escapeHtml(p.role || '')}</span>`;
    partEl.appendChild(tag);
  });

  const topicContainer = document.getElementById('sumTopicsContainer');
  topicContainer.innerHTML = '';
  (m.summary || []).forEach(block => {
    const box = document.createElement('div');
    box.className = 'topic-box';

    let kpList = '';
    (block.key_points || []).forEach(kp => {
      kpList += `<li class="topic-kp">${escapeHtml(kp)}</li>`;
    });

    let risks = '';
    if (block.risks && block.risks.length > 0) {
      risks = `<div class="topic-risk"><b>⚠️ Риски:</b> ${escapeHtml(block.risks.join('; '))}</div>`;
    }

    box.innerHTML = `
      <div class="topic-title">${escapeHtml(block.topic)}</div>
      <ul style="padding-left:16px">${kpList}</ul>
      ${risks}
    `;
    topicContainer.appendChild(box);
  });
}

// --------------------------------------------------------------------------
// TAB 5: TRANSCRIPT & DIARIZATION
// --------------------------------------------------------------------------
function renderTranscript(dialogue) {
  const container = document.getElementById('dialogueChat');
  container.innerHTML = '';

  if (!dialogue || dialogue.length === 0) {
    container.innerHTML = `<div class="empty-state"><p>Стенограмма диалога пуста.</p></div>`;
    return;
  }

  dialogue.forEach(d => {
    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble';
    bubble.innerHTML = `
      <div class="chat-header">
        <div class="chat-speaker">
          <i class="fa-solid fa-user"></i> ${escapeHtml(d.speaker)}
          <span class="chat-speaker-role">${escapeHtml(d.role || '')}</span>
        </div>
        <span class="chat-time">${escapeHtml(d.timestamp || '00:00')}</span>
      </div>
      <div class="chat-text">${escapeHtml(d.text)}</div>
    `;
    container.appendChild(bubble);
  });
}

function filterTranscript() {
  const query = document.getElementById('transcriptSearch').value.toLowerCase();
  const bubbles = document.querySelectorAll('.chat-bubble');
  bubbles.forEach(b => {
    const text = b.textContent.toLowerCase();
    b.style.display = text.includes(query) ? 'block' : 'none';
  });
}

// --------------------------------------------------------------------------
// TAB 6: SED CARD
// --------------------------------------------------------------------------
function renderSedCard(m) {
  const regNum = `ПР-2026-${m.id || '001'}`;
  const grid = document.getElementById('sedCardFields');
  grid.innerHTML = `
    <div class="sed-field-row">
      <div class="sed-field-label">Регистрационный номер:</div>
      <div class="sed-field-val">${regNum}</div>
    </div>
    <div class="sed-field-row">
      <div class="sed-field-label">Дата регистрации:</div>
      <div class="sed-field-val">${m.date || '23.09.2026'}</div>
    </div>
    <div class="sed-field-row">
      <div class="sed-field-label">Организация:</div>
      <div class="sed-field-val">${m.company || 'АО «Самрук-Қазына Өңдеу»'}</div>
    </div>
    <div class="sed-field-row">
      <div class="sed-field-label">Председатель:</div>
      <div class="sed-field-val">${m.leader || 'Руководитель правления'}</div>
    </div>
    <div class="sed-field-row">
      <div class="sed-field-label">Статус документа:</div>
      <div class="sed-field-val"><span class="badge badge-success">Зарегистрирован в СЭД</span></div>
    </div>
    <div class="sed-field-row">
      <div class="sed-field-label">Уровень доступа:</div>
      <div class="sed-field-val">Для служебного пользования (ДСП)</div>
    </div>
  `;

  const assignContainer = document.getElementById('sedAssignmentsContainer');
  assignContainer.innerHTML = '';
  (m.tasks || []).forEach(t => {
    const card = document.createElement('div');
    card.className = 'sed-assign-card';
    card.innerHTML = `
      <div>
        <div style="font-weight:700;font-size:0.86rem;color:var(--text-main)">${escapeHtml(t.task)}</div>
        <div style="font-size:0.75rem;color:var(--text-muted);margin-top:2px;">
          Исполнитель: <b>${escapeHtml(t.assignee)}</b> | Контрольный срок: <b>${escapeHtml(t.deadline)}</b>
        </div>
      </div>
      <div>
        <span class="badge badge-primary">Код СЭД: /П-${t.id}</span>
      </div>
    `;
    assignContainer.appendChild(card);
  });
}

// --------------------------------------------------------------------------
// EXPORT ACTIONS
// --------------------------------------------------------------------------
async function exportDocx() {
  if (!currentMeetingData) return;
  updateAiThought("Генерация официального Word (.docx) документа протокола...");
  try {
    const res = await callApi('export_docx', { meeting_id: currentMeetingData.id });
    if (res && res.filepath) {
      alert(`Протокол Word успешно сохранен:\n${res.filepath}`);
      updateAiThought(`Документ Word сформирован: ${res.filepath}`);
    }
  } catch (err) {
    alert("Ошибка экспорта DOCX: " + err.message);
  }
}

async function exportPdf() {
  if (!currentMeetingData) return;
  updateAiThought("Генерация официального PDF протокола...");
  try {
    const res = await callApi('export_pdf', { meeting_id: currentMeetingData.id });
    if (res && res.filepath) {
      alert(`Протокол PDF успешно сохранен:\n${res.filepath}`);
      updateAiThought(`Документ PDF сформирован: ${res.filepath}`);
    }
  } catch (err) {
    alert("Ошибка экспорта PDF: " + err.message);
  }
}

async function exportSedJson() {
  if (!currentMeetingData) return;
  updateAiThought("Экспорт регистрационной карточки в СЭД (Documentolog/1C)...");
  try {
    const res = await callApi('export_sed', { meeting_id: currentMeetingData.id });
    if (res && res.filepath) {
      alert(`Пакет СЭД успешно выгружен:\n${res.filepath}`);
      updateAiThought(`Пакет СЭД сформирован: ${res.filepath}`);
    }
  } catch (err) {
    alert("Ошибка экспорта СЭД: " + err.message);
  }
}

// --------------------------------------------------------------------------
// HISTORY LIST
// --------------------------------------------------------------------------
async function loadHistoryList() {
  try {
    const list = await callApi('list_meetings');
    const container = document.getElementById('historyList');
    container.innerHTML = '';
    if (!list || list.length === 0) {
      container.innerHTML = '<div style="font-size:0.75rem;color:var(--text-dim)">История пуста</div>';
      return;
    }

    let filtered = list;
    if (currentCompanyFilter !== 'all') {
      filtered = list.filter(item => item.company_id === currentCompanyFilter);
    }

    if (filtered.length === 0) {
      container.innerHTML = '<div style="font-size:0.75rem;color:var(--text-dim)">Нет совещаний по этой компании</div>';
      return;
    }

    filtered.forEach(item => {
      const el = document.createElement('div');
      el.className = 'history-item';
      if (currentMeetingData && currentMeetingData.id === item.id) {
        el.classList.add('active');
      }
      el.onclick = () => openDemoMeeting(item.id);
      el.innerHTML = `
        <div class="history-item-title">${escapeHtml(item.title)}</div>
        <div class="history-item-sub">
          <span>${escapeHtml(item.date || '')}</span>
          <span style="color:#38bdf8;font-weight:700">${item.tasks_count} поручений</span>
        </div>
      `;
      container.appendChild(el);
    });
  } catch (e) {
    console.error("Failed to load history list:", e);
  }
}

// --------------------------------------------------------------------------
// TABS & UTILS
// --------------------------------------------------------------------------
function switchTab(tabName) {
  document.querySelectorAll('.protocol-tabs button').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach(c => c.classList.remove('active'));

  event.currentTarget.classList.add('active');
  const target = document.getElementById('tabContent' + tabName.charAt(0).toUpperCase() + tabName.slice(1));
  if (target) target.classList.add('active');
}

function updateAiThought(thought) {
  const el = document.getElementById('aiThoughtText');
  if (el) el.textContent = thought;
}

function onEngineChange() {
  const eng = document.getElementById('engineSelect').value;
  if (eng === 'codex-astra') {
    updateAiThought("Подключен локальный агент Codex с моделью Astra для извлечения поручений.");
  } else if (eng === 'offline') {
    updateAiThought("Активирован On-Premise автономный контур (работа без интернета).");
  } else {
    updateAiThought("Выбран высокоточный движок OpenAI GPT-4o & Whisper.");
  }
}

function openSettingsModal() {
  document.getElementById('settingsModal').style.display = 'flex';
}

function closeSettingsModal() {
  document.getElementById('settingsModal').style.display = 'none';
}

async function saveSettings() {
  const apiKey = document.getElementById('settingsApiKey').value.trim();
  if (apiKey) {
    await callApi('save_settings', { api_key: apiKey });
    updateAiThought("Настройки успешно сохранены в .env файл.");
  }
  closeSettingsModal();
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function toggleProtocolPanel() {
  const panel = document.getElementById('protocolPanel');
  if (panel) {
    panel.classList.toggle('open');
  }
}
