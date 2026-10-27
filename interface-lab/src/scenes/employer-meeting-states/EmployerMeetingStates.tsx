import { useEffect, useId, useRef, useState } from 'react';
import { Dialog, DropdownMenu } from 'radix-ui';
import { ArrowUpRight, Check, ChevronDown, Clock3, MessageSquare, Mic, MicOff, MoreHorizontal, PhoneOff, Plus, RotateCcw, Video, VideoOff, X } from 'lucide-react';
import { CandidateStageCard } from '../employer-workspace/CandidateStageCard';
import { EmployerShell } from '../employer-workspace/EmployerShell';
import { CandidateInterviewPlayer } from '../interview-review/CandidateInterviewPlayer';
import type { RecordingChapter } from '../interview-review/InterviewReview';
import { asStage, dateLabel, endTime, interview, makeMeeting, seedScenario, staff, timeLabel, type DemoMeeting, type Scenario } from './meeting-demo';
import '../employer-workspace/employer.css';
import '../employer-workspace/candidate-stage-workspace.css';
import './employer-meeting-states.css';

type Fixture = {
  name: string; video: string;
  transcript: { start: number; end: number; speaker: string; text: string }[];
  chapters: { start: number; title: string; description: string }[];
};
type Comment = { id: string; seconds: number; text: string; createdAt: string };
type Editor = { type: 'new' | 'edit'; meeting: DemoMeeting };
const scenarios: { id: Scenario; label: string }[] = [
  { id: 'pending', label: 'Ожидает подтверждения' }, { id: 'confirmed', label: 'Подтверждена' },
  { id: 'live', label: 'Идёт встреча' }, { id: 'completed', label: 'Проведена' }, { id: 'repeat', label: 'Повторная встреча' },
];
const vttTime = (seconds: number) => `${String(Math.floor(seconds / 3600)).padStart(2, '0')}:${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}.000`;

