export class CallConnectionError extends Error {}

// Daily's call engine is served by this app (scripts/daily-bundles.mjs, pinned by sha256) instead of Daily's CDN,
// which some users' networks cannot reach. Keep in step with the installed @daily-co/daily-js version.
export const DAILY_JS_VERSION = '0.93.0';

export function dailyCallConfig(origin = globalThis.location?.origin ?? '') {
  return { bundlePathOverride: `${origin}/daily/${DAILY_JS_VERSION}` };
}

// Stop waiting immediately on cancel; a late provider response cannot resume the UI.
export function waitForCall<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const abort = () => { cleanup(); reject(signal.reason); };
    const cleanup = () => signal.removeEventListener('abort', abort);
    if (signal.aborted) abort();
    else signal.addEventListener('abort', abort, { once: true });
    operation.then(value => { cleanup(); resolve(value); }, cause => { cleanup(); reject(cause); });
  });
}

export class PreviewTimeoutError extends CallConnectionError {}

export function deviceAccessMessage(cause: unknown): string {
  const name = cause && typeof cause === 'object' && 'name' in cause ? cause.name : '';
  if (cause instanceof PreviewTimeoutError) return 'Браузер не ответил на запрос. Разрешите доступ в настройках сайта или войдите без устройств.';
  if (name === 'NotAllowedError' || name === 'SecurityError') return 'Доступ запрещён. В настройках сайта разрешите камеру и микрофон, затем повторите проверку.';
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'Камера или микрофон не найдены. Проверьте подключение или войдите без устройств.';
  if (name === 'NotReadableError' || name === 'AbortError') return 'Камера или микрофон заняты. Закройте другой звонок и повторите проверку.';
  return 'Не удалось включить устройства. Проверьте доступ в браузере и системных настройках.';
}

/** Same private room and scoped participant token, only without our iframe. */
export function directCallUrl(credentials: { url: string; token: string }): string {
  const url = new URL(credentials.url);
  if (url.protocol !== 'https:' || !url.hostname.endsWith('.daily.co') || url.username || url.password || !credentials.token) {
    throw new CallConnectionError('Не удалось открыть видеокомнату.');
  }
  url.searchParams.set('t', credentials.token);
  return url.href;
}

/** A permission prompt can stay unanswered forever. Never let it lock entry,
 * and release devices if the browser grants access after timeout/cancellation. */
export async function waitForPreview<T extends { getTracks: () => { stop: () => void }[] }>(
  operation: Promise<T>, signal: AbortSignal, timeoutMs = 10000,
): Promise<T> {
  const controller = new AbortController();
  const cancel = () => controller.abort(signal.reason);
  let waiting = true;
  if (signal.aborted) cancel();
  else signal.addEventListener('abort', cancel, { once: true });
  const timer = setTimeout(() => controller.abort(new PreviewTimeoutError('Не получили доступ к устройствам. Можно войти без камеры и микрофона.')), timeoutMs);
  try {
    return await waitForCall(operation.then(media => {
      if (!waiting || controller.signal.aborted) media.getTracks().forEach(track => track.stop());
      return media;
    }), controller.signal);
  } finally {
    waiting = false;
    clearTimeout(timer);
    signal.removeEventListener('abort', cancel);
  }
}

export async function releaseCall(call: { destroy: () => Promise<void>; iframe?: () => { remove: () => void } | null }, timeoutMs = 3000): Promise<void> {
  const controller = new AbortController();
  const iframe = call.iframe?.();
  const timeout = setTimeout(() => controller.abort(new CallConnectionError('Не удалось закрыть видеокомнату. Обновите страницу.')), timeoutMs);
  try { await waitForCall(call.destroy(), controller.signal); }
  catch (cause) {
    // A blocked Prebuilt bundle cannot acknowledge destroy. Detach its browsing
    // context so it cannot finish loading and acquire devices behind the error UI.
    // Keep the rejection: callers require a reload before creating another call.
    iframe?.remove();
    throw cause;
  }
  finally { clearTimeout(timeout); }
}
