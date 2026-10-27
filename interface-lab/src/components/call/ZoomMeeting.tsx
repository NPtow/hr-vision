import { useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { ZoomCallFrame } from './ZoomCallFrame';
import type { CallCredentials, ZoomCredentials } from './call-provider';
import { startZoomSessionReceipt } from './zoom-session-receipt';

type Props = {
  title: string; subtitle?: string; unavailable?: string; completed?: boolean;
  join: (signal: AbortSignal) => Promise<CallCredentials>;
  onJoined?: () => void | Promise<void>; onLeft?: () => void; onBack?: () => void;
  onOpenChange?: (open: boolean) => void;
  /** Product entry layout only; consent, access and the call remain owned here. */
  renderEntry?: (entry: { consent: boolean; onConsentChange: (value: boolean) => void; join: () => Promise<void>; opened: boolean }) => ReactNode;
  host?: {
    connected: (signal: AbortSignal) => Promise<unknown>;
    left: () => void;
  };
};

/** Product entry points supply access; this component owns the call.
 * No notes, resume, AI, device preview or provider selection lives here.
 */
export function ZoomMeeting(props: Props) {
  const callbacks = useRef(props); callbacks.current = props;
  const anchor = useRef<HTMLDivElement>(null);
  const request = useRef<AbortController | null>(null);
  const receipt = useRef<(() => void) | null>(null);
  const active = useRef(false);
  const mounted = useRef(false);
  const [consent, setConsent] = useState(false);
  const [opened, setOpened] = useState(false);
  const [credentials, setCredentials] = useState<ZoomCredentials | null>(null);
  const [frameGeneration, setFrameGeneration] = useState(0);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  function close() {
    active.current = false;
    request.current?.abort(); request.current = null;
    receipt.current?.(); receipt.current = null;
    setCredentials(null); setOpened(false); setFrameGeneration(value => value + 1);
  }
  function back() { close(); callbacks.current.onBack?.(); }
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false; active.current = false;
      request.current?.abort(); receipt.current?.();
    };
  }, []);
  useEffect(() => {
    if (props.completed && active.current) { close(); callbacks.current.onLeft?.(); }
  }, [props.completed]);
  useEffect(() => {
    callbacks.current.onOpenChange?.(opened);
    if (!opened) return;
    const shell = anchor.current?.closest<HTMLElement>('.cp-root, .rp-shell, .cj-app');
    const previousInert = shell?.inert;
    const overflow = document.body.style.overflow;
    const focus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (shell) shell.inert = true;
    document.body.style.overflow = 'hidden';
    return () => {
      if (shell) shell.inert = previousInert ?? false;
      document.body.style.overflow = overflow;
      callbacks.current.onOpenChange?.(false);
      if (focus?.isConnected) focus.focus();
    };
  }, [opened]);
  async function join() {
    if (active.current || !consent || props.unavailable || props.completed) return;
    const controller = new AbortController();
    request.current = controller; active.current = true;
    setError(''); setStatus(''); setOpened(true);
    try {
      const room = await callbacks.current.join(controller.signal);
      if (!mounted.current || controller.signal.aborted || request.current !== controller) return;
      if (room.provider !== 'zoom') throw new Error('Сервис встречи изменился. Откройте встречу заново.');
      setCredentials(room);
    } catch (cause) {
      if (!mounted.current || controller.signal.aborted || request.current !== controller) return;
      close(); setError(cause instanceof Error ? cause.message : 'Не удалось получить доступ к встрече.');
    }
  }
  function joined() {
    setError('');
    const current = request.current;
    // Optional product updates cannot throw into the SDK or replace its iframe.
    void Promise.resolve().then(() => callbacks.current.onJoined?.()).catch(() => {
      if (mounted.current && active.current && current === request.current) setStatus('Статус обновится позже. Звонок продолжается.');
    });
    const host = callbacks.current.host;
    if (credentials?.role !== 1 || !host) return;
    receipt.current?.();
    receipt.current = startZoomSessionReceipt({ request: host.connected,
      onPending: () => setStatus('Обновляем статус встречи. Звонок продолжается.'),
      onSuccess: () => setStatus(''),
      onExhausted: () => setStatus('Статус обновится позже. Звонок продолжается.'),
    });
  }
  function left() {
    const host = credentials?.role === 1 ? callbacks.current.host : undefined;
    close(); setStatus('');
    // Product bookkeeping outlives the window; it never delays local exit.
    if (host) host.left(); else callbacks.current.onLeft?.();
  }
  return <div ref={anchor} className="md-zoom-entry" style={props.renderEntry ? { display: 'contents' } : undefined}>
    {props.renderEntry ? props.renderEntry({ consent, onConsentChange: setConsent, join, opened }) : <>
    <h1>{props.title}</h1>{props.subtitle && <p>{props.subtitle}</p>}
    <label><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)}/>Согласен на запись встречи и сохранение видео в сервисе.</label>
    {props.unavailable && <p role="status">{props.unavailable}</p>}
    </>}
    {error && !opened && <p role="alert">{error}</p>}
    {status && <p role="status">{status}</p>}
    {!props.renderEntry && <div className="md-zoom-entry-actions">
      <button disabled={!consent || !!props.unavailable || props.completed || opened} onClick={() => void join()}>Присоединиться</button>
      {props.onBack && <button onClick={back}>Вернуться</button>}
    </div>}
    {(!props.unavailable || opened) && (!props.completed || opened) && createPortal(<div hidden={!opened} style={!opened ? { display: 'none' } : undefined} className="agency-product agency-product-theme md-zoom-overlay" role="dialog" aria-modal="true" aria-label={props.title}>
      <ZoomCallFrame key={frameGeneration} credentials={credentials} onJoined={joined} onLeft={left} onError={setError}/>
      {opened && !credentials && <div className="md-zoom-after-call md-zoom-access-pending"><p role="status">Получаем доступ к встрече…</p><button onClick={close}>Отменить</button></div>}
      {error && credentials && <div className="md-zoom-recovery"><button onClick={close}>Вернуться к подключению</button></div>}
    </div>, document.body)}
  </div>;
}
