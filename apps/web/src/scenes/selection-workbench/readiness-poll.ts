import { selectionRequest, type Readiness } from './api';
import { shouldPollReadiness } from './assessment-automation';

/** One initial GET plus at most five sequential refreshes per explicit check. */
export const READINESS_POLL_DELAYS = [3000, 5000, 10000, 20000, 30000] as const;
export function startReadinessPolling(candidateId: string, token: string, callbacks: {
  onValue: (value: Readiness) => void; onError: (cause: unknown) => void; onExhausted: () => void;
}) {
  let active = true, attempt = 0, timer: ReturnType<typeof setTimeout> | undefined;
  async function read() {
    try {
      const value = await selectionRequest<Readiness>('candidates/' + encodeURIComponent(candidateId) + '/readiness', token);
      if (!active) return;
      if (value.candidateId !== candidateId || !value.assessment) throw new Error('Получена карточка другого кандидата.');
      callbacks.onValue(value);
      if (!active || !shouldPollReadiness(value)) return;
      if (attempt >= READINESS_POLL_DELAYS.length) { callbacks.onExhausted(); return; }
      timer = setTimeout(() => void read(), READINESS_POLL_DELAYS[attempt++]);
    } catch (cause) { if (active) callbacks.onError(cause); }
  }
  void read();
  return () => { active = false; clearTimeout(timer); };
}
