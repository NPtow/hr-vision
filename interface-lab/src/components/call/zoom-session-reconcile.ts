export type ZoomSessionReconcileResult = {
  completed: boolean;
  status?: string;
};

export type ZoomSessionReconcileOptions = {
  request: (signal: AbortSignal) => Promise<ZoomSessionReconcileResult>;
  onCompleted: () => void;
  onPending?: () => void;
  onExhausted?: () => void;
};

const RETRY_DELAYS = [2000, 5000, 10000, 20000, 30000] as const;

/** Reconcile metadata after a native Zoom exit; never ends or rejoins a call.
 * The request owns its HTTP deadline. This owner bounds retries and cancellation.
 */
export function startZoomSessionReconcile(options: ZoomSessionReconcileOptions): () => void {
  let active = true;
  let attempts = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;

  function notify(callback: (() => void) | undefined) {
    try { callback?.(); } catch { /* presentation must not change reconciliation */ }
  }

  async function attempt() {
    if (!active) return;
    timer = undefined;
    attempts++;
    controller = new AbortController();
    let completed = false;
    try {
      completed = (await options.request(controller.signal)).completed === true;
    } catch { /* network and provider delays consume the same bounded retry budget */ }
    controller = undefined;
    if (!active) return;
    if (completed) {
      active = false;
      notify(options.onCompleted);
      return;
    }
    if (attempts === 1) notify(options.onPending);
    if (!active) return;
    if (attempts > RETRY_DELAYS.length) {
      active = false;
      notify(options.onExhausted);
      return;
    }
    timer = setTimeout(() => { void attempt(); }, RETRY_DELAYS[attempts - 1]);
  }

  void attempt();
  return () => {
    active = false;
    clearTimeout(timer);
    controller?.abort();
    controller = undefined;
  };
}
