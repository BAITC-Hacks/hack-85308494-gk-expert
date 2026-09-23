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
  let result;
  if (window.pywebview?.api && typeof window.pywebview.api[method] === 'function') {
    result = await window.pywebview.api[method](params);
  } else {
    const response = await fetch(`/api/${method}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params)
    });
    result = await response.json();
    if (!response.ok && !result?.error) throw new Error(`HTTP ${response.status}`);
  }
  if (result?.error) throw new Error(result.error);
  return result;
}

let isProcessing = false;
window.addEventListener('DOMContentLoaded', () => initWorkspace());

// --------------------------------------------------------------------------
// COMPANIES & GROUPS
// --------------------------------------------------------------------------
async function loadCompanies() {
  try {
    const comps = await callApi('get_companies');
    if (comps && comps.length > 0) {
      companiesList = comps;
      const select = document.getElementById('companyFilterSelect');
      select.innerHTML = '<option value="all">🏢 Все организации</option>';
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
    updateAiThought("Отображаются совещания всех организаций.");
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
      btn.innerHTML = '<i class="fa-solid fa-display"></i> Предпросмотр экрана';
      updateAiThought("Захват экрана отключен. Монитор переключен на визуализатор аудиопотока.");
      return;
    }

    screenStream = await navigator.mediaDevices.getDisplayMedia({
      video: { cursor: "always" },
      audio: false
    });
    await prepareScreenAudio();

    videoEl.srcObject = screenStream;
    videoEl.style.display = 'block';
    canvasEl.style.display = 'none';
    btn.innerHTML = '<i class="fa-solid fa-stop"></i> Отключить захват';
    updateAiThought("Экран выбран. Для записи включён только звук компьютера через WASAPI Loopback; микрофон выключен.");

    screenStream.getVideoTracks()[0].onended = () => {
      videoEl.style.display = 'none';
      canvasEl.style.display = 'block';
      btn.innerHTML = '<i class="fa-solid fa-display"></i> Предпросмотр экрана';
      screenStream = null;
    };
  } catch (err) {
    if (screenStream) screenStream.getTracks().forEach(track => track.stop());
    screenStream = null;
    updateAiThought(err.name === 'NotAllowedError' ? 'Выбор экрана отменён.' : 'Не удалось включить захват экрана: ' + err.message);
  }
}

// --------------------------------------------------------------------------
// AUDIO DEVICES & OBS VU-METERS
// --------------------------------------------------------------------------
let devicesLoadPromise = null;
let devicesLoaded = false;
async function initAudioDevices() {
  if (devicesLoaded) return;
  if (devicesLoadPromise) return devicesLoadPromise;
  devicesLoadPromise = populateAudioDevices();
  try { await devicesLoadPromise; } finally { devicesLoadPromise = null; }
}

async function populateAudioDevices() {
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
    devicesLoaded = true;
  } catch (e) {
    updateAiThought('Не удалось получить аудиоустройства: ' + e.message + '. Загрузка аудиофайлов доступна.');
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
// RENDER MEETING
// --------------------------------------------------------------------------
function renderMeeting(m) {
  currentMeetingData = m;
  document.getElementById('protocolPanel').classList.add('open');
  document.getElementById('protocolWarnings').textContent = (m.warnings || []).join(' ');

  // Header meta & Company tag
  document.getElementById('currentMeetingTitle').textContent = m.title || "Совещание";
  document.getElementById('metaSource').textContent = m.source_label || m.audio_filename || '';
  document.getElementById('metaCompanyTag').textContent = m.company || 'Организация';
  document.getElementById('metaDate').innerHTML = `<i class="fa-regular fa-calendar"></i> ${m.date || 'Текущая дата'}`;
  document.getElementById('metaLeader').innerHTML = `<i class="fa-solid fa-user-tie"></i> ${m.leader || 'Председатель'}`;
  
  const durM = Math.floor((m.audio_duration || 0) / 60);
  const durS = Math.floor((m.audio_duration || 0) % 60);
  document.getElementById('metaDuration').innerHTML = `<i class="fa-solid fa-stopwatch"></i> ${durM} мин ${durS} сек`;

  // Counts
  const tasks = m.tasks || [];
  const moments = m.key_moments || [];
  document.getElementById('tabTasksCount').textContent = m.is_fragment ? 'весь файл' : tasks.length;
  document.getElementById('tabMomentsCount').textContent = m.is_fragment ? 'весь файл' : moments.length;
  updateProtocolScope(m);

  // Render components
  renderMetrics(tasks);
  renderTasksTable(tasks);
  renderKeyMoments(moments);
  renderNotesAndMemo(m);
  renderSummary(m);
  renderTranscript(m.dialogue || []);
  renderSpeakerCards(m);
  renderSedCard(m);
  configureArchiveAudio(m);
  switchTab('transcript');
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
    if (d.speaker_id && (currentMeetingData.speakers || []).some(p => p.id === d.speaker_id)) {
      const edit = document.createElement('button'); edit.className = 'speaker-edit-button';
      edit.innerHTML = '<i class="fa-solid fa-pencil"></i>';
      edit.title = 'Изменить имя говорящего'; edit.setAttribute('aria-label', 'Изменить имя: ' + d.speaker);
      edit.disabled = !!currentMeetingData.is_fragment && !currentMeetingData.full_meeting_id;
      edit.onclick = () => editSpeakerInline(bubble, d.speaker_id);
      bubble.querySelector('.chat-speaker').appendChild(edit);
    }
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
// TABS & UTILS
// --------------------------------------------------------------------------
function switchTab(tabName) {
  if (currentMeetingData?.is_fragment && tabName !== 'transcript') { openFullProtocol(tabName); return; }
  document.querySelectorAll('.protocol-tabs button').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach(c => c.classList.remove('active'));

  const tabNames = ['tasks', 'moments', 'notes', 'summary', 'transcript', 'sed'];
  document.querySelectorAll('.protocol-tabs button')[tabNames.indexOf(tabName)]?.classList.add('active');
  const target = document.getElementById('tabContent' + tabName.charAt(0).toUpperCase() + tabName.slice(1));
  if (target) target.classList.add('active');
}

async function exportTranscript() {
  if (!currentMeetingData) { updateAiThought('Сначала загрузите аудио или выберите совещание.'); return; }
  try {
    const result = await callApi('export_transcript', { meeting_id: currentMeetingData.id });
    alert('Стенограмма сохранена:\n' + result.filepath);
  } catch (err) { updateAiThought(err.message); }
}

function updateAiThought(thought) {
  const el = document.getElementById('aiThoughtText');
  if (el) el.textContent = thought;
}

function onEngineChange() {
  updateAiThought("Активирован On-Premise автономный контур (Faster-Whisper + локальный NLP-экстрактор).");
}

async function openSettingsModal() {
  try {
    const settings = await callApi('get_settings');
    document.getElementById('settingsCompany').value = settings.company || '';
    document.getElementById('settingsPrompt').value = settings.prompt || '';
    document.getElementById('settingsLanguage').value = settings.language || 'auto';
    document.getElementById('settingsParticipants').value = settings.participants || '';
    document.getElementById('settingsSpeakerCount').value = settings.speaker_count || 0;
    document.getElementById('settingsVisualizer').value = settings.visualizer_mode || 'bars';
    document.getElementById('settingsOpacity').value = settings.window_opacity || 100;
    document.getElementById('settingsOpacity').disabled = !settings.window_opacity_supported;
    document.getElementById('opacityHint').textContent = settings.window_opacity_supported
      ? '100% — непрозрачное окно. Меньше значение — сильнее виден рабочий стол за программой.'
      : 'Прозрачность окна доступна в настольном EXE.';
    updateOpacityLabel();
  } catch (err) { updateAiThought(err.message); }
  document.getElementById('settingsModal').style.display = 'flex';
}

function closeSettingsModal() {
  document.getElementById('settingsModal').style.display = 'none';
}

async function saveSettings() {
  const company = document.getElementById('settingsCompany') ? document.getElementById('settingsCompany').value.trim() : '';
  const prompt = document.getElementById('settingsPrompt') ? document.getElementById('settingsPrompt').value.trim() : '';
  const settings = { company, prompt, language: document.getElementById('settingsLanguage').value,
    participants: document.getElementById('settingsParticipants').value, speaker_count: Number(document.getElementById('settingsSpeakerCount').value),
    visualizer_mode: document.getElementById('settingsVisualizer').value, window_opacity: Number(document.getElementById('settingsOpacity').value) };
  try {
    await callApi('save_settings', settings);
    applyAppearance(settings);
    updateAiThought('Настройки сохранены. Оформление будет таким же при следующем запуске.');
    closeSettingsModal();
  } catch (error) { updateAiThought('Не удалось сохранить настройки: ' + error.message); }
}

function updateOpacityLabel() {
  document.getElementById('opacityValue').textContent = document.getElementById('settingsOpacity').value + '%';
}
function applyAppearance(settings) {
  window.qazaqVisualizerMode = settings.visualizer_mode === 'circle' ? 'circle' : 'bars';
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

async function toggleProtocolPanel() {
  if (!currentMeetingData) {
    try { await ensureSelectedMeeting(); } catch (error) { updateAiThought(error.message); }
    return;
  }
  const panel = document.getElementById('protocolPanel');
  if (panel) {
    panel.classList.toggle('open');
  }
}
