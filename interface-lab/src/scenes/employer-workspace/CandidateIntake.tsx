import { useRef, useState, type FormEvent } from 'react';
import { ArrowLeft, FileText, Link2, Loader2, Plus, Trash2, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { request } from '../connected-journey/api';
import { downloadResume, type IntakePerson } from './intake';
import type { Scope } from './EmployerShell';

type Draft = { id: string; name: string; role: string; contact: string; link: string; note: string; file?: File };
const blank = (): Draft => ({ id: crypto.randomUUID(), name: '', role: '', contact: '', link: '', note: '' });
const allowed = /\.(pdf|doc|docx|rtf)$/i;
const file64 = (file: File) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = () => reject(new Error('Не удалось прочитать файл.')); reader.readAsDataURL(file);
});

export function CandidateIntake({ token, scope, onBack, onSaved }: { token: string; scope: Scope; vacancy: string; onBack: () => void; onSaved: (id: string) => void }) {
  const [mode, setMode] = useState<'files' | 'link' | 'manual'>('files');
  const [drafts, setDrafts] = useState<Draft[]>([blank()]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const batch = useRef(crypto.randomUUID());
  const files = useRef<HTMLInputElement>(null);
  function edit(id: string, key: keyof Draft, value: string) { setDrafts(all => all.map(d => d.id === id ? { ...d, [key]: value } : d)); }
  function changeMode(next: typeof mode) { setMode(next); setError(''); if (next !== 'files' && !drafts.length) setDrafts([blank()]); }
  function addFiles(incoming: File[]) {
    setError('');
    const accepted = incoming.filter(f => allowed.test(f.name) && f.size > 0 && f.size <= 5 * 1024 * 1024);
    if (accepted.length !== incoming.length) { setError('Подойдут PDF, DOC, DOCX или RTF до 5 МБ. Проверьте файлы.'); return; }
    const existing = drafts.filter(d => d.file || d.name.trim() || d.contact.trim() || d.link.trim());
    if (existing.length + accepted.length > 10 || [...existing.map(d => d.file?.size || 0), ...accepted.map(f => f.size)].reduce((a, b) => a + b, 0) > 8 * 1024 * 1024) { setError('За один раз можно добавить до 10 кандидатов и 8 МБ файлов.'); return; }
    setDrafts([...existing, ...accepted.map(file => ({ ...blank(), file, name: file.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ') }))]);
  }
  const entriesToSave = mode === 'files' ? drafts.filter(d => d.file) : drafts;
  async function save(e: FormEvent) {
    e.preventDefault(); if (busy) return;
    if (!entriesToSave.length || entriesToSave.some(d => !d.name.trim())) { setError('Укажите имя каждого кандидата.'); return; }
    if (mode === 'link' && entriesToSave.some(d => !d.link.trim())) { setError('Добавьте ссылку на резюме.'); return; }
    setBusy(true); setError('');
    try {
      const entries = await Promise.all(entriesToSave.map(async d => ({ name: d.name, role: d.role, contact: d.contact, link: d.link, note: d.note,
        ...(d.file ? { file: { name: d.file.name, data: await file64(d.file) } } : {}) })));
      const result = await request<{ candidates: IntakePerson[] }>('intake', token, { batch: batch.current, scope, entries });
      onSaved(result.candidates[0].id);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <section className="ew-form-page">
    <button className="ew-text-back" disabled={busy} onClick={onBack}><ArrowLeft size={16}/>К кандидатам</button>
    <header className="ew-form-heading"><h1>Добавить кандидатов</h1></header>
    <form onSubmit={save}>
      <div className="ew-methods" aria-label="Способ добавления">{[{ id: 'files', label: 'Резюме' }, { id: 'link', label: 'По ссылке' }, { id: 'manual', label: 'Вручную' }].map(m => <button key={m.id} type="button" disabled={busy} aria-pressed={mode === m.id} onClick={() => changeMode(m.id as typeof mode)}>{m.label}</button>)}</div>
      {mode === 'files' && <><input ref={files} type="file" multiple accept=".pdf,.doc,.docx,.rtf" className="ew-file-input" aria-label="Выбрать резюме" onChange={e => { addFiles(Array.from(e.target.files || [])); e.target.value = ''; }}/><div className={`ew-dropzone ${drag ? 'ew-drag' : ''}`} onDragOver={e => { e.preventDefault(); if (!busy) setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={e => { e.preventDefault(); setDrag(false); if (!busy) addFiles(Array.from(e.dataTransfer.files)); }}><strong>Перетащите резюме сюда</strong><span>Можно сразу несколько · PDF, DOCX, DOC, RTF</span><Button type="button" variant="outline" disabled={busy} onClick={() => files.current?.click()}>Выбрать файлы</Button></div></>}
      {entriesToSave.length > 0 && <div className="ew-intake-entries"><h2>К добавлению · {entriesToSave.length}</h2>{entriesToSave.map((d, index) => mode === 'files' ? <section key={d.id} className="ew-upload-row"><div><label><span className="sr-only">Имя кандидата {index + 1}</span><input required maxLength={140} value={d.name} disabled={busy} onChange={e => edit(d.id, 'name', e.target.value)}/></label><span>{d.file?.name}</span></div><button type="button" aria-label={`Убрать резюме ${d.file?.name}`} disabled={busy} onClick={() => setDrafts(all => all.filter(p => p.id !== d.id))}><X size={16}/></button></section> : <section key={d.id} className="ew-intake-card"><div className="ew-intake-card-top"><span>{drafts.length > 1 ? `Кандидат ${index + 1}` : ''}</span>{drafts.length > 1 && <button type="button" aria-label={`Убрать кандидата ${index + 1}`} disabled={busy} onClick={() => setDrafts(all => all.filter(p => p.id !== d.id))}><X size={17}/></button>}</div><div className="ew-fields"><label>Имя и фамилия<input required maxLength={140} autoComplete="off" value={d.name} disabled={busy} onChange={e => edit(d.id, 'name', e.target.value)} placeholder="Например, Анна Миронова"/></label><label>Должность<input maxLength={200} value={d.role} disabled={busy} onChange={e => edit(d.id, 'role', e.target.value)} placeholder="Менеджер по работе с клиентами"/></label><label>Почта или телефон<input maxLength={160} value={d.contact} disabled={busy} onChange={e => edit(d.id, 'contact', e.target.value)} placeholder="Контакт кандидата"/></label>{(mode === 'link' || d.link) && <label>Ссылка на резюме<input type="url" required={mode === 'link'} maxLength={2048} value={d.link} disabled={busy} onChange={e => edit(d.id, 'link', e.target.value)} placeholder="https://hh.ru/resume/…"/></label>}</div><details className="ew-extra"><summary>Добавить заметку</summary><label><span className="sr-only">Заметка о кандидате {index + 1}</span><textarea value={d.note} maxLength={4000} disabled={busy} onChange={e => edit(d.id, 'note', e.target.value)} placeholder="Что важно учесть при знакомстве"/></label></details></section>)}</div>}
      {mode !== 'files' && <button type="button" className="ew-add-another" disabled={busy || drafts.length >= 10} onClick={() => setDrafts(all => [...all, blank()])}><Plus size={16}/>Ещё кандидат</button>}
      {mode === 'link' && <p className="ew-form-note">Ссылка сохранится в карточке. Имя и остальные данные заполните выше.</p>}
      {error && <p className="ew-inline-error" role="alert">{error}</p>}
      <footer className="ew-form-footer"><Button type="button" variant="ghost" disabled={busy} onClick={onBack}>Отмена</Button><Button type="submit" disabled={busy || !entriesToSave.length}>{busy ? <><Loader2 className="cj-spin" size={17}/>Сохраняем…</> : entriesToSave.length > 1 ? `Добавить ${entriesToSave.length} кандидатов` : 'Добавить кандидата'}</Button></footer>
    </form>
  </section>;
}

export function IntakeDetail({ person, token, onArchived }: { person: IntakePerson; token: string; onArchived: () => void }) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  async function archive() {
    setBusy(true); setError('');
    try { await request('intake/archive', token, { id: person.id, archived: !person.archived }); onArchived(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <div className="ew-intake-detail"><header className="ew-person-heading"><div><span className="ew-status">{person.archived ? 'В архиве' : 'Добавлен вручную'}</span><h2>{person.name}</h2><p>{person.role || 'Должность не указана'}</p></div></header><section className="ew-detail-card"><h3>Резюме</h3>{person.files.map(f => <button className="ew-file-row" key={f.id} onClick={() => { void downloadResume(token, f).catch(e => setError(e.message)); }}><FileText size={20}/><span><strong>{f.name}</strong><small>{Math.ceil(f.size / 1024)} КБ</small></span><span>Скачать</span></button>)}{person.link && <a className="ew-file-row" href={person.link} target="_blank" rel="noreferrer"><Link2 size={18}/>Открыть резюме ↗</a>}{!person.link && !person.files.length && <p>Резюме ещё не добавлено.</p>}{person.contact && <><h3>Контакт</h3><p>{person.contact}</p></>}{person.note && <><h3>Заметка</h3><p className="ew-prewrap">{person.note}</p></>}</section><div className="ew-empty-recording"><FileText size={24}/><div><h3>Интервью ещё нет</h3><p>Здесь появятся запись и ключевые моменты.</p></div></div><p className="ew-form-note">Для назначения встречи нужно согласие кандидата и его свободное время.</p><details className="ew-extra" open={confirm} onToggle={e => setConfirm(e.currentTarget.open)}><summary>Действия с карточкой</summary><Button variant="ghost" disabled={busy} onClick={archive}><Trash2 size={15}/>{person.archived ? 'Восстановить кандидата' : 'Убрать в архив'}</Button></details>{error && <p className="ew-inline-error" role="alert">{error}</p>}</div>;
}
