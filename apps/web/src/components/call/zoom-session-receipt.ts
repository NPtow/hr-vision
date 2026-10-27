export type ZoomSessionReceiptOptions = {
  request: (signal: AbortSignal) => Promise<unknown>;
  onPending?: () => void;
  onSuccess?: () => void;
  onExhausted?: () => void;
};

const RETRY_DELAYS = [1000, 3000, 8000, 15000, 30000] as const;
const TERMINAL_STATUSES = new Set([400, 401, 403, 404]);

/** Best-effort metadata receipt only. Never joins, reconnects or ends a call.
 * The request owns its HTTP deadline; this owner bounds retries and cancellation.
 */
export function startZoomSessionReceipt(options: ZoomSessionReceiptOptions): () => void {
  let active = true;
  let attempts = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;

  function notify(callback: (() => void) | undefined) {
    // Optional presentation callbacks must not trigger another HTTP attempt or
    // prevent the receipt from completing. No provider/user data is logged.
    try { callback?.(); } catch { /* presentation is independent of the receipt */ }
  }

  async function attempt() {
    if (!active) return;
    timer = undefined;
    attempts++;
    controller = new AbortController();
    try {
      await options.request(controller.signal);
    } catch (error) {
      controller = undefined;
      if (!active) return;
      if (attempts === 1) notify(options.onPending);
      if (!active) return;
      const status = error && typeof error === 'object' ? (error as { status?: unknown }).status : undefined;
      if ((typeof status === 'number' && TERMINAL_STATUSES.has(status)) || attempts > RETRY_DELAYS.length) {
        active = false;
        notify(options.onExhausted);
        return;
      }
      timer = setTimeout(() => { void attempt(); }, RETRY_DELAYS[attempts - 1]);
      return;
    }
    controller = undefined;
    if (!active) return;
    active = false;
    notify(options.onSuccess);
  }

  void attempt();
  return () => {
    active = false;
    clearTimeout(timer);
    controller?.abort();
    controller = undefined;
  };
}
