'use strict';

// The SDK document owns its entire lifecycle. This shell never re-renders it
// when notes, questions, recording metadata, or panel visibility change.
const byId = id => document.getElementById(id);
const state = { config: null, meeting: null, mode: 'host', guestUrl: '', frame: null, phase: 'idle', busy: false, recordingsBusy: false, createRequestId: '', draft: { general: '', questions: [''] }, draftKey: 'zoom-poc:draft:preparation', draftWarning: '', panelFailed: false };
const ui = {
  app: byId('app'), title: byId('meeting-title'), card: byId('connection-card'), heading: byId('connection-title'), description: byId('connection-description'), error: byId('connection-error'), form: byId('join-form'), name: byId('display-name'), primary: byId('primary-action'), retry: byId('retry-config'), setup: byId('setup-details'), missing: byId('missing-settings'), sdk: byId('sdk-container'), status: byId('call-status'), copy: byId('copy-invite'), back: byId('return-to-join'), notice: byId('action-notice'), panel: byId('side-panel'), toggle: byId('toggle-panel'), workspace: byId('notes-workspace'), recovery: byId('notes-recovery'), notes: byId('general-notes'), questions: byId('questions-list'), draftStatus: byId('draft-status'), recordings: byId('recordings-section'), recordingStatus: byId('recordings-status'), files: byId('recording-files'),
};

function showNotice(message = '') { ui.notice.textContent = message; ui.notice.hidden = !message; }
function showError(message = '') { ui.error.textContent = message; ui.error.hidden = !message; }
function sessionRead(key) { try { return sessionStorage.getItem(key) || ''; } catch { return ''; } }
function sessionWrite(key, value) { try { sessionStorage.setItem(key, value); } catch { /* Name persistence must not gate the call. */ } }
function validMeetingId(value) { return typeof value === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(value); }
function meetingPath(suffix = '') { return `/api/meetings/${encodeURIComponent(state.meeting.id)}${suffix}`; }

async function api(path, options = {}) {
  const { timeoutMs = 45000, ...fetchOptions } = options;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', ...fetchOptions, signal: controller.signal, headers: { 'Content-Type': 'application/json', ...fetchOptions.headers } });
    let payload;
    try { payload = await response.json(); } catch { throw new Error('Сервер вернул неожиданный ответ. Попробуйте ещё раз.'); }
    if (!response.ok) {
      const messages = { 400: 'Проверьте данные встречи и попробуйте ещё раз.', 401: 'Нужна действующая ссылка доступа к этой встрече.', 403: 'Эта ссылка не даёт доступа к выбранному действию.', 404: 'Встреча или запись не найдена.', 409: 'Операция пока недоступна. Обновите состояние встречи.', 429: 'Слишком много запросов. Повторите немного позже.', 503: 'Zoom пока не подключён или временно недоступен.' };
      throw new Error(messages[response.status] || 'Не удалось выполнить действие. Попробуйте ещё раз.');
    }
    return payload;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('Сервер не ответил вовремя. Попробуйте ещё раз.');
    if (error instanceof TypeError) throw new Error('Не удалось связаться с локальным сервером. Проверьте, что он запущен.');
    throw error;
  } finally { clearTimeout(timeout); }
}

function setBusy(value) { state.busy = value; ui.primary.disabled = value; ui.name.disabled = value; ui.retry.disabled = value; }
function useMeeting(payload) {
  const meeting = payload.meeting || payload;
  if (!validMeetingId(meeting.id)) throw new Error('Сервер не вернул корректную встречу.');
  state.meeting = meeting;
  ui.title.textContent = meeting.topic || 'Пробная встреча Zoom';
  const candidateUrl = payload.guestUrl || sessionRead(`zoom-poc:invite:${meeting.id}`);
  if (candidateUrl) {
    const url = new URL(candidateUrl, location.origin);
    if (url.origin === location.origin) { state.guestUrl = url.href; sessionWrite(`zoom-poc:invite:${meeting.id}`, url.href); }
  }
  withNotes(() => {
    const oldDraft = state.draft;
    loadDraft(`zoom-poc:draft:${meeting.id}`);
    if (!state.draft.general && state.draft.questions.every(question => !question.trim()) && (oldDraft.general || oldDraft.questions.some(question => question.trim()))) {
      state.draft = oldDraft; saveDraft(); renderNotes();
    }
  });
  ui.copy.hidden = state.mode !== 'host' || !state.guestUrl;
  if (ui.recordings) ui.recordings.hidden = state.mode !== 'host';
  const url = new URL(location.href);
  url.searchParams.set('meeting', meeting.id); url.searchParams.set('mode', state.mode); url.hash = '';
  history.replaceState(null, '', url);
}

