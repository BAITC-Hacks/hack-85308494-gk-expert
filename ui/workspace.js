/* Current session, explicit history, independent live/file workers and real playback FFT. */
let workspaceTab = 'work';
let streamView = 'live';
let selectedSource = 'mic';
const sourceState = {mic: {enabled: true, locked: false, visible: true}, sys: {enabled: true, locked: false, visible: true}};
let selectedJobId = null;
let lastLive = {state: 'idle', segments: []};
let lastJob = null;
let jobsSnapshot = [];
let pollRunning = false;
let recordTransition = false;
let uploadInProgress = false;
let audioContext = null;
let activePlayback = null;
const audioNodes = new WeakMap();

function clockText(value) {
  const seconds = Math.max(0, Math.floor(value || 0));
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}
function savedTime(value) {
  if (!value) return 'Время не указано';
  const date = new Date(value.replace(' ', 'T'));
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('ru-RU', {dateStyle: 'short', timeStyle: 'medium'});
}

async function initWorkspace() {
  document.getElementById('protocolPanel').classList.remove('open');
  currentMeetingData = null;
  await initAudioDevices();
  loadCompanies();
  updateSourceControls();
  setupPlayer(document.getElementById('monitorAudio'));
  setupPlayer(document.getElementById('archiveAudio'));
  startWaveformAnimation();
  setInterval(pollWorkspace, 650);
  updateAiThought('Новая сессия. Начните запись или загрузите аудио; сохранённые записи доступны в истории.');
}

function showWorkspaceTab(tab) {
  workspaceTab = tab;
  document.getElementById('workList').hidden = tab !== 'work';
  document.getElementById('historyList').hidden = tab !== 'history';
  document.getElementById('workTabButton').classList.toggle('active', tab === 'work');
  document.getElementById('historyTabButton').classList.toggle('active', tab === 'history');
  if (tab === 'history') loadHistoryList();
}

async function loadHistoryList() {
  if (workspaceTab !== 'history') return;
  try {
    const list = await callApi('list_meetings');
    const container = document.getElementById('historyList');
    container.replaceChildren();
    const filtered = list.filter(item => currentCompanyFilter === 'all' || item.company_id === currentCompanyFilter);
    if (!filtered.length) container.innerHTML = '<div class="empty-state">Сохранённых записей нет.</div>';
    for (const item of filtered) {
      const button = document.createElement('button');
      button.className = 'history-item';
      button.innerHTML = `<strong>${escapeHtml(item.title || 'Совещание')}</strong><span>Запись сохранена: ${escapeHtml(savedTime(item.saved_at || item.created_at))}</span><small>${escapeHtml(item.audio_filename || '')} · ${clockText(item.duration)} · ${item.tasks_count || 0} поручений</small>`;
      button.onclick = () => openDemoMeeting(item.id);
      container.appendChild(button);
    }
  } catch (error) { updateAiThought(error.message); }
}

async function openDemoMeeting(id) {
  try {
    const meeting = await callApi('get_meeting', {id});
    if (!meeting) throw new Error('Запись не найдена');
    renderMeeting(meeting);
    updateAiThought(`Открыта запись от ${savedTime(meeting.recording_saved_at || meeting.saved_at || meeting.created_at)}`);
  } catch (error) { updateAiThought(error.message); }
}

function showStreamView(view) {
  streamView = view;
  for (const name of ['live', 'file']) {
    document.getElementById(name + 'View').hidden = view !== name;
    document.getElementById(name + 'ViewButton').classList.toggle('active', view === name);
  }
}

