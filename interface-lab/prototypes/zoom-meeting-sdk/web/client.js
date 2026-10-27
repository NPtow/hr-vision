// Zoom owns devices, prejoin, reconnect and leaving. This adapter only authorizes
// one join and reports provider state; notes never send commands to this frame.
const params = new URLSearchParams(location.search);
const notice = document.getElementById('connection-notice');
const title = document.getElementById('connection-title');
const description = document.getElementById('connection-description');
const backLink = document.getElementById('back-link');
const controller = new AbortController();
let active = true;
let joined = false;

function report(phase, code) {
  if (!active) return;
  parent.postMessage({ source: 'zoom-poc-client', type: 'status', phase,
    ...(Number.isFinite(code) ? { code } : {}) }, location.origin);
}

function showNotice(heading, text) {
  title.textContent = heading;
  description.textContent = text;
  notice.hidden = false;
}

function fail(stage, error) {
  if (!active) return;
  const code = Number(error?.errorCode ?? error?.code);
  report('error', code);
  // Never log SDK error objects: they can contain signatures or ZAK tokens.
  const messages = {
    authorization: 'Не удалось получить доступ к встрече. Вернитесь к макету и проверьте подключение аккаунта.',
    sdk: 'Не удалось загрузить Zoom. Проверьте доступ к source.zoom.us и esm.sh в этом браузере.',
    init: 'Zoom не смог подготовить окно встречи. Код ошибки передан в статус подключения.',
    join: 'Zoom не смог подключиться. Проверьте сообщение Zoom и настройки встречи.',
  };
  if (!joined) {
    showNotice('Не удалось подключиться', messages[stage] ?? messages.join);
    backLink.hidden = false;
  }
}

async function loadSdk() {
  let timer;
  try {
    // Pinned supported CDN path and React peer versions from Zoom's CDN sample.
    return await Promise.race([
      import('https://source.zoom.us/6.10.0/esm/client-view.es.min.js'),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('sdk_load_timeout')), 30000); }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

async function authorize(meeting, mode) {
  let displayName = 'Участник';
  try { displayName = sessionStorage.getItem('zoom-poc-display-name') || displayName; } catch { /* storage is optional */ }
  const timer = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(`/api/meetings/${encodeURIComponent(meeting)}/join?mode=${mode}`, {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ displayName }), signal: controller.signal,
    });
    if (!response.ok) throw new Error('authorization_failed');
    const data = await response.json();
    if (!/^\d{9,11}$/.test(String(data.meetingNumber)) || typeof data.signature !== 'string'
        || typeof data.userName !== 'string' || typeof data.passWord !== 'string'
        || data.role !== (mode === 'host' ? 1 : 0) || (mode === 'host' && !data.zak)) {
      throw new Error('invalid_authorization');
    }
    return data;
  } finally { clearTimeout(timer); }
}

async function start() {
  const meeting = params.get('meeting');
  const mode = params.get('mode');
  if (meeting && ['host', 'guest'].includes(mode)) {
    backLink.href = `/?meeting=${encodeURIComponent(meeting)}&mode=${mode}`;
  }
  if (params.get('left') === '1') {
    showNotice('Вы вышли из встречи', 'Заметки остаются в соседней панели.');
    report('left');
    backLink.hidden = false;
    return;
  }
  if (!meeting || !['host', 'guest'].includes(mode)) {
    showNotice('Откройте приглашение', 'Для подключения нужна ссылка на созданную тестовую встречу.');
    backLink.hidden = false;
    return;
  }
  if (!window.isSecureContext) {
    showNotice('Нужен защищённый адрес', 'Откройте макет через HTTPS или на localhost: браузер разрешает устройства только в защищённом контексте.');
    report('error');
    return;
  }
  report('loading');
  let config;
  try { config = await authorize(meeting, mode); }
  catch (error) { fail('authorization', error); return; }
  if (!active) return;
  let ZoomMtg;
  try { ({ ZoomMtg } = await loadSdk()); }
  catch (error) { fail('sdk', error); return; }
  if (!active) return;
  try {
    ZoomMtg.preLoadWasm();
    ZoomMtg.prepareWebSDK();
    ZoomMtg.inMeetingServiceListener('onMeetingStatus', ({ meetingStatus, status }) => {
      const value = meetingStatus ?? status;
      if (value === 1 || value === 4) report('joining');
      if (value === 2) { joined = true; report('joined'); }
      if (value === 3) report('left');
    });
    ZoomMtg.i18n.load('ru-RU');
    ZoomMtg.i18n.onLoad(() => {
      if (!active) return;
      ZoomMtg.init({
        leaveUrl: `${location.origin}/client.html?left=1&meeting=${encodeURIComponent(meeting)}&mode=${mode}`,
        debug: false,
        disablePreview: false,
        enableWaitingRoomPreview: true,
        // Match Zoom's non-isolated CDN sample. This does not bypass browser
        // permission policies; optional HD/SAB features are outside this PoC.
        disableCORP: !window.crossOriginIsolated,
        success: () => {
          if (!active) return;
          notice.hidden = true;
          report('joining');
          ZoomMtg.join({
            signature: config.signature, meetingNumber: String(config.meetingNumber),
            userName: config.userName, passWord: config.passWord,
            ...(config.role === 1 ? { zak: config.zak } : {}),
            success: () => { joined = true; report('joined'); },
            error: (error) => fail('join', error),
          });
          config = null;
        },
        error: (error) => fail('init', error),
      });
    });
  } catch (error) { fail('init', error); }
}

window.addEventListener('pagehide', () => { active = false; controller.abort(); }, { once: true });
start();
