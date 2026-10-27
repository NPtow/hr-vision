import { useEffect, useRef, useState } from 'react';
import { FileText, Upload } from 'lucide-react';
import * as pdfjs from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?worker&url';
import type { PDFDocumentProxy, RenderTask } from 'pdfjs-dist';
import './original-resume.css';

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

const MAX_BYTES = 20 * 1024 * 1024;
const DATABASE = 'hrv-coach-original-resumes-v1';
const STORE = 'originals';
type Original = { scope: string; name: string; mimeType: string; blob: Blob; savedAt: string };
export type OriginalResumeViewerProps = {
  scope: string;
  source?: { name: string; blob: Blob };
  allowLocalImport?: boolean;
  document?: { url: string; name: string; mimeType?: string };
};

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (!globalThis.indexedDB) { reject(new Error('Хранилище браузера недоступно.')); return; }
    let blocked = false;
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE)) request.result.createObjectStore(STORE, { keyPath: 'scope' });
    };
    request.onsuccess = () => {
      if (blocked) { request.result.close(); return; }
      request.result.onversionchange = () => request.result.close();
      resolve(request.result);
    };
    request.onerror = () => reject(request.error);
    request.onblocked = () => {
      blocked = true;
      reject(new Error('Закройте другое окно просмотра резюме и попробуйте снова.'));
    };
  });
}

async function readStored(scope: string): Promise<Original | null> {
  const db = await openDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = db.transaction(STORE, 'readonly');
      const request = transaction.objectStore(STORE).get(scope);
      request.onsuccess = () => {
        const value = request.result as Original | undefined;
        resolve(value?.scope === scope && value.blob instanceof Blob && value.blob.size > 0
          && value.blob.size <= MAX_BYTES && typeof value.name === 'string'
          && ['application/pdf', 'image/png', 'image/jpeg'].includes(value.mimeType) ? value : null);
      };
      request.onerror = () => reject(request.error);
      transaction.onabort = () => reject(transaction.error);
    });
  } finally { db.close(); }
}

async function storeOriginal(original: Original): Promise<void> {
  const db = await openDatabase();
  try {
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction(STORE, 'readwrite');
      transaction.objectStore(STORE).put(original);
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
  } finally { db.close(); }
}

function friendlyError(cause: unknown) {
  if (cause instanceof Error && cause.name === 'PasswordException') return 'Этот PDF защищён паролем. Выберите файл без пароля.';
  if (cause instanceof Error && cause.name === 'InvalidPDFException') return 'Не удалось прочитать PDF. Проверьте файл и попробуйте снова.';
  return cause instanceof Error ? cause.message : 'Не удалось открыть резюме.';
}

async function validateOriginal(blob: Blob, signal: AbortSignal): Promise<string> {
  if (!blob.size) throw new Error('Этот файл пуст.');
  if (blob.size > MAX_BYTES) throw new Error('Выберите файл до 20 МБ.');
  const signature = new Uint8Array(await blob.slice(0, 8).arrayBuffer());
  signal.throwIfAborted();
  if (new TextDecoder().decode(signature.slice(0, 5)) === '%PDF-') {
    const loading = pdfjs.getDocument({ data: new Uint8Array(await blob.arrayBuffer()) });
    const abort = () => { void loading.destroy().catch(() => {}); };
    signal.addEventListener('abort', abort, { once: true });
    try {
      signal.throwIfAborted();
      const pdf = await loading.promise;
      if (pdf.numPages < 1) throw new Error('В этом PDF нет страниц.');
      await pdf.getPage(1);
      signal.throwIfAborted();
      return 'application/pdf';
    } finally {
      signal.removeEventListener('abort', abort);
      await loading.destroy().catch(() => {});
    }
  }
  const png = signature.length >= 8 && [137, 80, 78, 71, 13, 10, 26, 10].every((value, i) => signature[i] === value);
  const jpeg = signature[0] === 255 && signature[1] === 216 && signature[2] === 255;
  if (!png && !jpeg) throw new Error('Выберите резюме в PDF, JPG или PNG.');
  const url = URL.createObjectURL(new Blob([blob], { type: png ? 'image/png' : 'image/jpeg' }));
  try {
    await new Promise<void>((resolve, reject) => {
      const image = new Image();
      const abort = () => { image.src = ''; reject(signal.reason); };
      image.onload = () => { signal.removeEventListener('abort', abort); resolve(); };
      image.onerror = () => { signal.removeEventListener('abort', abort); reject(new Error('Не удалось прочитать изображение.')); };
      signal.addEventListener('abort', abort, { once: true });
      if (signal.aborted) { abort(); return; }
      image.src = url;
    });
    signal.throwIfAborted();
    return png ? 'image/png' : 'image/jpeg';
  } finally { URL.revokeObjectURL(url); }
}

