import { useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import { FileText, Upload, X } from 'lucide-react';
import { Button } from '../../components/ui/button';

export type IntakePayload = { filename: string; contentBase64: string; contactEmail?: string };

export function CandidateIntake({ busy, onCreate, onClose }: { busy: boolean; onCreate: (payload: IntakePayload) => Promise<boolean>; onClose: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [email, setEmail] = useState('');
  const [reading, setReading] = useState(false);
  const [error, setError] = useState('');
  const working = busy || reading;
  function choose(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0];
    if (!selected) return;
    setFile(null); setError('');
    if (!/\.(pdf|docx|txt)$/i.test(selected.name)) { setError('Выберите PDF, DOCX или TXT.'); event.target.value = ''; return; }
    if (!selected.size || selected.size > 5 * 1024 * 1024) { setError('Нужен непустой файл размером до 5 МБ.'); event.target.value = ''; return; }
    setFile(selected);
  }
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!file || working) return;
    setReading(true); setError('');
    try {
      const contentBase64 = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error('Не удалось прочитать файл. Выберите его ещё раз.'));
        reader.onload = () => typeof reader.result === 'string' && reader.result.includes(',') ? resolve(reader.result.slice(reader.result.indexOf(',') + 1)) : reject(new Error('Не удалось прочитать файл.'));
        reader.readAsDataURL(file);
      });
      await onCreate({ filename: file.name, contentBase64, ...(email.trim() ? { contactEmail: email.trim() } : {}) });
    } catch (cause) { setError((cause as Error).message); }
    finally { setReading(false); }
  }
  return <form className="rp-card rp-intake" onSubmit={submit}>
    <div className="rp-section-heading"><h2>Добавить из CV</h2><Button type="button" variant="ghost" size="icon" aria-label="Закрыть добавление кандидата" disabled={working} onClick={onClose}><X size={18}/></Button></div>
    <p className="rp-muted">Подготовим профиль. Аккаунт кандидат создаст после знакомства.</p>
    <input ref={input} hidden type="file" accept=".pdf,.docx,.txt" aria-label="Резюме кандидата" disabled={working} onChange={choose}/>
    <div className="rp-upload"><span>{file ? <FileText size={24}/> : <Upload size={24}/>}</span><div><strong>{file?.name || 'Резюме кандидата'}</strong><p className="rp-muted">PDF, DOCX или TXT · до 5 МБ</p></div><Button type="button" variant="secondary" disabled={working} onClick={() => input.current?.click()}>{file ? 'Заменить' : 'Выбрать файл'}</Button></div>
    <label>Контактная почта · необязательно<input type="email" value={email} onChange={event => setEmail(event.target.value)} disabled={working} maxLength={254} autoComplete="off"/></label>
    {error && <p className="rp-error" role="alert">{error}</p>}
    <Button type="submit" disabled={!file || working}>{working ? 'Готовим профиль…' : 'Подготовить профиль'}</Button>
  </form>;
}