function selectSource(source) {
  selectedSource = source;
  document.querySelectorAll('[data-source]').forEach(row => row.classList.toggle('selected', row.dataset.source === source));
}
function updateSourceControls() {
  for (const key of ['mic', 'sys']) {
    const state = sourceState[key];
    const row = document.querySelector(`[data-source="${key}"]`);
    row.hidden = !state.visible;
    row.classList.toggle('source-muted', !state.enabled);
    const eye = document.getElementById(key + 'Enabled');
    eye.setAttribute('aria-pressed', String(state.enabled));
    eye.innerHTML = `<i class="fa-solid fa-${state.enabled ? 'eye' : 'eye-slash'}"></i>`;
    const lock = document.getElementById(key + 'Locked');
    lock.setAttribute('aria-pressed', String(state.locked));
    lock.innerHTML = `<i class="fa-solid fa-${state.locked ? 'lock' : 'lock-open'}"></i>`;
    document.getElementById(key === 'mic' ? 'micDeviceSelect' : 'loopbackDeviceSelect').disabled = state.locked || isRecording;
  }
  document.getElementById('sourceStateLabel').textContent = isRecording ? 'Устройства заняты записью' : 'Глаз — звук, замок — выбор';
  document.getElementById('btnStartRec').disabled = recordTransition || isRecording || !Object.values(sourceState).some(s => s.enabled && s.visible);
  document.getElementById('btnStopRec').disabled = recordTransition || !isRecording;
  document.getElementById('btnPauseRec').disabled = recordTransition || !isRecording;
  document.getElementById('audioTrackSelect').disabled = isRecording;
  document.getElementById('playbackNotice').textContent = isRecording && sourceState.sys.enabled ? 'Прослушиваемый звук также попадёт в запись системного канала.' : '';
}
async function toggleSource(source) {
  const next = !sourceState[source].enabled;
  try {
    await callApi('set_source_muted', {source, muted: !next});
    sourceState[source].enabled = next;
    updateSourceControls();
  } catch (error) { updateAiThought(error.message); }
}
function lockSource(source) { sourceState[source].locked = !sourceState[source].locked; updateSourceControls(); }
function openSourcePicker() { document.getElementById('sourcePicker').hidden = !document.getElementById('sourcePicker').hidden; }
async function addSource(source) {
  sourceState[source].visible = true;
  if (!sourceState[source].enabled) await toggleSource(source);
  document.getElementById('sourcePicker').hidden = true;
  selectSource(source);
  updateSourceControls();
}
async function removeSource() {
  if (sourceState[selectedSource].locked) { updateAiThought('Сначала снимите замок с источника.'); return; }
  if (sourceState[selectedSource].enabled) await toggleSource(selectedSource);
  sourceState[selectedSource].visible = false;
  updateSourceControls();
}
function moveSource(direction) {
  if (sourceState[selectedSource].locked) { updateAiThought('Сначала снимите замок с источника.'); return; }
  const row = document.querySelector(`[data-source="${selectedSource}"]`);
  const sibling = direction < 0 ? row.previousElementSibling : row.nextElementSibling;
  if (sibling?.dataset.source) row.parentElement.insertBefore(direction < 0 ? row : sibling, direction < 0 ? sibling : row);
}
async function refreshSources() {
  if (isRecording) { updateAiThought('Обновление устройств доступно после остановки записи.'); return; }
  devicesLoaded = false;
  await initAudioDevices();
  updateSourceControls();
}
function onTrackModeChange() {
  const mode = document.getElementById('audioTrackSelect').value;
  sourceState.mic.enabled = mode !== 'system';
  sourceState.sys.enabled = mode !== 'mic';
  for (const source of ['mic', 'sys']) sourceState[source].visible = true;
  updateSourceControls();
}

async function toggleRecording() {
  if (isRecording || recordTransition) return;
  recordTransition = true;
  updateSourceControls();
  try {
    const mic = sourceState.mic.enabled && sourceState.mic.visible;
    const sys = sourceState.sys.enabled && sourceState.sys.visible;
    if (!mic && !sys) throw new Error('Включите хотя бы один источник звука');
    const deviceIndex = id => /^\d+$/.test(document.getElementById(id).value) ? Number(document.getElementById(id).value) : null;
    await callApi('start_recording', {mode: mic && sys ? 'mix' : mic ? 'mic' : 'system', mic_index: deviceIndex('micDeviceSelect'),
      loopback_index: deviceIndex('loopbackDeviceSelect'), mic_muted: !mic, sys_muted: !sys});
    isRecording = true;
    isPaused = false;
    lastLive = {state: 'listening', segments: []};
    document.getElementById('liveTranscript').innerHTML = '<div class="empty-state">Запись идёт. Ожидание первого фрагмента…</div>';
    document.getElementById('recBadge').style.display = 'inline-block';
    showWorkspaceTab('work');
    showStreamView('live');
    document.getElementById('protocolPanel').classList.remove('open');
    updateAiThought('Идёт запись и параллельное локальное распознавание.');
  } catch (error) { updateAiThought('Не удалось начать запись: ' + error.message); }
  finally { recordTransition = false; updateSourceControls(); }
}
async function stopRecording() {
  if (!isRecording || recordTransition) return;
  recordTransition = true;
  updateSourceControls();
  try {
    const result = await callApi('stop_recording_job');
    selectedJobId = result.job.id;
    updateAiThought('Запись сохранена. Итоговый протокол готовится в отдельном потоке.');
    showStreamView('file');
  } catch (error) { updateAiThought('Ошибка сохранения: ' + error.message); }
  finally {
    isRecording = false; isPaused = false; recordTransition = false; latestAudioLevel = 0;
    document.getElementById('recBadge').style.display = 'none';
    updateSourceControls();
  }
}
async function togglePauseRecording() {
  if (!isRecording) return;
  try {
    await callApi('pause_recording');
    isPaused = !isPaused;
    document.getElementById('btnPauseRec').textContent = isPaused ? 'Продолжить запись' : 'Пауза';
  } catch (error) { updateAiThought(error.message); }
}
function triggerFileInput() { document.getElementById('audioFileInput').click(); }
async function handleFileSelected(event) {
  const file = event.target.files[0];
  if (!file || uploadInProgress) return;
  uploadInProgress = true;
  try {
    updateAiThought('Загрузка: ' + file.name);
    const data = new FormData(); data.append('audio', file);
    const response = await fetch('/api/upload_audio', {method: 'POST', body: data});
    const result = await response.json();
    if (result.error) throw new Error(result.error);
    selectedJobId = result.job.id;
    showWorkspaceTab('work'); showStreamView('file');
    document.getElementById('protocolPanel').classList.remove('open');
    updateAiThought('Файл поставлен в очередь. Запись и live могут работать одновременно.');
    await pollWorkspace();
  } catch (error) { updateAiThought('Ошибка загрузки: ' + error.message); }
  finally { uploadInProgress = false; event.target.value = ''; }
}

