import { useEffect, useRef, useState } from 'react';
import type { JSONContent } from '@tiptap/core';
import { ArrowLeft, ChevronRight, History, LoaderCircle, Sparkles } from 'lucide-react';
import { TiptapNoteEditor, validateNoteDocument } from './TiptapNoteEditor';
import { documentText } from './note-document';
import type { NoteSnapshot, NoteVersion } from './note-history';
import type { CoachTranscriptTurn } from './coach-types';
import './coach-notes.css';

export type EnhanceNotes = (input: {
  manualDocument: JSONContent;
  plainText: string;
  questionNotes: Record<string, string>;
  transcript: CoachTranscriptTurn[];
  baseRevision: string;
}) => Promise<{ document: JSONContent; baseRevision: string }>;

type Props = {
  document: JSONContent;
  questionNotes: { id: string; index: number; title: string; text: string }[];
  onChange: (document: JSONContent, text: string) => void;
  onSelectQuestion: (index: number) => void;
  versions: NoteVersion[];
  historyLoading: boolean;
  historyError: string;
  checkpoint: (snapshot?: NoteSnapshot, reason?: NoteVersion['reason']) => Promise<void>;
  onRestore: (snapshot: NoteSnapshot) => void;
  restoreVersion?: (id: string) => Promise<void>;
  hasMoreHistory?: boolean; onMoreHistory?: () => void;
  writable: boolean;
  transcript?: CoachTranscriptTurn[];
  /** Only provide when a recruiter-scoped model endpoint actually exists. */
  onEnhance?: EnhanceNotes;
};

