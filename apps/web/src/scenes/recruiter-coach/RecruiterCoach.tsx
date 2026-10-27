import { useEffect, useRef, useState } from 'react';
import { VideoOff } from 'lucide-react';
import { RecruiterInterviewShell } from './RecruiterInterviewShell';
import { useLocalCoachMedia } from './useLocalCoachMedia';
import { useNoteHistory } from './note-history';
import { CoachNotesPanel } from './CoachNotesPanel';
import { documentFromText, documentText } from './note-document';
import type { CoachDraft, CoachPerson } from './coach-types';

const empty = (): CoachDraft => ({ current: 0, status: {}, notes: {}, general: '' });

/** Private local adapter. Production supplies Daily media and server-owned drafts. */
export function RecruiterCoach({ candidate = 'kretova' }: { candidate?: 'kretova' | 'rybkina' }) {
  const [people, setPeople] = useState<CoachPerson[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    if (!['localhost', '127.0.0.1', '[::1]'].includes(location.hostname)) {
      setError('Персональные материалы доступны в локальном окне.'); return;
    }
    fetch('/__local_prototypes__/recruiter-coach.json', { cache: 'no-store' })
      .then(r => { if (!r.ok) throw new Error('Не удалось открыть сценарий.'); return r.json(); })
      .then(data => { if (active) setPeople(Array.isArray(data) ? data : data.candidates || []); })
      .catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, []);
  const person = people.find(p => candidate === 'kretova' ? /кретов/i.test(p.name) : /рыбкин/i.test(p.name));
  return person ? <Workspace key={`${person.id}:${person.planVersion || 'original'}`} person={person}/> :
    <div className="rcc-loading">{error || 'Открываем интервью…'}{error && <button onClick={() => location.reload()}>Обновить</button>}</div>;
}

function LocalVideo({ stream, mirror = false }: { stream: MediaStream; mirror?: boolean }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const video = ref.current;
    if (!video) return;
    video.srcObject = stream;
    void video.play().catch(() => {});
    return () => { video.srcObject = null; };
  }, [stream]);
  return <video ref={ref} autoPlay playsInline muted className={mirror ? 'rcc-mirror' : undefined}/>;
}
function download(text: string, filename: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function Workspace({ person }: { person: CoachPerson }) {
  // Keep the original namespace: previous notes and current position survive.
  const storageKey = `hrv-recruiter-coach-v1:${person.id}${person.planVersion ? `:${person.planVersion}` : ''}`;
  const [draft, setDraft] = useState<CoachDraft>(() => {
    try { const saved = localStorage.getItem(storageKey); return saved ? { ...empty(), ...JSON.parse(saved) } : empty(); }
    catch { return empty(); }
  });
  const [saveError, setSaveError] = useState('');
  const [completed, setCompleted] = useState(false);
  const media = useLocalCoachMedia();
  const noteDocument = draft.noteDocument || documentFromText(draft.general);
  const noteHistory = useNoteHistory(storageKey, {
    document: noteDocument,
    questionNotes: Object.fromEntries(Object.entries(draft.notes).filter(([, value]) => value)),
  });
  const baselineRequested = useRef(false);
  function captureBaseline() {
    if (baselineRequested.current || noteHistory.versions.length) return;
    baselineRequested.current = true;
    void noteHistory.checkpoint().catch(() => { baselineRequested.current = false; });
  }
  useEffect(() => {
    try { localStorage.setItem(storageKey, JSON.stringify(draft)); setSaveError(''); }
    catch { setSaveError('Не удалось сохранить заметки в браузере. Скачайте копию.'); }
  }, [draft, storageKey]);
  const initials = person.name.split(/\s+/).map(word => word[0]).slice(0, 2).join('');
  function exportNotes() {
    const text = `${person.name}\n\n${draft.general}\n\n${person.questions.map((q, i) => `${i + 1}. ${q.question}\n${draft.status[q.id] === 'answered' ? 'Ответ получен' : draft.status[q.id] === 'skipped' ? 'Пропущен' : 'Не отмечен'}\n${draft.notes[q.id] || ''}`).join('\n\n')}\n\nИсточник сценария: ${person.promptSource}`;
    download(text, `${person.name} — интервью.txt`);
  }
  return <RecruiterInterviewShell person={person} draft={draft}
    onChange={patch => {
      if (patch.notes || patch.general !== undefined || patch.noteDocument) captureBaseline();
      setDraft(previous => ({ ...previous, ...patch }));
    }}
    mediaSlot={media.screenStream ? <LocalVideo stream={media.screenStream}/> : <div className="rcc-video-placeholder"><span className="rcc-avatar">{initials}</span><span>Видео кандидата</span></div>}
    selfPreview={media.cameraStream ? <LocalVideo stream={media.cameraStream} mirror/> : <div className="rcc-self-placeholder"><VideoOff size={23}/></div>}
    mediaControls={media} phase={completed ? 'completed' : 'preview'} completedLabel="Просмотр завершён"
    onFinish={() => { media.stop(); setCompleted(true); }} onResume={() => setCompleted(false)}
    notice={media.error} saveError={saveError} onExport={exportNotes}
    notesContent={go => <CoachNotesPanel document={noteDocument}
      onChange={(document, general) => {
        // Capture the prior document (including the empty baseline) before the first keystroke.
        captureBaseline();
        setDraft(previous => ({ ...previous, noteDocument: document, general }));
      }}
      questionNotes={person.questions.map((q, index) => ({ id: q.id, index, title: q.topic, text: draft.notes[q.id] || '' }))}
      onSelectQuestion={go} versions={noteHistory.versions} historyLoading={noteHistory.loading}
      historyError={noteHistory.error} checkpoint={noteHistory.checkpoint} writable
      onRestore={snapshot => setDraft(previous => ({
        ...previous, noteDocument: snapshot.document, general: documentText(snapshot.document),
        notes: snapshot.questionNotes,
      }))}
    />}
  />;
}
