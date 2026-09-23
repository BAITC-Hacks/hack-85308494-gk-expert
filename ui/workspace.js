/* Current session, explicit history, independent live/file workers and real playback FFT. */
let workspaceTab = 'work';
let streamView = 'live';
let selectedSource = 'sys';
const sourceState = {mic: {enabled: false, locked: false, visible: true}, sys: {enabled: true, locked: false, visible: true}};
let sourceRevision = 0;
let activeSources = [];
const inputLevels = {mic: 0, sys: 0};
let selectedJobId = null;
let lastLive = {state: 'idle', segments: []};
let lastJob = null;
let jobsSnapshot = [];
let pollRunning = false;
let recordTransition = false;
let uploadInProgress = false;
let audioContext = null;
let activePlayback = null;
let protocolSelection = {kind: 'none', id: null};
let selectionVersion = 0;
let archiveSampleEnd = null;
const audioNodes = new WeakMap();

function invalidateProtocol(kind, id = null) {
  selectionVersion++;
  protocolSelection = {kind, id};
  currentMeetingData = null;
  document.getElementById('protocolPanel').classList.remove('open');
  for (const player of document.querySelectorAll('audio')) { player.pause(); player.removeAttribute('src'); player.load(); }
  document.getElementById('playbackLabel').textContent = 'Прослушивание выбранной записи';
}
function selectFileJob(job) {
  selectedJobId = job.id; lastJob = job;
  invalidateProtocol('job', job.id);
  const container = document.getElementById('fileTranscript');
  delete container.dataset.signature; renderStream('fileTranscript', job.segments || []);
  document.getElementById('fileStage').textContent = job.stage || 'Подготовка';
  document.getElementById('openFinishedMeeting').hidden = job.state !== 'done';
  updateChunkControls('file', job.current_chunk);
  showStreamView('file');
}
async function ensureSelectedMeeting() {
  if (currentMeetingData) return currentMeetingData;
  const version = selectionVersion;
  if (protocolSelection.kind === 'job') {
    const job = await callApi('get_job', {id: protocolSelection.id});
    if (version !== selectionVersion) return null;
    if (job.state !== 'done') { updateAiThought('Протокол выбранного файла ещё готовится. Дождитесь окончания обработки.'); return null; }
    await openDemoMeeting(job.meeting_id, 'job');
    return currentMeetingData;
  }
  if (protocolSelection.kind === 'history' && protocolSelection.id) {
    await openDemoMeeting(protocolSelection.id); return currentMeetingData;
  }
  updateAiThought('Выберите обработанный файл или запись в истории.');
  return null;
}

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
  const audioStatus = await callApi('get_recording_status');
  applySourceStatus(audioStatus);
  selectSource('sys');
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

async function openDemoMeeting(id, kind = 'history') {
  invalidateProtocol(kind, kind === 'job' ? selectedJobId : id);
  const version = selectionVersion;
  try {
    const meeting = await callApi('get_meeting', {id});
    if (version !== selectionVersion) return;
    if (!meeting) throw new Error('Запись не найдена');
    renderMeeting(meeting);
    updateAiThought(`Открыта запись от ${savedTime(meeting.recording_saved_at || meeting.saved_at || meeting.created_at)}`);
  } catch (error) { updateAiThought(error.message); }
}