function renderStream(id, segments) {
  const container = document.getElementById(id);
  const signature = segments.map(s => `${s.start}:${s.text}`).join('|');
  if (container.dataset.signature === signature) return;
  container.dataset.signature = signature;
  if (!segments.length) { container.innerHTML = '<div class="empty-state">Ожидание распознавания…</div>'; return; }
  const atBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 60;
  container.innerHTML = segments.map(s => `<div class="live-line"><time>${clockText(s.start)}</time><span>${escapeHtml(s.text)}</span></div>`).join('');
  if (atBottom) container.scrollTop = container.scrollHeight;
}
function updateChunkControls(kind, chunk) {
  document.getElementById('listen' + (kind === 'live' ? 'Live' : 'File') + 'Chunk').disabled = !chunk;
  document.getElementById(kind + 'ChunkRange').textContent = chunk ? `${clockText(chunk.start)} — ${clockText(chunk.end)}` : '';
}
function renderJobs() {
  const container = document.getElementById('workList');
  if (!jobsSnapshot.length && !isRecording) return;
  container.replaceChildren();
  if (isRecording) {
    const button = document.createElement('button'); button.className = 'history-item live-job';
    button.textContent = '● Живая запись · ' + (isPaused ? 'пауза' : 'идёт'); button.onclick = () => showStreamView('live'); container.appendChild(button);
  }
  for (const job of [...jobsSnapshot].reverse()) {
    const button = document.createElement('button'); button.className = 'history-item' + (job.id === selectedJobId ? ' active' : '');
    button.innerHTML = `<strong>${escapeHtml(job.name)}</strong><span>${escapeHtml(job.stage)}</span><small>${escapeHtml(savedTime(job.created_at))}</small>`;
    button.onclick = () => { selectedJobId = job.id; lastJob = null; delete document.getElementById('fileTranscript').dataset.signature; showStreamView('file'); pollWorkspace(); };
    container.appendChild(button);
  }
}
async function pollWorkspace() {
  if (pollRunning) return;
  pollRunning = true;
  try {
    const [status, live, jobs] = await Promise.all([callApi('get_recording_status'), callApi('get_live_status'), callApi('get_jobs')]);
    isRecording = status.is_recording; isPaused = status.is_paused;
    document.getElementById('recBadge').style.display = isRecording ? 'inline-block' : 'none';
    document.getElementById('btnPauseRec').textContent = isPaused ? 'Продолжить запись' : 'Пауза';
    if (status.error) updateAiThought(status.error);
    latestAudioLevel = Math.max(status.mic_level || 0, status.system_level || 0);
    document.getElementById('recTimer').textContent = clockText(status.elapsed_seconds);
    for (const [kind, level] of [['mic', status.mic_level], ['sys', status.system_level]]) {
      document.getElementById(kind + 'VuBar').style.height = `${Math.min(100, (level || 0) * 100)}%`;
      document.getElementById(kind + 'DbVal').textContent = level > 0 ? `${Math.round(20 * Math.log10(level))} dB` : '−∞ dB';
    }
    lastLive = live;
    if (live.state !== 'idle') {
      document.getElementById('liveStage').textContent = live.error || live.stage;
      document.getElementById('liveLag').textContent = isRecording ? `Ожидает распознавания: ${live.lag_seconds || 0} с` : '';
      renderStream('liveTranscript', live.segments || []); updateChunkControls('live', live.current_chunk);
    }
    jobsSnapshot = jobs; renderJobs();
    if (selectedJobId) {
      lastJob = await callApi('get_job', {id: selectedJobId});
      document.getElementById('fileStage').textContent = lastJob.error || lastJob.stage;
      document.getElementById('fileProgress').textContent = `${clockText(lastJob.completed_seconds)} / ${clockText(lastJob.total_seconds)}`;
      renderStream('fileTranscript', lastJob.segments || []); updateChunkControls('file', lastJob.current_chunk);
      const openButton = document.getElementById('openFinishedMeeting');
      openButton.hidden = lastJob.state !== 'done';
      openButton.onclick = () => openDemoMeeting(lastJob.meeting_id);
    }
    setProcessing(jobs.some(j => j.state === 'running'));
    document.getElementById('liveStatusPill').textContent = isRecording ? (isPaused ? 'Запись на паузе' : '● Запись + live') : isProcessing ? 'Обработка файла' : 'Готов';
    updateSourceControls();
  } catch (error) { updateAiThought('Связь с приложением: ' + error.message); }
  finally { pollRunning = false; }
}
function setProcessing(active) { isProcessing = active; document.body.classList.toggle('processing', active); }

