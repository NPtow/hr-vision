import { useEffect, useRef } from 'react';
import { CandidateInterviewPlayer } from '../interview-review/CandidateInterviewPlayer';
import { CompletedJournal } from './CompletedJournal';
import { CompletedWorkbench } from './CompletedWorkbench';
import { CompletedOutcome } from './CompletedOutcome';
import { type CompletedPanelProps, type MeetingComment, type Verdict } from './completed-meeting-shared';
import './completed-meeting.css';

export type CompletedVariant = 'journal' | 'workbench' | 'outcome';
export const completedVariants: { id: CompletedVariant; label: string }[] = [
  { id: 'journal', label: 'Журнал' }, { id: 'workbench', label: 'Панель' }, { id: 'outcome', label: 'Итог' },
];
const sampleComments: MeetingComment[] = [
  { id: 'moment-1', author: 'Мария', seconds: 4, text: 'Хорошо разложила работу с клиентом. Я бы вернулась к этому примеру при обсуждении задач.', createdAt: '2026-10-17T10:04:00Z' },
  { id: 'moment-2', author: 'Никита', seconds: 12, text: 'Хочу уточнить, как действовала бы без скидки. Это важно для нашей модели продаж.', createdAt: '2026-10-17T10:12:00Z' },
];
const chapters = [
  { title: 'Опыт', detail: '', fragment: 'Обсуждение опыта', source: '00:00', seconds: 0 },
  { title: 'Работа с клиентом', detail: '', fragment: 'Разбор ситуации', source: '00:04', seconds: 4 },
  { title: 'Следующий шаг', detail: '', fragment: 'Уточняющие вопросы', source: '00:12', seconds: 12 },
];
const transcript = 'WEBVTT\n\n00:00.000 --> 00:04.000\nОбсуждение опыта.\n\n00:04.000 --> 00:12.000\nРазбор ситуации с клиентом.\n\n00:12.000 --> 00:20.000\nУточняющие вопросы и следующий шаг.';

export type CompletedMeetingDraft = { comments: MeetingComment[]; note: string; verdict: Verdict | null; feedback: string; currentTime: number };
export const makeCompletedDraft = (): CompletedMeetingDraft => ({ comments: sampleComments.map(comment => ({ ...comment })), note: 'На следующей встрече разобрать один сложный кейс без скидки.', verdict: null, feedback: '', currentTime: 0 });
export function CompletedMeeting({ variant, stageId, date, onNext, draft, onChange }: { variant: CompletedVariant; stageId: string; date?: string; onNext?: () => void; draft: CompletedMeetingDraft; onChange: (draft: CompletedMeetingDraft) => void }) {
  const root = useRef<HTMLDivElement>(null);
  const { comments, note, verdict, feedback, currentTime } = draft;
  const setCurrentTime = (value: number) => onChange({ ...draft, currentTime: value });
  useEffect(() => {
    const video = root.current?.querySelector('video');
    if (!video) return;
    const restore = () => { video.currentTime = currentTime; };
    if (video.readyState >= 1) restore();
    else video.addEventListener('loadedmetadata', restore, { once: true });
    return () => video.removeEventListener('loadedmetadata', restore);
  }, []);
  function seek(seconds: number) {
    const video = root.current?.querySelector('video');
    if (!video) return;
    video.pause(); video.currentTime = seconds; setCurrentTime(seconds);
    video.dispatchEvent(new Event('timeupdate', { bubbles: true }));
    const player = root.current?.querySelector<HTMLElement>('.iv-player');
    player?.scrollIntoView({ block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  }
  const panelProps: CompletedPanelProps = { comments, onAddComment: text => onChange({ ...draft, comments: [...comments, { id: crypto.randomUUID(), author: 'Никита', text, seconds: currentTime, createdAt: new Date().toISOString() }] }), note, onNoteChange: note => onChange({ ...draft, note }), verdict, onVerdictChange: verdict => onChange({ ...draft, verdict }), currentTime, onSeek: seek, feedback, onFeedbackChange: feedback => onChange({ ...draft, feedback }) };
  const dateText = date ? new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' }).format(new Date(date)) : '17 октября в 13:00';
  return <div className="cm-completed" data-variant={variant} ref={root} onTimeUpdateCapture={event => setCurrentTime((event.target as HTMLVideoElement).currentTime)} onFocusCapture={event => { if ((event.target as HTMLElement).closest('.cm-composer')) root.current?.querySelector('video')?.pause(); }}>
    <div className="cm-recording">
      <CandidateInterviewPlayer sourceId={`prototype-${stageId}`} name="Анна · тест звонка" kind="meeting" media={{ src: '/task-meeting-preview/interview.webm', localPreview: true }} chapters={chapters} comments={comments} transcriptText={transcript}
        header={<div className="cm-call-scene"><div className="cm-call-meta"><strong>Встреча</strong><span>{dateText} · МСК</span></div><div className="cm-call-participants"><div className="cm-call-person"><span className="cm-call-initial">А</span><span className="cm-call-name">Анна</span></div><div className="cm-call-person"><span className="cm-call-initial">Н</span><span className="cm-call-name">Никита · вы</span></div></div></div>}/>
    </div>
    {variant === 'journal' ? <CompletedJournal {...panelProps}/> : variant === 'workbench' ? <CompletedWorkbench {...panelProps}/> : <CompletedOutcome {...panelProps}/>}
    <footer className="cm-next"><span role="status">{verdict ? 'Фидбек сохранён' : ''}</span>{onNext && <button type="button" onClick={onNext}>Следующий шаг <span aria-hidden="true">↗</span></button>}</footer>
  </div>;
}