/** Local-only design review. No authenticated APIs, storage, invitations or device permissions. */
export function EmployerMeetingStates({ initialScenario = 'confirmed', runId = 0, compact = false }: {
  initialScenario?: Scenario; runId?: number; compact?: boolean;
}) {
  const [scenario, setScenario] = useState(initialScenario);
  const [meetings, setMeetings] = useState(() => seedScenario(initialScenario));
  const [selected, setSelected] = useState(() => seedScenario(initialScenario).at(-1)!.id);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [fixture, setFixture] = useState<Fixture | null>(null);
  const [comments, setComments] = useState<Record<string, Comment[]>>({});
  const [viewer, setViewer] = useState('nikita');
  const [resetEpoch, setResetEpoch] = useState(0);
  const [joined, setJoined] = useState(initialScenario === 'live');
  const sequence = useRef(seedScenario(initialScenario).length + 1);
  const current = meetings.find(meeting => meeting.id === selected);
  const viewerName = staff.find(person => person.id === viewer)!.name;
  function reset(next: Scenario) {
    setScenario(next); setMeetings(seedScenario(next)); setSelected(seedScenario(next).at(-1)!.id);
    setEditor(null); setComments({}); setJoined(next === 'live'); sequence.current = seedScenario(next).length + 1;
    setResetEpoch(value => value + 1);
  }
  useEffect(() => { reset(initialScenario); }, [initialScenario, runId]);
  useEffect(() => {
    // The private fixture is deliberately absent from production and source control.
    if (!['localhost', '127.0.0.1', '[::1]'].includes(location.hostname)) return;
    let active = true;
    fetch('/__local_prototypes__/meeting-interview.json').then(result => result.ok ? result.json() : null)
      .then(value => { if (active && value?.video && Array.isArray(value.transcript) && Array.isArray(value.chapters)) setFixture(value); })
      .catch(() => {});
    return () => { active = false; };
  }, []);
  function change(id: string, values: Partial<DemoMeeting>) {
    setMeetings(items => items.map(meeting => meeting.id === id ? { ...meeting, ...values } : meeting));
  }
  function newMeeting() {
    const pending = meetings.find(meeting => ['pending', 'confirmed', 'live'].includes(meeting.status));
    if (pending) { setSelected(pending.id); return; }
    setEditor({ type: 'new', meeting: { ...makeMeeting(`meeting-${sequence.current}`, 'pending'), organizer: viewer, title: 'Следующая встреча', start: '2026-10-23T14:30:00+03:00' } });
  }
  const transcript = fixture ? `WEBVTT\n\n${fixture.transcript.map(row => `${vttTime(row.start)} --> ${vttTime(Math.max(row.end, row.start + 1))}\n<v ${row.speaker}>${row.text}</v>`).join('\n\n')}` : '';
  const chapters: RecordingChapter[] = fixture?.chapters.map(chapter => ({ title: chapter.title, detail: chapter.description, fragment: '', source: '', seconds: chapter.start })) || [];
  function recording(id: string) {
    return <CandidateInterviewPlayer key={`recording-${id}`} sourceId={id} kind={id === interview.id ? 'screening' : 'meeting'} name="Дина Чаплыгина"
      media={fixture ? { src: fixture.video, localPreview: true } : undefined} chapters={chapters} transcriptText={transcript}
      comments={comments[id] || []} canComment={!!fixture} emptyMessage="Видео для локального макета не подключено"
      renderTimeComment={(seconds, close) => <LocalComment onCancel={close} onSave={text => {
        setComments(value => ({ ...value, [id]: [...(value[id] || []), { id: crypto.randomUUID(), seconds, text, createdAt: new Date().toISOString() }] })); close();
      }}/>}/>;
  }
  return <div className={`ems-scene ew-root${compact ? ' ems-compact' : ''}`}>
    <div className="ems-lab" aria-label="Управление макетом">
      <details><summary>Макет · локальные действия</summary><p>Встречи, общий отзыв и участники — пример. Сообщения не отправляются, камера не включается. Для проверки плеера используется исходное интервью Дины; это не запись показанной встречи.</p></details>
      <div className="ems-lab-controls"><label>Сценарий<select value={scenario} onChange={event => reset(event.target.value as Scenario)}>{scenarios.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
        <label>Аккаунт<select value={viewer} onChange={event => setViewer(event.target.value)}>{staff.map(person => <option key={person.id} value={person.id}>{person.name}</option>)}</select></label>
        {current?.status === 'pending' && <button type="button" onClick={() => change(current.id, { status: 'confirmed' })}>Подтвердить за кандидата</button>}
        {current?.status === 'completed' && !current.materialsReady && <button type="button" onClick={() => change(current.id, { materialsReady: true })}>Материалы готовы</button>}
        <button type="button" className="ems-reset" aria-label="Сбросить макет" onClick={() => reset(scenario)}><RotateCcw size={15}/></button>
      </div>
    </div>
    <EmployerShell page="shortlist" scope="sfera" candidateContext="vacancy" vacancy="Менеджер по продажам" name={viewerName} hasOffers={false}
      navigationBusy onNavigate={() => {}} onAccount={() => {}} candidateNavigation={<span className="ems-one-person">ДЧ</span>}>
      <main className="esc-workspace">
        <CandidateStageCard key={resetEpoch} person={{ id: 'dina', name: 'Дина Чаплыгина' }} stages={[interview, ...meetings.map(asStage)]} workflowRevision={1}
          selectedStageId={selected} onSelectStage={id => { setSelected(id); setJoined(false); }}
          renderInterview={() => recording(interview.id)}
          renderMeeting={stage => {
            const meeting = meetings.find(item => item.id === stage.id)!;
            return <MeetingContent key={`contents-${meeting.id}`} meeting={meeting} joined={joined}
              onEdit={() => setEditor({ type: 'edit', meeting })} onNew={newMeeting}
              onCancel={() => change(meeting.id, { status: 'cancelled', changes: [...meeting.changes, `${viewerName} отменил встречу`] })}
              onJoin={() => { setJoined(true); change(meeting.id, { status: 'live' }); }}
              onLeave={() => setJoined(false)} onFinish={() => { setJoined(false); change(meeting.id, { status: 'completed', materialsReady: false }); }}
              onFeedback={feedback => change(meeting.id, { feedback, feedbackAuthor: viewerName })}
              recording={recording(meeting.id)}/>;
          }}/>
      </main>
    </EmployerShell>
    <Dialog.Root open={!!editor} onOpenChange={open => { if (!open) setEditor(null); }}>
      <Dialog.Portal><Dialog.Overlay className="ems-dialog-overlay"/><Dialog.Content className="ems-dialog ew-root" aria-describedby={undefined}>
        {editor && <MeetingEditor key={`${editor.type}-${editor.meeting.id}`} editor={editor} onCancel={() => setEditor(null)} onSave={value => {
          if (editor.type === 'new') { setMeetings(items => [...items, value]); sequence.current += 1; }
          else change(value.id, { ...value, changes: value.start !== editor.meeting.start ? [...value.changes, `${dateLabel(editor.meeting.start)}, ${timeLabel(editor.meeting.start)} → ${dateLabel(value.start)}, ${timeLabel(value.start)} · ${viewerName}`] : value.changes });
          setSelected(value.id); setJoined(false); setEditor(null);
        }}/>}<Dialog.Close className="ems-dialog-close" aria-label="Закрыть"><X size={20}/></Dialog.Close>
      </Dialog.Content></Dialog.Portal>
    </Dialog.Root>
  </div>;
}

function MeetingContent({ meeting, joined, onEdit, onNew, onCancel, onJoin, onLeave, onFinish, onFeedback, recording }: {
  meeting: DemoMeeting; joined: boolean; onEdit: () => void; onNew: () => void; onCancel: () => void;
  onJoin: () => void; onLeave: () => void; onFinish: () => void; onFeedback: (text: string) => void; recording: React.ReactNode;
}) {
  const [editingNote, setEditingNote] = useState(false);
  const [note, setNote] = useState(meeting.feedback);
  const [mic, setMic] = useState(true);
  const [camera, setCamera] = useState(true);
  const [cancelPrompt, setCancelPrompt] = useState(false);
  const completed = meeting.status === 'completed';
  const cancelled = meeting.status === 'cancelled';
  const names = meeting.participants.map(id => staff.find(person => person.id === id)!.name);
  return <section className="ems-meeting" aria-label={meeting.title}>
    <header className="ems-meeting-header"><div><h3>{meeting.title}</h3><p>{dateLabel(meeting.start)} · {timeLabel(meeting.start)}—{endTime(meeting.start)} <span>МСК</span></p></div>
      <DropdownMenu.Root><DropdownMenu.Trigger className="ems-icon" aria-label="Действия со встречей"><MoreHorizontal size={22}/></DropdownMenu.Trigger><DropdownMenu.Portal><DropdownMenu.Content className="ems-menu" sideOffset={6} align="end">
        {!completed && !cancelled && <DropdownMenu.Item onSelect={onEdit}>Дата и участники</DropdownMenu.Item>}
        {completed && <DropdownMenu.Item onSelect={() => { setNote(meeting.feedback); setEditingNote(true); }}>Изменить общий отзыв</DropdownMenu.Item>}
        {!completed && !cancelled && <DropdownMenu.Item className="ems-danger" onSelect={() => setCancelPrompt(true)}>Отменить встречу</DropdownMenu.Item>}
        {(completed || cancelled) && <DropdownMenu.Item onSelect={onNew}>Следующая встреча</DropdownMenu.Item>}
      </DropdownMenu.Content></DropdownMenu.Portal></DropdownMenu.Root>
    </header>
    <div className="ems-meeting-meta"><details className="ems-participants"><summary><span className="ems-avatar-stack">{meeting.participants.map(id => { const person = staff.find(item => item.id === id)!; return <span key={id} style={{ background: person.color }}>{person.initials}</span>; })}</span><span>{names.join(', ')}</span><ChevronDown size={15}/></summary>
      <div className="ems-participant-list"><p>Организатор — {staff.find(person => person.id === meeting.organizer)!.name}</p>{meeting.participants.map(id => { const person = staff.find(item => item.id === id)!; return <div key={id}><span className="ems-person-dot" style={{ background: person.color }}>{person.initials}</span><span><strong>{person.name}</strong><small>{person.role}</small></span><Check size={15}/></div>; })}<div><span className="ems-person-dot">ДЧ</span><span><strong>Дина Чаплыгина</strong><small>{meeting.status === 'pending' ? 'Ещё не подтвердила' : 'Кандидат'}</small></span>{meeting.status !== 'pending' && <Check size={15}/>}</div></div>
    </details><span className={`ems-status ems-status-${meeting.status}`}><i/>{({ pending: 'Ждём Дину', confirmed: 'Подтверждена', live: 'Идёт встреча', completed: 'Проведена', cancelled: 'Отменена' })[meeting.status]}</span></div>
    {cancelPrompt && <div className="ems-cancel"><p>Отменить встречу {dateLabel(meeting.start)}?</p><button type="button" className="ems-text-button" onClick={() => setCancelPrompt(false)}>Оставить</button><button type="button" className="ems-text-button ems-danger" onClick={() => { setCancelPrompt(false); onCancel(); }}>Отменить встречу</button></div>}
    {meeting.changes.length > 0 && <details className="ems-changes"><summary>Изменения<ChevronDown size={14}/></summary>{meeting.changes.map((change, i) => <p key={i}>{change}</p>)}</details>}
    {!completed && !cancelled && !joined && <div className="ems-upcoming"><p>{meeting.agenda}</p>{meeting.status !== 'pending' && <button className="ems-primary" type="button" onClick={onJoin}><Video size={18}/>Войти во встречу<ArrowUpRight size={17}/></button>}</div>}
    {meeting.status === 'live' && joined && <div className="ems-call"><div className="ems-call-grid">{[{ name: 'Дина Чаплыгина', initials: 'ДЧ', color: '#6c687e' }, ...meeting.participants.map(id => staff.find(person => person.id === id)!)].map(person => <div className="ems-call-person" key={person.name}><span style={{ background: person.color }}>{person.initials}</span><p>{person.name}</p></div>)}</div><div className="ems-call-footer"><span><i/>06:12</span><div><button className="ems-call-control" type="button" aria-label={mic ? 'Выключить микрофон' : 'Включить микрофон'} aria-pressed={!mic} onClick={() => setMic(value => !value)}>{mic ? <Mic size={18}/> : <MicOff size={18}/>}</button><button className="ems-call-control" type="button" aria-label={camera ? 'Выключить камеру' : 'Включить камеру'} aria-pressed={!camera} onClick={() => setCamera(value => !value)}>{camera ? <Video size={19}/> : <VideoOff size={19}/>}</button><button className="ems-call-control ems-call-leave" type="button" aria-label="Выйти из встречи" onClick={onLeave}><PhoneOff size={19}/></button></div><button className="ems-call-finish" type="button" onClick={onFinish}>Завершить для всех</button></div></div>}
    {completed && <><div className="ems-result"><div className="ems-result-heading"><h4>Общий отзыв</h4>{meeting.feedback && !editingNote && <button type="button" className="ems-text-button" onClick={() => { setNote(meeting.feedback); setEditingNote(true); }}>Изменить</button>}</div>
      {editingNote ? <form onSubmit={event => { event.preventDefault(); onFeedback(note.trim()); setEditingNote(false); }}><textarea autoFocus aria-label="Общий отзыв о встрече" value={note} onChange={event => setNote(event.target.value)} rows={3}/><div className="ems-form-actions"><button className="ems-primary" type="submit" disabled={!note.trim()}>Сохранить</button><button className="ems-text-button" type="button" onClick={() => setEditingNote(false)}>Отмена</button></div></form>
        : meeting.feedback ? <><p>{meeting.feedback}</p><small>{meeting.feedbackAuthor}</small></> : <button type="button" className="ems-note-empty" onClick={() => setEditingNote(true)}><MessageSquare size={17}/>Как прошла встреча?</button>}
    </div>{meeting.materialsReady ? <div className="ems-recording">{recording}</div> : <div className="ems-processing"><Clock3 size={18}/><span>Запись и расшифровка готовятся</span></div>}
      <footer className="ems-next"><button className="ems-primary" type="button" onClick={onNew}><Plus size={17}/>Следующая встреча</button></footer></>}
    {cancelled && <footer className="ems-next"><button className="ems-primary" type="button" onClick={onNew}><Plus size={17}/>Назначить встречу</button></footer>}
  </section>;
}

function MeetingEditor({ editor, onSave, onCancel }: { editor: Editor; onSave: (meeting: DemoMeeting) => void; onCancel: () => void }) {
  const id = useId();
  const [title, setTitle] = useState(editor.meeting.title);
  const [day, setDay] = useState(editor.meeting.start.slice(0, 10));
  const [time, setTime] = useState(timeLabel(editor.meeting.start));
  const [participants, setParticipants] = useState(editor.meeting.participants);
  const [agenda, setAgenda] = useState(editor.meeting.agenda);
  return <form className="ems-editor" onSubmit={event => { event.preventDefault(); onSave({ ...editor.meeting, title: title.trim(), start: `${day}T${time}:00+03:00`, participants, agenda: agenda.trim(), status: 'pending' }); }}>
    <Dialog.Title>{editor.type === 'new' ? 'Следующая встреча' : 'Дата и участники'}</Dialog.Title>
    <label htmlFor={`${id}-title`}>Встреча<input id={`${id}-title`} value={title} onChange={event => setTitle(event.target.value)} required/></label>
    <div className="ems-editor-time"><label htmlFor={`${id}-date`}>Дата<input id={`${id}-date`} type="date" value={day} onChange={event => setDay(event.target.value)} required/></label><label htmlFor={`${id}-time`}>Время · МСК<select id={`${id}-time`} value={time} onChange={event => setTime(event.target.value)}>{['10:00', '11:00', '12:00', '14:30', '16:00'].map(value => <option key={value}>{value}</option>)}</select></label><span>45 минут</span></div>
    <fieldset><legend>Коллеги</legend>{staff.map(person => <label className="ems-staff-option" key={person.id}><input type="checkbox" checked={participants.includes(person.id)} onChange={event => setParticipants(values => event.target.checked ? [...values, person.id] : values.filter(value => value !== person.id))}/><span className="ems-person-dot" style={{ background: person.color }}>{person.initials}</span><span>{person.name}</span></label>)}</fieldset>
    <details className="ems-editor-agenda"><summary>Что обсудим<ChevronDown size={15}/></summary><textarea aria-label="Темы встречи" value={agenda} onChange={event => setAgenda(event.target.value)} rows={2}/></details>
    <div className="ems-form-actions"><button className="ems-primary" type="submit" disabled={!participants.length || !title.trim()}>{editor.type === 'new' ? 'Назначить встречу' : 'Сохранить'}</button><button className="ems-text-button" type="button" onClick={onCancel}>Отмена</button></div>
  </form>;
}

function LocalComment({ onSave, onCancel }: { onSave: (text: string) => void; onCancel: () => void }) {
  const [value, setValue] = useState('');
  return <form className="ems-comment" onSubmit={event => { event.preventDefault(); if (value.trim()) onSave(value.trim()); }}><textarea autoFocus aria-label="Комментарий к моменту" placeholder="Комментарий" value={value} onChange={event => setValue(event.target.value)} rows={3}/><div><button type="button" onClick={onCancel}>Отмена</button><button type="submit" disabled={!value.trim()}>Сохранить</button></div></form>;
}
