import { useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import { Download, FileText, Upload } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { candidateRequest, type CandidateResume } from './api';

const MAX_RESUME_BYTES = 5 * 1024 * 1024;
const acceptedExtensions = /\.(pdf|docx|txt)$/i;
const fileSize = (bytes: number) => bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1).replace('.', ',')} МБ` : `${Math.max(1, Math.round(bytes / 1024))} КБ`;

function base64File(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Не удалось прочитать файл. Выберите его ещё раз.'));
    reader.onload = () => {
      if (typeof reader.result !== 'string' || !reader.result.includes(',')) { reject(new Error('Не удалось прочитать файл. Выберите его ещё раз.')); return; }
      resolve(reader.result.slice(reader.result.indexOf(',') + 1));
    };
    reader.readAsDataURL(file);
  });
}

export function ResumeUpload({ resume, busy, onUpload, compact = false }: {
  resume: CandidateResume | null; busy: boolean; onUpload: (payload: { filename: string; contentBase64: string }) => Promise<boolean>; compact?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [reading, setReading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState('');
  const working = busy || reading;
  function choose(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0];
    if (!selected) return;
    setError(''); setFile(null);
    if (!acceptedExtensions.test(selected.name)) { setError('Выберите резюме в PDF, DOCX или TXT.'); event.target.value = ''; return; }
    if (!selected.size) { setError('Этот файл пустой. Выберите другое резюме.'); event.target.value = ''; return; }
    if (selected.size > MAX_RESUME_BYTES) { setError('Размер резюме должен быть не больше 5 МБ.'); event.target.value = ''; return; }
    setFile(selected);
  }
  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!file || working) return;
    setError(''); setReading(true);
    try {
      const contentBase64 = await base64File(file);
      if (await onUpload({ filename: file.name, contentBase64 })) { setFile(null); if (input.current) input.current.value = ''; }
    } catch (cause) { setError((cause as Error).message); }
    finally { setReading(false); }
  }
  async function download() {
    if (downloading) return;
    setDownloading(true); setError('');
    try {
      const result = await candidateRequest<{ filename: string; mime: string; contentBase64: string }>('resume');
      const binary = atob(result.contentBase64);
      const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: result.mime }));
      const link = document.createElement('a'); link.href = url; link.download = result.filename; link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) { setError((cause as Error).message); }
    finally { setDownloading(false); }
  }
  return <section className={`cp-resume ${compact ? 'cp-resume-compact' : ''}`}>
    {compact && <h2>Резюме</h2>}
    {resume && <div className="cp-resume-current"><FileText size={23}/><div><strong>{resume.filename}</strong><span>{fileSize(resume.size)}</span></div><Button variant="ghost" size="icon" aria-label="Скачать резюме" disabled={downloading} onClick={download}><Download size={18}/></Button></div>}
    <form onSubmit={upload}>
      <input ref={input} className="cp-file-input" type="file" hidden accept=".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain" aria-label="Файл резюме" onChange={choose} disabled={working}/>
      <div className={`cp-upload-area ${file ? 'has-file' : ''}`}>
        {!compact && <span className="cp-large-icon">{file ? <FileText size={26}/> : <Upload size={26}/>}</span>}
        {file ? <div className="cp-upload-selected"><strong>{file.name}</strong><span>{fileSize(file.size)}</span></div> : !compact && <p>PDF, DOCX или TXT · до 5 МБ</p>}
        <Button type="button" variant="secondary" disabled={working} onClick={() => input.current?.click()}>{file ? 'Выбрать другой файл' : resume ? 'Заменить резюме' : 'Выбрать файл'}</Button>
      </div>
      {error && <p className="cp-error" role="alert">{error}</p>}
      {file && <div className="cp-form-actions"><Button type="submit" disabled={working}>{working ? 'Загружаем резюме…' : compact ? 'Сохранить резюме' : 'Продолжить'}</Button></div>}
    </form>
  </section>;
}