function renderConnection() {
  if (state.frame) return;
  const config = state.config;
  ui.form.hidden = true; ui.retry.hidden = false; ui.setup.hidden = true;
  if (!config) { ui.heading.textContent = 'Подключение Zoom'; ui.description.textContent = 'Проверяем подключение аккаунта…'; return; }
  if (!config.configured) {
    ui.heading.textContent = 'Подключение Zoom';
    ui.description.textContent = 'Для видеовстречи нужно подключить аккаунт Zoom.' + (state.panelFailed ? '' : ' Заметки и вопросы уже доступны.');
    ui.status.textContent = 'Zoom требует подключения';
    ui.missing.replaceChildren();
    for (const name of Array.isArray(config.missing) ? config.missing : []) {
      if (typeof name !== 'string' || !/^[A-Z0-9_]+$/.test(name)) continue;
      const item = document.createElement('li'); item.textContent = name; ui.missing.append(item);
    }
    ui.setup.hidden = !ui.missing.children.length;
    return;
  }
  if (state.mode === 'host' && !config.hostSession) {
    ui.heading.textContent = 'Вход организатора';
    ui.description.textContent = 'Откройте локальную ссылку организатора, выданную сервером.';
    ui.status.textContent = 'Требуется доступ организатора';
    return;
  }
  if (state.mode === 'guest' && !state.meeting) {
    ui.heading.textContent = 'Приглашение на встречу';
    ui.description.textContent = 'Откройте действующую ссылку приглашения от организатора.';
    ui.status.textContent = 'Требуется приглашение';
    return;
  }
  ui.heading.textContent = state.meeting ? 'Можно подключаться' : 'Новая встреча';
  ui.description.textContent = state.meeting ? 'Проверьте имя. Камеру, микрофон и вход во встречу подготовит Zoom.' : 'Создайте пробную встречу и пригласите второго участника.';
  ui.primary.textContent = state.meeting ? 'Войти в Zoom' : 'Создать встречу';
  ui.form.hidden = false; ui.retry.hidden = true;
  ui.status.textContent = state.meeting ? 'Встреча создана · вы ещё не подключились' : 'Встреча ещё не создана';
}

async function refreshConfig() {
  setBusy(true); showError();
  try { state.config = await api('/api/config'); renderConnection(); }
  catch (error) { ui.description.textContent = 'Не удалось проверить подключение Zoom.'; ui.status.textContent = 'Нет ответа от сервера'; ui.retry.hidden = false; showError(error.message); }
  finally { setBusy(false); }
}

function joinMeeting() {
  if (state.frame || !state.meeting) return;
  sessionWrite('zoom-poc-display-name', ui.name.value.trim());
  const frame = document.createElement('iframe');
  frame.title = 'Zoom · видеовстреча';
  frame.allow = 'camera; microphone; display-capture; fullscreen; autoplay; clipboard-write';
  frame.referrerPolicy = 'no-referrer';
  frame.src = `/client.html?meeting=${encodeURIComponent(state.meeting.id)}&mode=${state.mode}`;
  state.frame = frame; state.phase = 'loading';
  ui.sdk.append(frame); ui.sdk.hidden = false; ui.card.hidden = true; ui.back.hidden = true;
  ui.status.textContent = 'Загружается интерфейс Zoom…'; showError(); showNotice();
}