function showStreamView(view) {
  if (view === 'file' && selectedJobId && (protocolSelection.kind !== 'job' || protocolSelection.id !== selectedJobId)) invalidateProtocol('job', selectedJobId);
  if (view === 'live' && isRecording && protocolSelection.kind !== 'live') invalidateProtocol('live');
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
function applySourceStatus(status) {
  activeSources = status.active_sources || [];
  for (const key of ['mic', 'sys']) {
    if (typeof status.muted?.[key] === 'boolean') sourceState[key].enabled = !status.muted[key];
  }
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
    const absent = isRecording && !activeSources.includes(key) && !state.enabled;
    eye.disabled = recordTransition || !!state.pending || absent;
    const mixer = document.getElementById(key + 'MuteButton');
    const label = key === 'mic' ? 'Микрофон' : 'Системный звук';
    const action = absent ? `${label} выключен. Включите источник перед следующей записью.` : `${state.enabled ? 'Выключить' : 'Включить'} ${key === 'mic' ? 'микрофон' : 'системный звук'}`;
    mixer.disabled = eye.disabled;
    mixer.setAttribute('aria-pressed', String(!state.enabled));
    mixer.setAttribute('aria-label', action); mixer.title = action; eye.title = action;
    mixer.classList.toggle('is-muted', !state.enabled);
    mixer.innerHTML = `<i class="fa-solid fa-${key === 'mic' ? (state.enabled ? 'microphone' : 'microphone-slash') : (state.enabled ? 'volume-high' : 'volume-xmark')}"></i><span>${state.enabled ? 'Включён' : 'Выключен'}</span>`;
    mixer.closest('.mixer-channel').classList.toggle('is-muted', !state.enabled);
    const lock = document.getElementById(key + 'Locked');
    lock.setAttribute('aria-pressed', String(state.locked));
    lock.innerHTML = `<i class="fa-solid fa-${state.locked ? 'lock' : 'lock-open'}"></i>`;
    document.getElementById(key === 'mic' ? 'micDeviceSelect' : 'loopbackDeviceSelect').disabled = state.locked || isRecording;
  }
  document.getElementById('sourceStateLabel').textContent = isRecording ? 'Устройства заняты записью' : 'Глаз — звук, замок — выбор';
  document.getElementById('btnStartRec').disabled = recordTransition || isRecording || Object.values(sourceState).some(s => s.pending) || !Object.values(sourceState).some(s => s.enabled && s.visible);
  document.getElementById('btnStopRec').disabled = recordTransition || !isRecording;
  document.getElementById('btnPauseRec').disabled = recordTransition || !isRecording;
  const mic = sourceState.mic.enabled && sourceState.mic.visible, sys = sourceState.sys.enabled && sourceState.sys.visible;
  document.getElementById('audioTrackSelect').disabled = isRecording || recordTransition || Object.values(sourceState).some(s => s.pending);
  document.getElementById('audioTrackSelect').value = mic && sys ? 'mix' : mic ? 'mic' : sys ? 'system' : 'none';
  document.getElementById('dualTrackBadge').innerHTML = `<i class="fa-solid fa-${mic && sys ? 'layer-group' : mic ? 'microphone' : sys ? 'volume-high' : 'volume-xmark'}"></i> ${mic && sys ? 'MIC + SYS' : mic ? 'МИКРОФОН' : sys ? 'ТОЛЬКО ЗВУК КОМПЬЮТЕРА' : 'ЗВУК ВЫКЛЮЧЕН'}`;
  document.getElementById('playbackNotice').textContent = isRecording && sourceState.sys.enabled ? 'Прослушиваемый звук также попадёт в запись системного канала.' : '';
}
async function toggleSource(source) {
  if (recordTransition || sourceState[source].pending) return;
  try {
    await setSourceEnabled(source, !sourceState[source].enabled);
  } catch (error) { updateAiThought(error.message); }
}
async function setSourceEnabled(source, enabled, quiet = false) {
  const state = sourceState[source];
  if (state.pending) throw new Error('Дождитесь переключения источника звука.');
  state.pending = true; sourceRevision++; updateSourceControls();
  try {
    const result = await callApi('set_source_muted', {source, muted: !enabled});
    state.enabled = !result[source];
    if (!state.enabled) {
      inputLevels[source] = 0;
      document.getElementById(source + 'VuBar').style.height = '0%';
      document.getElementById(source + 'DbVal').textContent = '−∞ dB';
      latestAudioLevel = Math.max(inputLevels.mic, inputLevels.sys);
    }
    if (!quiet) updateAiThought(`${source === 'mic' ? 'Микрофон' : 'Системный звук'} ${state.enabled ? 'включён' : 'выключен'}${state.enabled ? '.' : ': новый звук этого источника не попадает в запись и live-стенограмму.'}`);
  } finally { state.pending = false; sourceRevision++; updateSourceControls(); }
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
async function onTrackModeChange() {
  if (isRecording || recordTransition) return;
  const mode = document.getElementById('audioTrackSelect').value;
  recordTransition = true; updateSourceControls();
  try {
    await setSourceEnabled('mic', mode === 'mic' || mode === 'mix', true);
    await setSourceEnabled('sys', mode === 'system' || mode === 'mix', true);
    for (const source of ['mic', 'sys']) sourceState[source].visible = true;
  } catch (error) { updateAiThought(error.message); }
  finally { recordTransition = false; updateSourceControls(); }
}
async function prepareScreenAudio() {
  if (recordTransition || Object.values(sourceState).some(s => s.pending)) throw new Error('Дождитесь переключения источников и повторите выбор экрана.');
  recordTransition = true; updateSourceControls();
  try {
    await setSourceEnabled('mic', false, true);
    if (!sourceState.sys.enabled) await setSourceEnabled('sys', true, true);
    sourceState.sys.visible = true;
  } finally { recordTransition = false; updateSourceControls(); }
}

async function toggleRecording() {
  if (isRecording || recordTransition || Object.values(sourceState).some(s => s.pending)) return;
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
    activeSources = [...(mic ? ['mic'] : []), ...(sys ? ['sys'] : [])];
    invalidateProtocol('live');
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
    selectFileJob(result.job);
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
    invalidateProtocol('upload', file.name);
    selectedJobId = null; lastJob = null;
    delete document.getElementById('fileTranscript').dataset.signature;
    renderStream('fileTranscript', []);
    document.getElementById('fileStage').textContent = 'Загрузка: ' + file.name;
    document.getElementById('fileProgress').textContent = '';
    document.getElementById('openFinishedMeeting').hidden = true;
    updateChunkControls('file', null);
    showStreamView('file');
    updateAiThought('Загрузка: ' + file.name);
    const data = new FormData(); data.append('audio', file);
    const response = await fetch('/api/upload_audio', {method: 'POST', body: data});
    const result = await response.json();
    if (result.error) throw new Error(result.error);
    selectFileJob(result.job);
    showWorkspaceTab('work'); showStreamView('file');
    document.getElementById('protocolPanel').classList.remove('open');
    updateAiThought('Файл поставлен в очередь. Запись и live могут работать одновременно.');
    await pollWorkspace();
  } catch (error) { updateAiThought('Ошибка загрузки: ' + error.message); }
  finally { uploadInProgress = false; event.target.value = ''; }
}