async function loadServerOriginal(url: string, signal: AbortSignal): Promise<Blob> {
  const parsed = new URL(url, window.location.href);
  if (parsed.origin !== window.location.origin || !['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error('Файл должен быть доступен в этом сервисе.');
  }
  const response = await fetch(parsed, { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal });
  if (!response.ok) throw new Error('Не удалось загрузить оригинал резюме.');
  if (Number(response.headers.get('content-length') || 0) > MAX_BYTES) throw new Error('Размер резюме превышает 20 МБ.');
  if (!response.body) {
    const blob = await response.blob();
    if (blob.size > MAX_BYTES) throw new Error('Размер резюме превышает 20 МБ.');
    return blob;
  }
  const reader = response.body.getReader();
  const parts: ArrayBuffer[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BYTES) { await reader.cancel(); throw new Error('Размер резюме превышает 20 МБ.'); }
      parts.push(value.slice().buffer);
    }
  } finally { reader.releaseLock(); }
  return new Blob(parts, { type: response.headers.get('content-type') || '' });
}

function PdfPage({ pdf, number, width }: { pdf: PDFDocumentProxy; number: number; width: number }) {
  const container = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const previousRender = useRef<Promise<unknown>>(Promise.resolve());
  const [visible, setVisible] = useState(false);
  const [height, setHeight] = useState(0);
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    const target = container.current;
    if (!target) return;
    if (!globalThis.IntersectionObserver) { setVisible(true); return; }
    const observer = new IntersectionObserver(entries => setVisible(entries[0].isIntersecting), { rootMargin: '700px 0px' });
    observer.observe(target);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!visible || width < 1 || !canvas.current) return;
    let active = true;
    let render: RenderTask | undefined;
    const element = canvas.current;
    // Capture before assigning the next task; never render concurrently into a canvas.
    const pending = previousRender.current;
    const next = pending.catch(() => {}).then(() => {
      if (!active) return;
      return renderPage();
    });
    async function renderPage() {
      const page = await pdf.getPage(number);
      if (!active) return;
      const original = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({ scale: width / original.width });
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      setHeight(viewport.height);
      setError('');
      element.width = Math.ceil(viewport.width * ratio);
      element.height = Math.ceil(viewport.height * ratio);
      element.style.width = `${viewport.width}px`;
      element.style.height = `${viewport.height}px`;
      render = page.render({ canvas: element, viewport, transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0] });
      await render.promise;
      if (!active) return;
      const content = await page.getTextContent();
      if (active) setText(content.items.map(item => 'str' in item ? item.str + (item.hasEOL ? '\n' : ' ') : '').join(''));
    }
    previousRender.current = next.catch(cause => {
      if (active && !(cause instanceof Error && cause.name === 'RenderingCancelledException')) setError('Не удалось показать эту страницу.');
    });
    return () => {
      active = false;
      render?.cancel();
      void next.catch(() => {}).then(() => {
        if (!element.isConnected) { element.width = 0; element.height = 0; }
      });
    };
  }, [pdf, number, width, visible]);
  return <div ref={container} className="rcc-original-page" style={{ minHeight: height || width * Math.SQRT2 }} aria-label={`Страница ${number}`}>
    {visible && <canvas ref={canvas} role="img" aria-label={`Страница ${number} оригинала резюме`}/>}
    {text && <div className="rcc-original-accessible">{text}</div>}
    {error && <p className="rcc-original-page-error" role="status">{error}</p>}
  </div>;
}

function PdfDocument({ blob }: { blob: Blob }) {
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const update = () => setWidth(Math.max(1, Math.floor(element.getBoundingClientRect().width)));
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let active = true;
    let loading: ReturnType<typeof pdfjs.getDocument> | undefined;
    setPdf(null); setError('');
    void blob.arrayBuffer().then(async buffer => {
      if (!active) return;
      loading = pdfjs.getDocument({ data: new Uint8Array(buffer) });
      const document = await loading.promise;
      if (active) setPdf(document);
    }).catch(cause => { if (active) setError(friendlyError(cause)); });
    return () => { active = false; if (loading) void loading.destroy().catch(() => {}); };
  }, [blob]);
  return <div className="rcc-original-pdf" ref={container} aria-busy={!pdf && !error}>
    {error ? <p className="rcc-original-error" role="alert">{error}</p>
      : pdf ? Array.from({ length: pdf.numPages }, (_, index) => <PdfPage key={index} pdf={pdf} number={index + 1} width={width}/>)
      : <p className="rcc-original-loading" role="status">Открываем резюме…</p>}
  </div>;
}

