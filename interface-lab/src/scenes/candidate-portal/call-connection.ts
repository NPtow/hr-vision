export class CallConnectionError extends Error {}

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

export async function releaseCall(call: { destroy: () => Promise<void> }, timeoutMs = 3000): Promise<void> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(new CallConnectionError('Не удалось закрыть видеокомнату. Обновите страницу.')), timeoutMs);
  try { await waitForCall(call.destroy(), controller.signal); }
  finally { clearTimeout(timeout); }
}
