import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import type { ZoomCredentials } from './call-provider';
import './call-provider.css';

export type ZoomCallFrameHandle = { leave: () => void };
type Phase = 'loading' | 'joining' | 'joined' | 'left' | 'error';

/** The SDK lives in a stable same-origin document, isolated from product CSS/React.
 * Credentials stay in memory and are sent only to this exact child after ready.
 */
export const ZoomCallFrame = forwardRef<ZoomCallFrameHandle, {
  credentials: ZoomCredentials; onJoined?: () => void; onLeft?: () => void; onError?: (message: string) => void;
}>(function ZoomCallFrame({ credentials, onJoined, onLeft, onError }, ref) {
  const frame = useRef<HTMLIFrameElement>(null);
  const access = useRef(credentials);
  const cancelled = useRef(false);
  const callbacks = useRef({ onJoined, onLeft, onError });
  callbacks.current = { onJoined, onLeft, onError };
  const [phase, setPhase] = useState<Phase>('loading');
  const [error, setError] = useState('');
  useImperativeHandle(ref, () => ({ leave: () => {
    if (cancelled.current) return;
    cancelled.current = true;
    setPhase('left');
    frame.current?.contentWindow?.postMessage({ source: 'hr-zoom-parent', type: 'leave' }, window.location.origin);
  } }), []);
  useEffect(() => {
    let ready = false, joined = false, left = false, timedOut = false;
    const timeout = window.setTimeout(() => {
      if (ready || cancelled.current) return;
      timedOut = true;
      const message = 'Окно Zoom не загрузилось. Выйдите и повторите подключение.';
      setPhase('error'); setError(message); callbacks.current.onError?.(message);
    }, 15000);
    function receive(event: MessageEvent) {
      if (event.origin !== window.location.origin || event.source !== frame.current?.contentWindow) return;
      const data = event.data;
      if (!data || data.source !== 'hr-zoom-client' || timedOut) return;
      if (cancelled.current && data.phase !== 'left') return;
      if (data.type === 'ready' && !ready && !left) {
        ready = true; clearTimeout(timeout);
        frame.current?.contentWindow?.postMessage({ source: 'hr-zoom-parent', type: 'join', credentials: access.current }, window.location.origin);
        return;
      }
      if (data.type !== 'status' || !['loading', 'joining', 'joined', 'left', 'error'].includes(data.phase)) return;
      if (left) return;
      setPhase(data.phase);
      if (data.phase === 'joined' && !joined) { joined = true; callbacks.current.onJoined?.(); }
      if (data.phase === 'left') { left = true; clearTimeout(timeout); callbacks.current.onLeft?.(); }
      if (data.phase === 'error') {
        const message = 'Не удалось подключиться к Zoom. Выйдите и повторите подключение.';
        setError(message); callbacks.current.onError?.(message);
      }
    }
    window.addEventListener('message', receive);
    // Removing the iframe releases its document/media. Do not call Zoom's host
    // leaveMeeting here: that command can end the meeting for every participant.
    return () => { clearTimeout(timeout); window.removeEventListener('message', receive); };
  }, []);
  return <div className="md-zoom-call" data-phase={phase}>
    <iframe ref={frame} src="/call/zoom/client.html" title="Встреча Zoom" allow="camera; microphone; fullscreen; display-capture; autoplay" allowFullScreen referrerPolicy="no-referrer"/>
    {phase === 'loading' && <div className="md-zoom-status" role="status">Открываем Zoom…</div>}
    {phase === 'error' && <p className="md-zoom-error" role="alert">{error}</p>}
  </div>;
});