ui.form.addEventListener('submit', async event => {
  event.preventDefault();
  if (state.busy || state.frame) return;
  const name = ui.name.value.trim();
  if (!name) { ui.name.setCustomValidity('Укажите имя участника.'); ui.name.reportValidity(); return; }
  sessionWrite('zoom-poc-display-name', name);
  if (state.meeting) { joinMeeting(); return; }
  setBusy(true); showError(); ui.primary.textContent = 'Создаём встречу…';
  try {
    // Keep the same operation ID through timeout/reload so the server can
    // recover an existing Zoom meeting instead of creating a duplicate.
    const requestId = state.createRequestId || sessionRead('zoom-poc:create-request-id') || crypto.randomUUID();
    state.createRequestId = requestId;
    sessionWrite('zoom-poc:create-request-id', requestId);
    const created = await api('/api/meetings', { method: 'POST', body: JSON.stringify({ topic: 'Пробная встреча · МашаДаша', requestId }) });
    useMeeting(created); renderConnection();
    state.createRequestId = '';
    sessionWrite('zoom-poc:create-request-id', '');
  } catch (error) { showError(`${error.message} При неизвестном результате проверьте встречу в Zoom: повтор использует тот же запрос.`); ui.primary.textContent = 'Повторить запрос'; }
  finally { setBusy(false); }
});
ui.name.addEventListener('input', () => ui.name.setCustomValidity(''));
ui.retry.addEventListener('click', () => void refreshConfig());

window.addEventListener('message', event => {
  if (!state.frame || event.source !== state.frame.contentWindow || event.origin !== location.origin) return;
  const message = event.data;
  if (!message || message.source !== 'zoom-poc-client' || message.type !== 'status') return;
  const labels = { loading: 'Загружается интерфейс Zoom…', joining: 'Zoom выполняет вход во встречу…', joined: 'Zoom подтвердил вход · проверьте звук с собеседником', left: 'Вы вышли из Zoom', error: 'Zoom сообщил об ошибке подключения' };
  if (!Object.hasOwn(labels, message.phase)) return;
  state.phase = message.phase;
  ui.status.textContent = labels[message.phase] + (message.phase === 'error' && Number.isFinite(message.code) ? ` · код ${message.code}` : '');
  // A failed load might still recover. Only an explicit SDK exit permits reset.
  ui.back.hidden = message.phase !== 'left';
});
ui.back.addEventListener('click', () => {
  if (state.phase !== 'left') return;
  state.frame?.remove(); state.frame = null; state.phase = 'idle';
  ui.sdk.hidden = true; ui.card.hidden = false; ui.back.hidden = true; renderConnection();
});
ui.copy.addEventListener('click', async () => {
  if (!state.guestUrl) return;
  try { await navigator.clipboard.writeText(state.guestUrl); showNotice('Приглашение скопировано. Отправьте его второму участнику.'); }
  catch { showNotice('Браузер не разрешил копирование. Откройте страницу отдельно и повторите.'); }
});
ui.toggle.addEventListener('click', () => {
  const closed = ui.app.classList.toggle('panel-closed');
  ui.toggle.setAttribute('aria-expanded', String(!closed));
  ui.toggle.setAttribute('aria-label', closed ? 'Показать заметки и вопросы' : 'Скрыть заметки и вопросы');
  ui.toggle.title = ui.toggle.getAttribute('aria-label');
});
byId('fullscreen').addEventListener('click', async () => {
  try { if (document.fullscreenElement) await document.exitFullscreen(); else await ui.app.requestFullscreen(); }
  catch { showNotice('Полный экран недоступен в этом браузере.'); }
});

