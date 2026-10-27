import { useEffect, useRef, useState } from 'react';
import { Camera, LoaderCircle } from 'lucide-react';
import { deviceAccessMessage, waitForPreview } from '../scenes/candidate-portal/call-connection';
import './call-device-access.css';

/** Request from the button's event handler, before any API/SDK await. A late
 * browser grant after leaving or skipping must never keep devices running. */
export function useCallDeviceAccess() {
  const controller = useRef<AbortController | null>(null);
  const current = useRef<MediaStream | null>(null);
  const alive = useRef(true);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState('');
  function stop() {
    controller.current?.abort();
    controller.current = null;
    current.current?.getTracks().forEach(track => track.stop());
    current.current = null;
    if (alive.current) { setStream(null); setChecking(false); }
  }
  function request(devices: { audio: boolean; video: boolean }): Promise<boolean> {
    stop(); setError('');
    if (!devices.audio && !devices.video) return Promise.resolve(true);
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Браузер не предоставляет доступ к устройствам. Откройте ссылку в Safari или Chrome.');
      return Promise.resolve(false);
    }
    const attempt = new AbortController();
    controller.current = attempt;
    setChecking(true);
    // Browser permission UI can be outside the fullscreen document. Reveal the
    // browser chrome, while keeping getUserMedia in this same click handler.
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
    // Do not move this call into an effect: the explicit user gesture matters.
    let capture: Promise<MediaStream>;
    try { capture = navigator.mediaDevices.getUserMedia(devices); }
    catch (cause) { capture = Promise.reject(cause); }
    return waitForPreview(capture, attempt.signal, 20000)
      .then(media => {
        if (!alive.current || controller.current !== attempt || attempt.signal.aborted) {
          media.getTracks().forEach(track => track.stop()); return false;
        }
        current.current = media; setStream(media);
        return true;
      }).catch(cause => {
        if (alive.current && controller.current === attempt && !attempt.signal.aborted) setError(deviceAccessMessage(cause));
        return false;
      }).finally(() => {
        if (alive.current && controller.current === attempt) setChecking(false);
      });
  }
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; stop(); };
  }, []);
  return { stream, checking, error, request, stop };
}

export function CallDeviceAccess({ checking, error, onRequest, onAudioOnly, onSkip, disabled = false, skipDisabled = false }: {
  checking: boolean; error: string; onRequest: () => void; onAudioOnly?: () => void; onSkip: () => void; disabled?: boolean; skipDisabled?: boolean;
}) {
  return <div className="call-device-access">
    <Camera size={28} aria-hidden="true"/>
    <h2>Камера и микрофон</h2>
    <p role="status">{checking ? 'В запросе браузера нажмите «Разрешить».' : 'Разрешите доступ, чтобы собеседник видел и слышал вас.'}</p>
    {error && <p className="call-device-access-error" role="alert">{error}</p>}
    <button className="call-device-access-primary" disabled={disabled || checking} onClick={onRequest}>
      {checking && <LoaderCircle size={17} className="call-device-access-spinner"/>}
      {checking ? 'Ждём разрешения…' : error ? 'Проверить доступ ещё раз' : 'Разрешить камеру и микрофон'}
    </button>
    {error && onAudioOnly && <button className="call-device-access-skip" disabled={disabled || checking} onClick={onAudioOnly}>Продолжить только с микрофоном</button>}
    <p className="call-device-access-help">Нет запроса? Откройте настройки сайта рядом с адресом, разрешите камеру и микрофон. В Telegram откройте ссылку во внешнем браузере.</p>
    <button className="call-device-access-skip" disabled={disabled || skipDisabled} onClick={onSkip}>Войти без камеры и микрофона</button>
  </div>;
}