function renderStream(id, segments) {
  const container = document.getElementById(id);
  const signature = segments.map(s => `${s.start}:${s.speaker || ''}:${s.text}`).join('|');
  if (container.dataset.signature === signature) return;
  container.dataset.signature = signature;
  if (!segments.length) { container.innerHTML = '<div class="empty-state">Ожидание распознавания…</div>'; return; }
  const atBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 60;
  container.innerHTML = segments.map(s => `<div class="live-line"><time>${clockText(s.start)}</time><span>${s.speaker ? `<strong class="stream-speaker">${escapeHtml(s.speaker)}</strong>` : ''}${escapeHtml(s.text)}</span></div>`).join('');
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
    button.onclick = () => { selectFileJob(job); pollWorkspace(); };
    container.appendChild(button);
  }
}
async function pollWorkspace() {
  if (pollRunning) return;
  pollRunning = true;
  const requestedSourceRevision = sourceRevision;
  try {
    const [status, live, jobs] = await Promise.all([callApi('get_recording_status'), callApi('get_live_status'), callApi('get_jobs')]);
    isRecording = status.is_recording; isPaused = status.is_paused;
    if (sourceRevision === requestedSourceRevision && !recordTransition && !Object.values(sourceState).some(s => s.pending)) applySourceStatus(status);
    document.getElementById('recBadge').style.display = isRecording ? 'inline-block' : 'none';
    document.getElementById('btnPauseRec').textContent = isPaused ? 'Продолжить запись' : 'Пауза';
    if (status.error) updateAiThought(status.error);
    inputLevels.mic = sourceState.mic.enabled ? status.mic_level || 0 : 0;
    inputLevels.sys = sourceState.sys.enabled ? status.system_level || 0 : 0;
    latestAudioLevel = Math.max(inputLevels.mic, inputLevels.sys);
    document.getElementById('recTimer').textContent = clockText(status.elapsed_seconds);
    for (const [kind, level] of Object.entries(inputLevels)) {
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
      const requestedId = selectedJobId;
      const receivedJob = await callApi('get_job', {id: requestedId});
      if (requestedId !== selectedJobId) return;
      lastJob = receivedJob;
      document.getElementById('fileStage').textContent = lastJob.error || lastJob.stage;
      document.getElementById('fileProgress').textContent = `${clockText(lastJob.completed_seconds)} / ${clockText(lastJob.total_seconds)}`;
      renderStream('fileTranscript', lastJob.segments || []); updateChunkControls('file', lastJob.current_chunk);
      const openButton = document.getElementById('openFinishedMeeting');
      openButton.hidden = lastJob.state !== 'done';
      openButton.onclick = () => { if (lastJob?.id === selectedJobId && lastJob.state === 'done') openDemoMeeting(lastJob.meeting_id, 'job'); };
    }
    if (protocolSelection.kind === 'fragment') {
      const version = selectionVersion;
      const fragment = await callApi('get_meeting', {id: protocolSelection.id});
      if (version === selectionVersion && currentMeetingData?.id === fragment.id && currentMeetingData.fragment_version !== fragment.fragment_version) renderMeeting(fragment);
    }
    setProcessing(jobs.some(j => j.state === 'running'));
    document.getElementById('liveStatusPill').textContent = isRecording ? (isPaused ? 'Запись на паузе' : '● Запись + live') : isProcessing ? 'Обработка файла' : 'Готов';
    updateSourceControls();
  } catch (error) { updateAiThought('Связь с приложением: ' + error.message); }
  finally { pollRunning = false; }
}
function setProcessing(active) { isProcessing = active; document.body.classList.toggle('processing', active); }