function setupPlayer(player) {
  player.addEventListener('play', async () => {
    for (const other of document.querySelectorAll('audio')) if (other !== player) other.pause();
    if (!audioContext) audioContext = new (window.AudioContext || window.webkitAudioContext)();
    if (!audioNodes.has(player)) {
      const source = audioContext.createMediaElementSource(player);
      const analyser = audioContext.createAnalyser(); analyser.fftSize = 128; analyser.smoothingTimeConstant = 0.65;
      source.connect(analyser); analyser.connect(audioContext.destination);
      audioNodes.set(player, {analyser, bins: new Uint8Array(analyser.frequencyBinCount)});
    }
    await audioContext.resume(); activePlayback = player;
  });
  player.addEventListener('pause', () => { if (activePlayback === player) activePlayback = null; });
  player.addEventListener('ended', () => { if (activePlayback === player) activePlayback = null; });
}
async function listenCurrentChunk(kind) {
  const chunk = (kind === 'live' ? lastLive : lastJob)?.current_chunk;
  if (!chunk) return;
  const player = document.getElementById('monitorAudio');
  document.getElementById('playbackLabel').textContent = `Прослушивание ${clockText(chunk.start)} — ${clockText(chunk.end)}`;
  player.src = chunk.audio_url;
  try { await player.play(); } catch (error) { updateAiThought('Не удалось воспроизвести фрагмент: ' + error.message); }
}
function configureArchiveAudio(meeting) {
  const player = document.getElementById('archiveAudio');
  player.pause(); player.removeAttribute('src'); player.hidden = !meeting.audio_url;
  if (meeting.audio_url) player.src = meeting.audio_url;
}

function startWaveformAnimation() {
  const archiveCanvas = document.getElementById('playbackCanvas');
  function paint(target, values) {
    const context = target.getContext('2d'); context.clearRect(0, 0, target.width, target.height);
    const width = target.width / 48;
    for (let i = 0; i < 48; i++) {
      const amplitude = values[i] || 0;
      const height = amplitude > 0.008 ? Math.max(3, amplitude * target.height * 0.85) : 2;
      context.fillStyle = amplitude > 0.008 ? '#32b3ef' : '#353f48';
      context.fillRect(i * width, (target.height - height) / 2, Math.max(1, width - 2), height);
    }
  }
  function draw() {
    let playback = Array(48).fill(0);
    if (activePlayback && !activePlayback.paused && !activePlayback.ended) {
      const state = audioNodes.get(activePlayback);
      if (state) { state.analyser.getByteFrequencyData(state.bins); playback = Array.from(state.bins.slice(0, 48), n => n / 255); }
    }
    const playing = activePlayback && !activePlayback.paused;
    const levels = playing ? playback : Array.from({length: 48}, (_, i) => isRecording && !isPaused ? Math.min(1, latestAudioLevel * 2.8 * Math.exp(-2.2 * ((i - 24) / 24) ** 2)) : 0);
    paint(canvas, levels); paint(archiveCanvas, playback);
    document.getElementById('waveSourceLabel').textContent = playing ? 'Эквалайзер: прослушиваемый фрагмент' : 'Индикаторы входящего звука';
    waveformAnimationId = requestAnimationFrame(draw);
  }
  draw();
}

async function saveExport(format) {
  if (!currentMeetingData) { updateAiThought('Выберите сохранённую запись в истории.'); return; }
  try {
    const result = await callApi('save_export', {meeting_id: currentMeetingData.id, format});
    if (result.cancelled) return;
    if (result.download_url) {
      const link = document.createElement('a'); link.href = result.download_url; link.download = result.filename; link.click();
    }
    updateAiThought(result.filepath ? 'Сохранено: ' + result.filepath : 'Документ передан в загрузки браузера: ' + result.filename);
  } catch (error) { updateAiThought('Ошибка сохранения: ' + error.message); }
}
function exportDocx() { return saveExport('docx'); }
function exportPdf() { return saveExport('pdf'); }
function exportTranscript() { return saveExport('txt'); }
function exportSedJson() { return saveExport('json'); }
