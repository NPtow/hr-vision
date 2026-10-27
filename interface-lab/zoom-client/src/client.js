import { prepareSdk } from './sdk-loader.js';
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
let preparation;
let failed = false;
const controller = new AbortController();

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
  if (!active || failed) return;
  failed = true;
  controller.abort();
  const messages = {
    invalid_credentials: 'Не удалось получить доступ к встрече. Обновите страницу и попробуйте снова.',
    insecure_context: 'Для звонка нужен защищённый адрес HTTPS.',
    sdk_load: 'Не удалось загрузить окно встречи. Повторите загрузку.',
    sdk_assets: 'Не удалось загрузить файлы встречи. Повторите загрузку.',
    sdk_load_timeout: 'Подготовка встречи занимает слишком много времени. Повторите загрузку.',
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

function prepare() {
  if (preparation) return preparation;
  report('loading');
  preparation = Promise.race([
    prepareSdk(controller.signal),
    new Promise((_, reject) => {
      sdkTimer = setTimeout(() => { controller.abort(); reject(new Error('sdk_load_timeout')); }, 90000);
    }),
  ]).then(sdk => {
    if (!active || failed) throw new Error('cancelled');
    report('prepared');
    return sdk;
  }).finally(() => clearTimeout(sdkTimer));
  return preparation;
}

function preparationFailed(error) {
  fail(error?.message === 'sdk_load_timeout' || controller.signal.aborted ? 'sdk_load_timeout'
    : error?.message === 'sdk_assets' ? 'sdk_assets' : 'sdk_load');
}

function stop() {
  active = false;
  controller.abort();
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
  let ZoomMtg;
  try { ZoomMtg = await prepare(); }
  catch (error) { config = null; preparationFailed(error); return; }
  if (!active || failed) return;
  try {
    ZoomMtg.inMeetingServiceListener('onMeetingStatus', (event = {}) => {
      if (!active || failed) return;
      const status = event.meetingStatus ?? event.status;
      if (status === 1 || status === 4) report('joining');
      if (status === 2) { joined = true; report('joined'); }
      if (status === 3) { report('left'); stop(); }
    });
    let initialized = false;
    report('joining');
    const pending = ZoomMtg.init({
      leaveUrl: `${location.origin}${LEFT_PATH}`,
      helper: '/call/zoom/helper.html', externalLinkPage: '/call/zoom/externalLinkPage.html',
      debug: false, patchJsMedia: false,
      disablePreview: false, enableWaitingRoomPreview: true,
      disableCORP: !window.crossOriginIsolated,
      success: () => {
        if (!active || failed || initialized || !config) return;
        initialized = true;
        notice.hidden = true;
        const credentials = config; config = null;
        try {
          const join = ZoomMtg.join({ ...credentials,
            success: () => { if (active && !failed) { joined = true; report('joined'); } },
            error: error => fail('join', error),
          });
          join?.catch(error => fail('join', error));
        } catch (error) { fail('join', error); }
      },
      error: error => { config = null; fail('init', error); },
    });
    pending?.catch(error => { config = null; fail('init', error); });
  } catch (error) { config = null; fail('init', error); }
}

window.addEventListener('message', (event) => {
  if (!active || window.parent === window || event.source !== window.parent
      || event.origin !== location.origin || event.data?.source !== 'hr-zoom-parent') return;
  if (event.data.type === 'leave') { localLeave(); return; }
  if (event.data.type === 'prepare') {
    if (!window.isSecureContext) { fail('insecure_context'); return; }
    if (!failed) void prepare().catch(preparationFailed);
    return;
  }
  if (event.data.type !== 'join' || attempted || failed) return;
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
