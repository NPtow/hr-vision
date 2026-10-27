import { useRef, useState } from 'react';
import { Button } from '../../components/ui/button';

type Scope = { source: 'intake'; vacancyId: string; id: string } | {
  source: 'screening' | 'meeting'; vacancyId: string; candidate: string; applicationId: string;
  generation: string; meetingId?: string;
};
type Upload = { id: string; status: string; parts: { index: number; status: string }[] };

const mime = (file: File) => file.type || ({ mp4: 'video/mp4', mov: 'video/quicktime', webm: 'video/webm',
  wav: 'audio/wav', mp3: 'audio/mpeg', m4a: 'audio/mp4', ogg: 'audio/ogg', flac: 'audio/flac' }[file.name.split('.').pop()?.toLowerCase() || ''] || '');

/** One stable product intent survives retries/reloads; only the source service hashes bytes. */
export function RecordingUpload({ token, scope, onUploaded }: { token: string; scope: Scope; onUploaded: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const running = useRef(false);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  async function send(path: string, body: object | File): Promise<Upload> {
    const raw = body instanceof File;
    const response = await fetch('/api/hr/employer/recordings/uploads' + path, {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': raw ? 'application/octet-stream' : 'application/json' },
      body: raw ? body : JSON.stringify(body), cache: 'no-store', signal: AbortSignal.timeout(15 * 60 * 1000),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Не удалось сохранить запись. Повторите загрузку.');
    return result;
  }
  async function upload() {
    if (running.current || !files.length) return;
    running.current = true; setBusy(true); setError('');
    try {
      if (files.length > 10 || files.some(file => !file.size || file.size > 1024 ** 3) || files.reduce((n, f) => n + f.size, 0) > 2 * 1024 ** 3) {
        throw new Error('До 10 частей, до 1 ГБ каждая и до 2 ГБ суммарно.');
      }
      const manifest = files.map(file => ({ name: file.name, size: file.size, content_type: mime(file) }));
      const seed = JSON.stringify([scope, files.map(file => [file.name, file.size, file.lastModified, mime(file)])]);
      const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(seed)))].map(b => b.toString(16).padStart(2, '0')).join('');
      const storageKey = 'hr-recording-upload:' + digest;
      const requestId = sessionStorage.getItem(storageKey) || crypto.randomUUID();
      sessionStorage.setItem(storageKey, requestId);
      setMessage('Готовим загрузку…');
      let state = await send('', { ...scope, requestId, files: manifest });
      if (state.status !== 'registered') {
        for (let index = 0; index < files.length; index++) {
          if (state.parts[index]?.status === 'ready') continue;
          setMessage(`Загружаем часть ${index + 1} из ${files.length}…`);
          await send(`/${state.id}/parts/${index}`, files[index]);
        }
      }
      // Source recovery may already have committed its receipt after a lost
      // response. Repeat the product acknowledgement to repair its projection.
      setMessage('Сохраняем запись…');
      state = await send(`/${state.id}/finalize`, {});
      if (state.status !== 'registered') throw new Error('Сохранение ещё не завершено. Повторите загрузку.');
      setMessage('Запись сохранена. Расшифровка появится здесь автоматически.');
      setFiles([]); if (input.current) input.current.value = '';
      onUploaded();
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Загрузка прервалась. Повторите её.'); }
    finally { running.current = false; setBusy(false); }
  }
  return <div className="recording-upload" style={{ padding: '12px 0' }}>
    <input ref={input} type="file" multiple accept=".mp4,.mov,.webm,.wav,.mp3,.m4a,.ogg,.flac" aria-label="Части записи интервью"
      disabled={busy} onChange={event => { setFiles(Array.from(event.target.files || [])); setError(''); setMessage(''); }} />
    {files.length > 0 && <>
      <ol>{files.map((file, i) => <li key={`${i}:${file.name}`}>{file.name}
        {!busy && i > 0 && <Button variant="ghost" onClick={() => setFiles(value => { const next = [...value]; [next[i - 1], next[i]] = [next[i], next[i - 1]]; return next; })} aria-label={`Переместить ${file.name} выше`}>↑</Button>}
      </li>)}</ol>
      <Button type="button" disabled={busy} onClick={() => void upload()}>{busy ? 'Загружаем…' : error ? 'Повторить загрузку' : 'Добавить запись'}</Button>
    </>}
    {message && <p role="status">{message}</p>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