function setupPlayer(player) {
  player.addEventListener('timeupdate', () => {
    if (player.id === 'archiveAudio' && archiveSampleEnd !== null && player.currentTime >= archiveSampleEnd) { archiveSampleEnd = null; player.pause(); }
  });
  player.addEventListener('play', async () => {
    for (const other of document.querySelectorAll('audio')) if (other !== player) other.pause();
    if (!audioContext) audioContext = new (window.AudioContext || window.webkitAudioContext)();
    if (!audioNodes.has(player)) {
      const source = audioContext.createMediaElementSource(player);
      const analyser = audioContext.createAnalyser(); analyser.fftSize = 4096; analyser.smoothingTimeConstant = 0.35;
      analyser.minDecibels = -85; analyser.maxDecibels = -20;
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
  const identifier = 'fragment_' + chunk.audio_url.split('/').pop();
  await openDemoMeeting(identifier, 'fragment');
  if (currentMeetingData?.id !== identifier) return;
  const player = document.getElementById('archiveAudio');
  document.getElementById('playbackLabel').textContent = `Прослушивание ${clockText(chunk.start)} — ${clockText(chunk.end)}`;
  try { await player.play(); } catch (error) { updateAiThought('Не удалось воспроизвести фрагмент: ' + error.message); }
}
function configureArchiveAudio(meeting) {
  archiveSampleEnd = null;
  const player = document.getElementById('archiveAudio');
  if (meeting.audio_url && player.getAttribute('src') === meeting.audio_url) return;
  player.pause(); player.removeAttribute('src'); player.hidden = !meeting.audio_url;
  if (meeting.audio_url) player.src = meeting.audio_url;
}

function startWaveformAnimation() {
  const archiveCanvas = document.getElementById('playbackCanvas');
  const mainVisual = new VoiceVisualizer(canvas), archiveVisual = new VoiceVisualizer(archiveCanvas);
  function draw(now) {
    let playback = new Float32Array(40);
    if (activePlayback && !activePlayback.paused && !activePlayback.ended) {
      const state = audioNodes.get(activePlayback);
      if (state) playback = VoiceVisualizer.readBands(state, audioContext.sampleRate);
    }
    const playing = activePlayback && !activePlayback.paused && !activePlayback.ended;
    const levels = playing ? playback : Array.from({length: 40}, (_, i) => isRecording && !isPaused ? Math.min(1, latestAudioLevel * 2.8 * Math.exp(-2.2 * ((i - 19.5) / 20) ** 2)) : 0);
    mainVisual.paint(levels, now); archiveVisual.paint(playback, now);
    document.getElementById('waveSourceLabel').textContent = playing ? 'Эквалайзер: прослушиваемый фрагмент' : 'Индикаторы входящего звука';
    const status = playing ? 'Воспроизведение записи' : isPaused ? 'Запись на паузе' : isRecording ? (latestAudioLevel > .008 ? 'Слышу голос' : 'Слушаю · ожидание речи') : 'Готов слушать';
    const label = document.getElementById('voiceState');
    if (label.textContent !== status) { label.textContent = status; canvas.setAttribute('aria-label', 'Эквалайзер: ' + status); }
    document.getElementById('voiceDetail').textContent = playing ? '40 частотных полос' : 'Уровень входящего звука';
    waveformAnimationId = requestAnimationFrame(draw);
  }
  draw(performance.now());
}

async function saveExport(format) {
  try {
    if (!await ensureSelectedMeeting()) return;
    if (currentMeetingData.is_fragment && !currentMeetingData.fragment_ready) { updateAiThought('Этот фрагмент ещё распознаётся. Дождитесь появления текста.'); return; }
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

async function reprocessSelectedMeeting() {
  if (!currentMeetingData) return;
  try {
    const result = await callApi('reprocess_meeting', {id: currentMeetingData.id});
    selectFileJob(result.job); showWorkspaceTab('work');
    updateAiThought('Создаётся новый протокол с различением голосов. Предыдущий сохранён в истории.');
  } catch (error) { updateAiThought(error.message); }
}

function renderSpeakerCards(meeting) {
  document.getElementById('reprocessMeetingButton').hidden = !!meeting.is_fragment;
  const container = document.getElementById('speakerCards'); container.replaceChildren();
  for (const profile of meeting.speakers || []) {
    const card = document.createElement('div'); card.className = 'speaker-card';
    const title = document.createElement('strong'); title.textContent = profile.label;
    const status = document.createElement('span'); status.className = 'speaker-status';
    status.textContent = ({confirmed: 'Подтверждено вами', inferred: 'Имя по контексту — проверьте', self_introduced: 'Представился в записи', conflict: 'Противоречивые обращения', unresolved: 'Имя пока неизвестно'})[profile.name_status] || '';
    const input = document.createElement('input'); input.value = profile.name || ''; input.placeholder = 'Имя этого голоса'; input.maxLength = 120;
    const save = document.createElement('button'); save.textContent = 'Подтвердить имя';
    save.onclick = async () => {
      const version = selectionVersion; save.disabled = true;
      try {
        const result = await callApi('update_speaker_name', {meeting_id: meeting.id, speaker_id: profile.id, name: input.value});
        if (version === selectionVersion) renderMeeting(result);
      } catch (error) { updateAiThought(error.message); }
      finally { save.disabled = false; }
    };
    const listen = document.createElement('button'); listen.textContent = 'Послушать голос'; listen.disabled = !meeting.audio_url;
    listen.onclick = () => {
      const player = document.getElementById('archiveAudio');
      archiveSampleEnd = profile.sample_end;
      player.currentTime = profile.sample_start || 0; player.play().catch(error => updateAiThought(error.message));
    };
    card.append(title, status, input, save, listen);
    if (profile.evidence?.length) {
      const details = document.createElement('details'); const heading = document.createElement('summary'); heading.textContent = 'Почему предложено это имя'; details.appendChild(heading);
      for (const evidence of profile.evidence) { const line = document.createElement('p'); line.textContent = `${clockText(evidence.start)} · ${evidence.name}: «${evidence.quote}»`; details.appendChild(line); }
      card.appendChild(details);
    }
    container.appendChild(card);
  }
}
