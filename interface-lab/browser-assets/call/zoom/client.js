// This document alone owns one Zoom Client View. Credentials arrive in memory
// from the authorized parent; device permissions and reconnection belong to Zoom.
const SDK_VERSION = '6.10.0';
const LEFT_PATH = '/call/zoom/left.html';
const notice = document.getElementById('connection-notice');
const title = document.getElementById('connection-title');
const description = document.getElementById('connection-description');
let active = true;
let attempted = false;
let joined = false;
let sdkTimer;

function post(message) {
  if (window.parent === window) return;
  window.parent.postMessage({ source: 'hr-zoom-client', ...message }, location.origin);
}

function report(phase, extra = {}) {
  if (active) post({ type: 'status', phase, ...extra });
}

function showNotice(heading, text) {
  title.textContent = heading;
  description.textContent = text;
  notice.hidden = false;
}

function fail(stage, error) {
  if (!active) return;
  const messages = {
    invalid_credentials: 'Не удалось получить доступ к встрече. Обновите страницу и попробуйте снова.',
    insecure_context: 'Для звонка нужен защищённый адрес HTTPS.',
    sdk_load: 'Не удалось загрузить Zoom. Проверьте подключение к интернету и обновите страницу.',
    sdk_load_timeout: 'Zoom загружается слишком долго. Проверьте подключение к интернету и обновите страницу.',
    init: 'Не удалось подготовить Zoom. Обновите страницу и попробуйте снова.',
    join: 'Не удалось подключиться. Проверьте сообщение Zoom или обновите страницу.',
  };
  // SDK errors may contain credentials. Only a bounded numeric provider code
  // and our fixed message cross the boundary; raw errors are never logged.
  const providerCode = error?.errorCode ?? error?.code;
  const code = (typeof providerCode === 'number' && Number.isSafeInteger(providerCode))
    || (typeof providerCode === 'string' && /^\d{1,10}$/.test(providerCode))
    ? String(providerCode) : stage;
  report('error', { code, message: messages[stage] ?? messages.join });
  if (!joined) showNotice('Не удалось подключиться', messages[stage] ?? messages.join);
}

function boundedText(value, limit, allowEmpty = false) {
  return typeof value === 'string' && value.length <= limit
    && (allowEmpty || value.trim().length > 0) && !/[\u0000-\u001f\u007f]/.test(value);
}

function readCredentials(value) {
  if (!value || typeof value !== 'object' || value.provider !== 'zoom'
      || value.sdkVersion !== SDK_VERSION || !/^\d{9,11}$/.test(String(value.meetingNumber))
      || ![0, 1].includes(value.role) || !boundedText(value.userName, 128)
      || !boundedText(value.passWord, 128, true) || !boundedText(value.signature, 16384)
      || !/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(value.signature)
      || (value.role === 1 && !boundedText(value.zak, 16384))
      || (value.role === 0 && value.zak != null)) return null;
  return {
    meetingNumber: String(value.meetingNumber), userName: value.userName,
    passWord: value.passWord, signature: value.signature,
    ...(value.role === 1 ? { zak: value.zak } : {}),
  };
}

async function loadSdk() {
  try {
    return await Promise.race([
      import('https://source.zoom.us/6.10.0/esm/client-view.es.min.js'),
      new Promise((_, reject) => {
        sdkTimer = setTimeout(() => reject(new Error('sdk_load_timeout')), 30000);
      }),
    ]);
  } finally { clearTimeout(sdkTimer); }
}

function stop() {
  active = false;
  clearTimeout(sdkTimer);
}

function localLeave() {
  if (!active) return;
  report('left');
  stop();
  // ZoomMtg.leaveMeeting ends the meeting when called by a host. Unloading this
  // frame closes only this participant document; do not issue end-for-all here.
  location.replace(LEFT_PATH);
}

async function start(config) {
  report('loading');
  let ZoomMtg;
  try { ({ ZoomMtg } = await loadSdk()); }
  catch (error) {
    fail(error?.message === 'sdk_load_timeout' ? 'sdk_load_timeout' : 'sdk_load');
    return;
  }
  if (!active) return;
  try {
    let initialized = false;
    ZoomMtg.preLoadWasm();
    ZoomMtg.prepareWebSDK();
    ZoomMtg.inMeetingServiceListener('onMeetingStatus', (event = {}) => {
      if (!active) return;
      const status = event.meetingStatus ?? event.status;
      if (status === 1 || status === 4) report('joining');
      if (status === 2) { joined = true; report('joined'); }
      if (status === 3) { report('left'); stop(); }
    });
    ZoomMtg.i18n.load('ru-RU');
    ZoomMtg.i18n.onLoad(() => {
      if (!active || initialized) return;
      initialized = true;
      try {
        ZoomMtg.init({
          leaveUrl: `${location.origin}${LEFT_PATH}`,
          debug: false,
          disablePreview: false,
          enableWaitingRoomPreview: true,
          disableCORP: !window.crossOriginIsolated,
          success: () => {
            if (!active || !config) return;
            notice.hidden = true;
            report('joining');
            const credentials = config;
            config = null;
            try {
              ZoomMtg.join({
                ...credentials,
                success: () => { if (active) { joined = true; report('joined'); } },
                error: (error) => fail('join', error),
              });
            } catch (error) { fail('join', error); }
          },
          error: (error) => { config = null; fail('init', error); },
        });
      } catch (error) { config = null; fail('init', error); }
    });
  } catch (error) { config = null; fail('init', error); }
}

window.addEventListener('message', (event) => {
  if (!active || window.parent === window || event.source !== window.parent
      || event.origin !== location.origin || event.data?.source !== 'hr-zoom-parent') return;
  if (event.data.type === 'leave') { localLeave(); return; }
  if (event.data.type !== 'join' || attempted) return;
  attempted = true;
  if (!window.isSecureContext) { fail('insecure_context'); return; }
  const credentials = readCredentials(event.data.credentials);
  if (!credentials) { fail('invalid_credentials'); return; }
  void start(credentials);
});

window.addEventListener('pagehide', stop, { once: true });
if (window.parent === window) {
  showNotice('Откройте приглашение', 'Подключиться к встрече можно из вашего кабинета или по персональной ссылке.');
} else {
  post({ type: 'ready' });
}
