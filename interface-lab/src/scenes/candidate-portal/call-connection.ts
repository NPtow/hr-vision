export class CallConnectionError extends Error {}

// Stop waiting immediately on cancel; a late provider response cannot resume the UI.
export function waitForCall<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason);
    const cleanup = () => signal.removeEventListener('abort', abort);
    if (signal.aborted) abort();
    else signal.addEventListener('abort', abort, { once: true });
    operation.then(value => { cleanup(); resolve(value); }, cause => { cleanup(); reject(cause); });
  });
}

export async function releaseCall(call: { destroy: () => Promise<void> }, timeoutMs = 3000): Promise<void> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(new CallConnectionError('Не удалось закрыть видеокомнату. Обновите страницу.')), timeoutMs);
  try { await waitForCall(call.destroy(), controller.signal); }
  finally { clearTimeout(timeout); }
}