// Notes are a local, optional surface. Its failure boundary has no reference to
// the SDK iframe and never blocks creation, joining, or native Zoom exit.
function showNotesFailure() {
  state.panelFailed = true;
  if (ui.workspace) ui.workspace.hidden = true;
  if (ui.recovery) ui.recovery.hidden = false;
}
function withNotes(operation) {
  try { operation(); }
  catch { showNotesFailure(); }
}
function onNotes(id, eventName, handler) {
  byId(id).addEventListener(eventName, event => withNotes(() => handler(event)));
}
function reportDraftStatus() {
  ui.draftStatus.textContent = state.draftWarning || 'Заметки сохранены в этом браузере';
  ui.draftStatus.classList.toggle('save-warning', !!state.draftWarning);
}
function saveDraft() {
  try { localStorage.setItem(state.draftKey, JSON.stringify(state.draft)); state.draftWarning = ''; }
  catch { state.draftWarning = 'Черновик в памяти страницы. Скачайте его перед закрытием.'; }
  reportDraftStatus();
}
function loadDraft(key) {
  state.draftKey = key; state.draft = { general: '', questions: [''] }; state.draftWarning = '';
  try {
    const raw = localStorage.getItem(key);
    if (raw) {
      const draft = JSON.parse(raw);
      if (typeof draft.general !== 'string' || !Array.isArray(draft.questions) || !draft.questions.every(value => typeof value === 'string')) throw new Error('Invalid draft');
      state.draft = { general: draft.general.slice(0, 100000), questions: draft.questions.slice(0, 30).map(value => value.slice(0, 5000)) };
      if (!state.draft.questions.length) state.draft.questions.push('');
    }
  } catch { state.draftWarning = 'Не удалось прочитать сохранённые заметки. Новый черновик можно скачать.'; }
  renderNotes(); reportDraftStatus();
}
function renderNotes(fail = false) {
  try {
    if (fail) throw new Error('Requested notes failure');
    ui.notes.value = state.draft.general; ui.questions.replaceChildren();
    state.draft.questions.forEach((value, index) => {
      const field = document.createElement('div'); field.className = 'question-field';
      const label = document.createElement('label'); label.htmlFor = `question-${index}`; label.textContent = `Вопрос ${index + 1}`;
      const input = document.createElement('textarea'); input.id = label.htmlFor; input.value = value; input.maxLength = 5000; input.placeholder = 'Что хотите узнать?';
      input.addEventListener('input', () => withNotes(() => { state.draft.questions[index] = input.value; saveDraft(); }));
      field.append(label, input); ui.questions.append(field);
    });
    byId('add-question').disabled = state.draft.questions.length >= 30;
    state.panelFailed = false; ui.workspace.hidden = false; ui.recovery.hidden = true;
  } catch { showNotesFailure(); }
}
function setTab(name, focus = false) {
  for (const tab of ['notes', 'questions']) {
    const selected = tab === name;
    byId(`${tab}-tab`).setAttribute('aria-selected', String(selected)); byId(`${tab}-tab`).tabIndex = selected ? 0 : -1; byId(`${tab}-view`).hidden = !selected;
  }
  if (focus) byId(`${name}-tab`).focus();
}
function exportNotes() {
  const lines = [state.meeting?.topic || 'Заметки к встрече', '', 'Заметки', state.draft.general, '', 'Вопросы', ...state.draft.questions.map((value, index) => `${index + 1}. ${value}`)];
  const url = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' }));
  const link = document.createElement('a'); link.href = url; link.download = `meeting-notes-${new Date().toISOString().slice(0, 10)}.txt`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function initializeNotes() {
  loadDraft(state.draftKey);
  ui.notes.maxLength = 100000;
  onNotes('general-notes', 'input', () => { state.draft.general = ui.notes.value; saveDraft(); });
  onNotes('add-question', 'click', () => {
    if (state.draft.questions.length >= 30) return;
    state.draft.questions.push(''); saveDraft(); renderNotes(); ui.questions.lastElementChild?.querySelector('textarea')?.focus();
  });
  for (const tab of ['notes', 'questions']) {
    onNotes(`${tab}-tab`, 'click', () => setTab(tab));
    onNotes(`${tab}-tab`, 'keydown', event => { if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) { event.preventDefault(); setTab(event.key === 'Home' ? 'notes' : event.key === 'End' ? 'questions' : tab === 'notes' ? 'questions' : 'notes', true); } });
  }
  onNotes('export-notes', 'click', exportNotes);
  onNotes('export-recovery', 'click', exportNotes);
  onNotes('fail-notes', 'click', () => renderNotes(true));
  onNotes('restore-notes', 'click', () => renderNotes());
}