function OriginalImage({ original }: { original: Original }) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    const next = URL.createObjectURL(new Blob([original.blob], { type: original.mimeType }));
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [original.blob, original.mimeType]);
  return url ? <img className="rcc-original-image" src={url} alt={`Оригинал резюме: ${original.name}`}/> : null;
}

/** Original bytes only. A local selection is never uploaded by this component. */
export function OriginalResumeViewer({ scope, document: supplied, source, allowLocalImport = true }: OriginalResumeViewerProps) {
  const input = useRef<HTMLInputElement>(null);
  const request = useRef<AbortController | null>(null);
  const [original, setOriginal] = useState<Original | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [storageError, setStorageError] = useState('');
  const visible = original?.scope === scope ? original : null;
  useEffect(() => {
    const controller = new AbortController();
    request.current?.abort();
    request.current = controller;
    setOriginal(null); setError(''); setStorageError(''); setBusy(true);
    void (async () => {
      if (source) {
        const mimeType = await validateOriginal(source.blob, controller.signal);
        if (!controller.signal.aborted) setOriginal({scope,name:source.name,blob:source.blob,mimeType,savedAt:''});
      } else if (supplied?.url) {
        const blob = await loadServerOriginal(supplied.url, controller.signal);
        const mimeType = await validateOriginal(blob, controller.signal);
        if (!controller.signal.aborted) setOriginal({ scope, name: supplied.name, blob, mimeType, savedAt: '' });
      } else if (allowLocalImport) {
        const saved = await readStored(scope);
        if (!controller.signal.aborted) setOriginal(saved);
      }
    })().catch(cause => {
      if (!controller.signal.aborted) {
        if (supplied?.url || source) setError(friendlyError(cause));
        else setStorageError('Не удалось прочитать сохранённый файл. Можно выбрать его снова.');
      }
    }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => { controller.abort(); request.current?.abort(); };
  }, [scope, supplied?.url, supplied?.name, source, allowLocalImport]);
  async function choose(file: File) {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setBusy(true); setError(''); setStorageError('');
    try {
      const mimeType = await validateOriginal(file, controller.signal);
      const next: Original = { scope, blob: file, name: file.name, mimeType, savedAt: new Date().toISOString() };
      controller.signal.throwIfAborted();
      // Only a successfully read original can replace the existing stored file.
      try { await storeOriginal(next); }
      catch { if (!controller.signal.aborted) setStorageError('Файл открыт, но не сохранился в этом браузере.'); }
      if (!controller.signal.aborted) setOriginal(next);
    } catch (cause) { if (!controller.signal.aborted) setError(friendlyError(cause)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <section className="rcc-original" aria-label="Оригинал резюме" aria-busy={busy}>
    <input ref={input} className="rcc-original-input" type="file" accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png" tabIndex={-1} aria-label="Выбрать оригинал резюме"
      onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void choose(file); }}/>
    {visible ? <>
      <header className="rcc-original-header">
        {supplied?.url || source || !allowLocalImport ? <span className="rcc-original-filename"><FileText size={16}/><span>{visible.name}</span></span>
          : <button className="rcc-original-filename" title="Выбрать другой файл" aria-label={`Выбрать другой файл. Сейчас: ${visible.name}`} disabled={busy} onClick={() => input.current?.click()}><FileText size={16}/><span>{visible.name}</span></button>}
        {busy && <span className="rcc-original-pending" role="status">Открываем…</span>}
      </header>
      <div className="rcc-original-document">
        {visible.mimeType === 'application/pdf' ? <PdfDocument key={visible.savedAt || supplied?.url} blob={visible.blob}/> : <OriginalImage original={visible}/>}
      </div>
    </> : <div className="rcc-original-empty">
      <FileText size={30} strokeWidth={1.5} aria-hidden="true"/>
      <p>{busy ? 'Открываем резюме…' : 'Оригинал резюме не добавлен'}</p>
      {!busy && allowLocalImport && <button className="rcc-original-choose" onClick={() => input.current?.click()}><Upload size={16}/>Выбрать файл</button>}
    </div>}
    {(error || storageError) && <div className="rcc-original-error" role="alert">{error || storageError}</div>}
  </section>;
}