export function CoachNotesPanel({
  document, questionNotes, onChange, onSelectQuestion, versions, historyLoading,
  historyError, checkpoint, onRestore, restoreVersion, hasMoreHistory, onMoreHistory, writable, transcript, onEnhance,
}: Props) {
  const [view, setView] = useState<'edit' | 'history' | 'version' | 'enhanced'>('edit');
  const [selected, setSelected] = useState<NoteVersion>();
  const [enhanced, setEnhanced] = useState<{ document: JSONContent; baseRevision: string }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const requestSequence = useRef(0);
  const mounted = useRef(true);
  const snapshot: NoteSnapshot = {
    document,
    questionNotes: Object.fromEntries(questionNotes.filter(q => q.text).map(q => [q.id, q.text])),
  };
  const fingerprint = JSON.stringify(snapshot);
  const latest = useRef({ snapshot, fingerprint, writable });
  latest.current = { snapshot, fingerprint, writable };
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; requestSequence.current++; };
  }, []);

  async function showHistory() {
    setError('');
    setView('history');
    try { await checkpoint(); } catch { /* The history hook supplies its error. */ }
  }
  async function restore(version: NoteVersion) {
    if (!writable) return;
    const base = latest.current.fingerprint;
    setBusy(true); setError('');
    try {
      if (restoreVersion) { await restoreVersion(version.id); if (mounted.current) setView('edit'); return; }
      await checkpoint(latest.current.snapshot, 'before_restore');
      if (!mounted.current || !latest.current.writable) return;
      if (latest.current.fingerprint !== base) throw new Error('Notes changed');
      // Persist the new version first: an IndexedDB failure must not replace current text.
      await checkpoint(version.snapshot, 'restored');
      if (!mounted.current || !latest.current.writable) return;
      if (latest.current.fingerprint !== base) throw new Error('Notes changed');
      onRestore(structuredClone(version.snapshot));
      setView('edit');
    } catch { if (mounted.current) setError('Не удалось восстановить версию. Текущие заметки сохранены.'); }
    finally { if (mounted.current) setBusy(false); }
  }
  async function enhance() {
    if (!onEnhance || !transcript?.length || !writable) return;
    const sequence = ++requestSequence.current;
    const base = structuredClone(latest.current);
    setBusy(true); setError('');
    try {
      await checkpoint(base.snapshot, 'before_ai');
      if (!mounted.current || sequence !== requestSequence.current || !latest.current.writable) return;
      if (latest.current.fingerprint !== base.fingerprint) throw new Error('Notes changed');
      const result = await onEnhance({
        manualDocument: base.snapshot.document,
        plainText: documentText(base.snapshot.document),
        questionNotes: base.snapshot.questionNotes,
        transcript: structuredClone(transcript),
        baseRevision: base.fingerprint,
      });
      if (!mounted.current || sequence !== requestSequence.current) return;
      if (result.baseRevision !== base.fingerprint || latest.current.fingerprint !== base.fingerprint) {
        setError('Заметки изменились. Дополните их ещё раз, чтобы использовать текущую версию.');
        return;
      }
      validateNoteDocument(result.document);
      setEnhanced(result); setView('enhanced');
    } catch { if (mounted.current && sequence === requestSequence.current) setError('Не удалось дополнить заметки. Ваши записи сохранены.'); }
    finally { if (mounted.current && sequence === requestSequence.current) setBusy(false); }
  }
  async function acceptEnhanced() {
    if (!enhanced || !writable || enhanced.baseRevision !== latest.current.fingerprint) {
      setError('Исходные заметки изменились. Вернитесь к ним и повторите дополнение.'); return;
    }
    setBusy(true); setError('');
    const base = latest.current.fingerprint;
    try {
      await checkpoint(latest.current.snapshot, 'before_ai');
      if (!mounted.current || !latest.current.writable || latest.current.fingerprint !== base) return;
      await checkpoint({ document: enhanced.document, questionNotes: latest.current.snapshot.questionNotes }, 'ai');
      if (!mounted.current || !latest.current.writable || latest.current.fingerprint !== base) return;
      onChange(enhanced.document, documentText(enhanced.document));
      setEnhanced(undefined); setView('edit');
    } catch { if (mounted.current) setError('Не удалось сохранить дополнение. Ваши записи не изменены.'); }
    finally { if (mounted.current) setBusy(false); }
  }
  const dateLabel = (date: string) => new Date(date).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const reasonLabel = (reason: NoteVersion['reason']) => ({
    manual: 'Ваши записи', before_restore: 'Перед восстановлением', restored: 'Восстановленная версия',
    before_ai: 'До дополнения', ai: 'Дополнено',
  }[reason]);
  return <div className="rcc-note-workspace">
    <header className="rcc-note-heading">
      {view !== 'edit' && <button className="rcc-note-icon" aria-label={view === 'version' ? 'К истории заметок' : 'К заметкам'} onClick={() => { setView(view === 'version' ? 'history' : 'edit'); setError(''); }}><ArrowLeft size={18}/></button>}
      <h2>{view === 'edit' ? 'Заметки' : view === 'history' ? 'История' : view === 'enhanced' ? 'Дополнено' : 'Версия заметок'}</h2>
      {view === 'edit' && <button className="rcc-note-icon" aria-label="История заметок" title="История заметок" onClick={() => void showHistory()}><History size={19}/></button>}
    </header>
    <div className="rcc-note-body">
      {view === 'edit' && <>
        <TiptapNoteEditor value={document} onChange={onChange} editable={writable && !busy} label="Общие заметки" placeholder="Начните писать или нажмите /"/>
        {questionNotes.some(q => q.text) && <div className="rcc-saved-notes">{questionNotes.filter(q => q.text).map(q => <div key={q.id}>
          <button onClick={() => onSelectQuestion(q.index)}>{q.title}<ChevronRight size={15}/></button><p>{q.text}</p>
        </div>)}</div>}
      </>}
      {view === 'history' && <div className="rcc-note-history">
        {historyLoading && <p className="rcc-quiet">Открываем историю…</p>}
        {!historyLoading && !versions.length && <p className="rcc-quiet">Здесь будут сохранённые версии заметок.</p>}
        {versions.map((version, index) => <button key={version.id} onClick={() => { setSelected(version); setView('version'); }}>
          <span className="rcc-version-dot"/><span><strong>{dateLabel(version.createdAt)}</strong><small>{reasonLabel(version.reason)}{index === 0 ? ' · последняя' : ''}</small>
            <p>{documentText(version.snapshot.document).trim().slice(0, 100) || Object.values(version.snapshot.questionNotes).join(' ').slice(0, 100) || 'Пустая заметка'}</p></span><ChevronRight size={16}/>
        </button>)}
        {hasMoreHistory && <button disabled={historyLoading} onClick={onMoreHistory}>Показать более ранние</button>}
      </div>}
      {view === 'version' && selected && <>
        <p className="rcc-note-version-date">{dateLabel(selected.createdAt)} · {reasonLabel(selected.reason)}</p>
        <TiptapNoteEditor value={selected.snapshot.document} onChange={() => {}} editable={false} label="Сохранённая версия" placeholder="Пустая заметка"/>
        {!!Object.values(selected.snapshot.questionNotes).filter(Boolean).length && <div className="rcc-saved-notes">{Object.entries(selected.snapshot.questionNotes).filter(([, value]) => value).map(([id, text]) => <div key={id}><span className="rcc-note-version-date">{questionNotes.find(q => q.id === id)?.title || 'К вопросу'}</span><p>{text}</p></div>)}</div>}
      </>}
      {view === 'enhanced' && enhanced && <TiptapNoteEditor value={enhanced.document} onChange={() => {}} editable={false} label="Предлагаемое дополнение"/>}
    </div>
    {(error || historyError) && <p className="rcc-note-error" role="alert">{error || historyError}</p>}
    {view === 'version' && selected && <footer className="rcc-note-actions"><button className="rcc-next" disabled={busy || !writable || JSON.stringify(selected.snapshot) === fingerprint} onClick={() => void restore(selected)}>{busy && <LoaderCircle className="rcc-spinning" size={15}/>}Восстановить версию</button></footer>}
    {view === 'edit' && onEnhance && <footer className="rcc-note-actions"><button className="rcc-next" disabled={busy || !writable || !transcript?.length} title={!transcript?.length ? 'Для дополнения нужна расшифровка встречи' : undefined} onClick={() => void enhance()}>{busy ? <LoaderCircle className="rcc-spinning" size={15}/> : <Sparkles size={15}/>}Дополнить по встрече</button></footer>}
    {view === 'enhanced' && <footer className="rcc-note-actions"><button disabled={busy} onClick={() => { setEnhanced(undefined); setView('edit'); }}>Оставить мои записи</button><button className="rcc-next" disabled={busy || !writable} onClick={() => void acceptEnhanced()}>Применить</button></footer>}
  </div>;
}
