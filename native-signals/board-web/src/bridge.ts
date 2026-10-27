import { PROTOCOL_VERSION, parseHostMessage, type HostMessage, type OutgoingMessage } from './protocol';

declare global {
  interface Window {
    webkit?: { messageHandlers?: { board?: { postMessage: (message: OutgoingMessage) => void } } };
    boardHost: { receive: (message: unknown) => void };
  }
}
export const newId = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
export const sessionId = newId();
const listeners = new Set<(message: HostMessage) => void>();
const errors = new Set<(message: string) => void>();
let queued: HostMessage | undefined;
window.boardHost = {
  receive(input: unknown) {
    // Explicitly ignore old renderer responses instead of poisoning the current board.
    if (typeof input === 'object' && input !== null && 'sessionId' in input && input.sessionId !== undefined && input.sessionId !== sessionId) return;
    try {
      const message = parseHostMessage(input, sessionId);
      if (listeners.size === 0) queued = message;
      else listeners.forEach(listener => listener(message));
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Не удалось прочитать ответ приложения.';
      errors.forEach(listener => listener(message));
    }
  },
};
export function subscribe(receive: (message: HostMessage) => void, error: (message: string) => void) {
  listeners.add(receive); errors.add(error);
  if (queued) { const message = queued; queued = undefined; receive(message); }
  return () => { listeners.delete(receive); errors.delete(error); };
}
export function post(type: string, values: Record<string, unknown> = {}): boolean {
  const handler = window.webkit?.messageHandlers?.board;
  if (!handler) return false;
  handler.postMessage({ ...values, protocolVersion: PROTOCOL_VERSION, sessionId, type });
  return true;
}