function setRecordingsBusy(value) {
  state.recordingsBusy = value;
  const refresh = byId('refresh-recordings');
  if (refresh) refresh.disabled = value;
  if (ui.files) for (const button of ui.files.querySelectorAll('button')) button.disabled = value || button.dataset.downloaded === 'true';
}
byId('refresh-recordings')?.addEventListener('click', async () => {
  if (!state.meeting || state.mode !== 'host' || state.recordingsBusy || !ui.files || !ui.recordingStatus) return;
  setRecordingsBusy(true); ui.recordingStatus.textContent = 'Проверяем записи в Zoom…';
  try {
    const data = await api(meetingPath('/recordings'));
    const labels = { absent: 'Zoom пока не вернул запись этой встречи.', processing: 'Zoom обрабатывает запись.', ready: 'Zoom вернул готовые файлы записи.' };
    ui.recordingStatus.textContent = labels[data.status] || 'Статус записи пока неизвестен.'; ui.files.replaceChildren();
    for (const file of Array.isArray(data.files) ? data.files : []) {
      if (typeof file.id !== 'string') continue;
      const item = document.createElement('li'); const label = document.createElement('span'); label.className = 'recording-name'; label.textContent = [file.fileType, file.recordingType].filter(value => typeof value === 'string').join(' · ') || 'Файл записи'; item.append(label);
      if (file.status === 'completed') {
        const download = document.createElement('button'); download.className = 'text-button'; download.textContent = file.downloaded ? 'Сохранено локально' : 'Сохранить локально'; download.dataset.downloaded = String(!!file.downloaded); download.disabled = true;
        download.addEventListener('click', async () => {
          if (state.recordingsBusy || download.dataset.downloaded === 'true') return;
          setRecordingsBusy(true); download.textContent = 'Сохраняем…';
          try {
            const result = await api(meetingPath(`/recordings/${encodeURIComponent(file.id)}/download`), { method: 'POST', body: '{}', timeoutMs: 160000 });
            if (result.status !== 'downloaded') throw new Error('Сервер ещё не подтвердил сохранение файла. Проверьте запись ещё раз.');
            download.dataset.downloaded = 'true'; download.textContent = 'Сохранено локально';
          } catch (error) { ui.recordingStatus.textContent = error.message; download.textContent = 'Повторить сохранение'; }
          finally { setRecordingsBusy(false); }
        });
        item.append(download);
      }
      ui.files.append(item);
    }
  } catch (error) { ui.recordingStatus.textContent = error.message; }
  finally { setRecordingsBusy(false); }
});

async function initialize() {
  ui.name.value = sessionRead('zoom-poc-display-name');
  const hash = new URLSearchParams(location.hash.slice(1));
  const query = new URLSearchParams(location.search);
  const hostCapability = hash.get('host'); const guestCapability = hash.get('guest');
  const meetingId = hash.get('meeting') || query.get('meeting');
  state.mode = guestCapability || query.get('mode') === 'guest' ? 'guest' : 'host';
  // Capabilities are consumed once and removed before any API/network work.
  if (location.hash) history.replaceState(null, '', `${location.pathname}${location.search}`);
  setBusy(true);
  try {
    if (hostCapability) await api('/api/session', { method: 'POST', body: JSON.stringify({ hostCapability }) });
    if (guestCapability) {
      if (!validMeetingId(meetingId)) throw new Error('В приглашении нет корректной встречи.');
      await api(`/api/meetings/${encodeURIComponent(meetingId)}/session`, { method: 'POST', body: JSON.stringify({ guestCapability }) });
    }
    state.config = await api('/api/config');
    if (validMeetingId(meetingId)) useMeeting(await api(`/api/meetings/${encodeURIComponent(meetingId)}?mode=${state.mode}`));
    renderConnection();
  } catch (error) {
    renderConnection(); ui.description.textContent = 'Не удалось подготовить вход во встречу.'; ui.status.textContent = 'Подключение не выполнено'; showError(error.message); ui.retry.hidden = false;
  } finally { setBusy(false); }
}
void initialize();
withNotes(initializeNotes);
