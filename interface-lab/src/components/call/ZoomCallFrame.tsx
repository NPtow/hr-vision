import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import type { ZoomCredentials } from './call-provider';
import './call-provider.css';

export type ZoomCallFrameHandle = { leave: () => void };
type Phase = 'loading' | 'prepared' | 'joining' | 'joined' | 'left' | 'error';

/** The SDK lives in a stable same-origin document, isolated from product CSS/React.
 * Credentials stay in memory and are sent only to this exact child after ready.
 */
export const ZoomCallFrame = forwardRef<ZoomCallFrameHandle, {
  credentials: ZoomCredentials | null; onJoined?: () => void; onLeft?: () => void; onError?: (message: string) => void;
}>(function ZoomCallFrame({ credentials, onJoined, onLeft, onError }, ref) {
  const frame = useRef<HTMLIFrameElement>(null);
  const access = useRef(credentials);
  const sendJoin = useRef<(() => void) | null>(null);
  const sent = useRef(false);
  const failed = useRef(false);
  const [generation, setGeneration] = useState(0);
  const [retryable, setRetryable] = useState(false);
  if (!sent.current) access.current = credentials;
  const cancelled = useRef(false);
  const callbacks = useRef({ onJoined, onLeft, onError });
  callbacks.current = { onJoined, onLeft, onError };
  const [phase, setPhase] = useState<Phase>('loading');
  const [error, setError] = useState('');
  const retry = () => {
    callbacks.current.onError?.('');
    setError(''); setPhase('loading'); setRetryable(false);
    access.current = credentials;
    setGeneration(value => value + 1);
  };
  useImperativeHandle(ref, () => ({ leave: () => {
    if (cancelled.current) return;
    cancelled.current = true;
    setPhase('left');
    frame.current?.contentWindow?.postMessage({ source: 'hr-zoom-parent', type: 'leave' }, window.location.origin);
  } }), []);
  useEffect(() => {
    cancelled.current = false; sent.current = false; failed.current = false;
    let ready = false, joined = false, left = false, timedOut = false;
    const dispatch = () => {
      if (!ready || sent.current || !access.current || left || timedOut || cancelled.current) return;
      sent.current = true;
      frame.current?.contentWindow?.postMessage({ source: 'hr-zoom-parent', type: 'join', credentials: access.current }, window.location.origin);
    };
    sendJoin.current = dispatch;
    const timeout = window.setTimeout(() => {
      if (ready || cancelled.current) return;
      timedOut = true;
      const message = 'Окно Zoom не загрузилось. Выйдите и повторите подключение.';
      failed.current = true; setRetryable(true);
      setPhase('error'); setError(message); callbacks.current.onError?.(message);
    }, 15000);
    function receive(event: MessageEvent) {
      if (event.origin !== window.location.origin || event.source !== frame.current?.contentWindow) return;
      const data = event.data;
      if (!data || data.source !== 'hr-zoom-client' || timedOut) return;
      if (cancelled.current && data.phase !== 'left') return;
      if (data.type === 'ready' && !ready && !left) {
        ready = true; clearTimeout(timeout);
        if (access.current) dispatch();
        else frame.current?.contentWindow?.postMessage({ source: 'hr-zoom-parent', type: 'prepare' }, window.location.origin);
        return;
      }
      if (data.type !== 'status' || !['loading', 'prepared', 'joining', 'joined', 'left', 'error'].includes(data.phase)) return;
      if (left || failed.current || (joined && ['loading', 'prepared'].includes(data.phase))) return;
      setPhase(data.phase);
      if (data.phase === 'joined' && !joined) { joined = true; callbacks.current.onJoined?.(); }
      if (data.phase === 'left') { left = true; clearTimeout(timeout); callbacks.current.onLeft?.(); }
      if (data.phase === 'error') {
        failed.current = true;
        const loading = ['sdk_load', 'sdk_assets', 'sdk_load_timeout'].includes(data.code);
        setRetryable(loading && !joined);
        const message = loading ? 'Не удалось загрузить файлы встречи. Повторите загрузку.' : 'Не удалось подключиться к Zoom. Выйдите и повторите подключение.';
        setError(message); callbacks.current.onError?.(message);
      }
    }
    window.addEventListener('message', receive);
    // Removing the iframe releases its document/media. Do not call Zoom's host
    // leaveMeeting here: that command can end the meeting for every participant.
    return () => { sendJoin.current = null; clearTimeout(timeout); window.removeEventListener('message', receive); };
  }, [generation]);
  useEffect(() => {
    if (!credentials) return;
    if (failed.current && !sent.current) retry();
    else sendJoin.current?.();
  }, [credentials]);
  return <div className="md-zoom-call" data-phase={phase}>
    <iframe key={generation} ref={frame} src="/call/zoom/client.html" title="Встреча Zoom" allow="camera; microphone; fullscreen; display-capture; autoplay" allowFullScreen referrerPolicy="no-referrer"/>
    {phase === 'loading' && <div className="md-zoom-status" role="status">Открываем Zoom…</div>}
    {phase === 'error' && <div className="md-zoom-error" role="alert">{error}{retryable && <button type="button" onClick={retry}>Повторить загрузку</button>}</div>}
  </div>;
});
