import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { AnimatePresence, LayoutGroup, MotionConfig, motion, useReducedMotion } from 'motion/react';
import { Dialog, Tooltip } from 'radix-ui';
import { AlignLeft, ArrowLeft, ArrowUp, ArrowUpRight, Check, ChevronDown, ClipboardList, FileText, Mail, PanelRightClose, PanelRightOpen, Play, RotateCcw, Sparkles, Users, X } from 'lucide-react';
import { CandidateInterviewPlayer } from '../interview-review/CandidateInterviewPlayer';
import { EmployerResumeDialog } from '../employer-workspace/EmployerResumeDialog';
import { AssessmentProfiles } from './profiles/ProfileVariants';
import { RadarCandidatePreview } from './RadarCandidatePreview';
import { demoAssessment, demoTurns } from './assessment-fixture';
import type { AssessmentCriterion, AssessmentEvidence, AssessmentFeedback } from './assessment-view';
import '../employer-workspace/employer-candidate-profile.css';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import './assessment-interpretation.css';

export type AssessmentScene = 'inspector' | 'decision' | 'lens' | 'brief' | 'evidence' | 'plan';
type Pane = 'transcript' | 'analysis' | 'chat';
const scenes: { id: AssessmentScene; title: string; group: 'card' | 'profile' }[] = [
  { id: 'inspector', title: 'Карта навыков', group: 'card' },
  { id: 'decision', title: 'Вопрос к решению', group: 'card' },
  { id: 'lens', title: 'Рядом с репликой', group: 'card' },
  { id: 'brief', title: 'Краткое досье', group: 'profile' },
  { id: 'evidence', title: 'Качества и основания', group: 'profile' },
  { id: 'plan', title: 'Следующая встреча', group: 'profile' },
];
const clock = (s: number) => `${Math.floor(s / 60).toString().padStart(2, '0')}:${Math.floor(s % 60).toString().padStart(2, '0')}`;
const transition = { type: 'spring' as const, bounce: 0, duration: .3 };
const named = (id: string) => demoAssessment.criteria.find(row => row.id === id)!;
const chapters = [{ title: 'Учебный кейс', seconds: 0, detail: '', fragment: '', source: '00:00' }, { title: 'Разговор об опыте', seconds: 13, detail: '', fragment: '', source: '00:13' }];
const transcript = () => demoTurns.filter(row => row.seconds !== null).map((row, index, rows) => `[${clock(row.seconds!)}-${clock(rows[index + 1]?.seconds ?? 20)}] ${row.speaker === 'Рекрутер' ? 'Р' : 'К'}: ${row.text}`).join('\n');

export function AssessmentInterpretationPreview({ initialScene = 'inspector' }: { initialScene?: AssessmentScene }) {
  const [scene, setScene] = useState<AssessmentScene>(initialScene);
  const [runId, setRunId] = useState(0);
  const [profileSource, setProfileSource] = useState<AssessmentEvidence | null>(null);
  const sceneInfo = scenes.find(row => row.id === scene)!;
  const card = sceneInfo.group === 'card';
  const profileVideo = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!profileSource || profileSource.seconds === null) return;
    const video = profileVideo.current?.querySelector('video');
    if (!video) return;
    const seek = () => { video.currentTime = profileSource.seconds!; };
    if (video.readyState >= 1) seek();
    else { video.addEventListener('loadedmetadata', seek, { once: true }); return () => video.removeEventListener('loadedmetadata', seek); }
  }, [profileSource]);
  return <MotionConfig reducedMotion="user"><Tooltip.Provider delayDuration={650}>
    <div className="aip-preview">
      <div className="aip-labbar"><div className="aip-lab-group" aria-label="Тип макета"><button aria-pressed={card} onClick={() => setScene('inspector')}>Карточка</button><button aria-pressed={!card} onClick={() => setScene('brief')}>Профиль</button></div><nav aria-label="Варианты макета">{scenes.filter(row => row.group === sceneInfo.group).map(row => <button key={row.id} aria-current={scene === row.id ? 'page' : undefined} onClick={() => setScene(row.id)}>{row.title}</button>)}</nav><span>Демо · данные и действия локальные</span><button aria-label="Сбросить макет оценки" className="aip-lab-reset" onClick={() => setRunId(value => value + 1)}><RotateCcw size={14}/></button></div>
      <header className="aip-brand"><img src={logo} alt="HR Vision"/><span>Никита<ChevronDown size={14}/></span></header>
      <div className="aip-cabinet"><nav className="aip-rail" aria-label="Разделы кабинета"><span><ClipboardList size={21}/></span><span className="aip-rail-selected"><Users size={21}/></span><span><FileText size={20}/></span></nav><div className="aip-content"><div className="aip-vacancy"><h1>{demoAssessment.vacancyTitle}</h1>{card ? <button onClick={() => setScene('brief')}>Профиль кандидата<ArrowUpRight size={14}/></button> : <button onClick={() => setScene('inspector')}><ArrowLeft size={14}/>К интервью</button>}</div>
      {card ? scene === 'inspector' ? <RadarCandidatePreview key={`radar-${runId}`} onProfile={() => setScene('brief')}/> : <CandidateCard key={`${scene}-${runId}`} variant={scene as 'inspector' | 'decision' | 'lens'} onProfile={() => setScene('brief')}/> : <div className="aip-profile-scroll"><AssessmentProfiles data={demoAssessment} variant={scene as 'brief' | 'evidence' | 'plan'} runId={runId} onSource={setProfileSource}/></div>}
      </div></div>
    </div>
    <Dialog.Root open={!!profileSource} onOpenChange={open => { if (!open) setProfileSource(null); }}><Dialog.Portal><Dialog.Overlay className="aip-scrim"/><Dialog.Content className="aip-dialog aip-video-dialog"><header><Dialog.Title>Источник вывода</Dialog.Title><Dialog.Close aria-label="Закрыть источник" className="aip-icon"><X size={20}/></Dialog.Close></header><Dialog.Description>{profileSource?.seconds === null ? 'Реплика из интервью. Видео для этого источника недоступно.' : `Фрагмент интервью · ${profileSource?.time}`}</Dialog.Description><div ref={profileVideo} className="aip-source-player" onLoadedMetadata={event => { if (event.target instanceof HTMLVideoElement && profileSource?.seconds !== null && profileSource?.seconds !== undefined) event.target.currentTime = profileSource.seconds; }}>{profileSource && profileSource.seconds !== null && <CandidateInterviewPlayer name={demoAssessment.candidate.name} sourceId="assessment-profile-source-preview" kind="screening" initialEvidenceIndex={0} media={{ src: '/assessment-preview/interview.webm', localPreview: true }} chapters={[{ title: 'Источник вывода', seconds: profileSource.seconds, detail: '', fragment: '', source: profileSource.time }]} transcriptText={transcript()}/>}</div><blockquote>{profileSource?.text}</blockquote><small>Учебная запись: переход показывает механику привязки реплики к видео.</small></Dialog.Content></Dialog.Portal></Dialog.Root>
  </Tooltip.Provider></MotionConfig>;
}

function CandidateCard({ variant, onProfile }: { variant: 'inspector' | 'decision' | 'lens'; onProfile: () => void }) {
  const reduced = useReducedMotion();
  const container = useRef<HTMLDivElement>(null);
  const media = useRef<HTMLDivElement>(null);
  const transcriptList = useRef<HTMLDivElement>(null);
  const [pane, setPane] = useState<Pane>(variant === 'inspector' ? 'analysis' : 'transcript');
  const [selected, setSelected] = useState<string | null>(variant === 'inspector' ? 'A10' : null);
  const [sideHidden, setSideHidden] = useState(false);
  const [full, setFull] = useState(false);
  const [current, setCurrent] = useState(0);
  const [source, setSource] = useState<string | null>(null);
  const [agenda, setAgenda] = useState<string[]>([]);
  const [feedback, setFeedback] = useState<AssessmentFeedback[]>([]);
  const [feedbackKind, setFeedbackKind] = useState<AssessmentFeedback['kind'] | null>(null);
  const [feedbackText, setFeedbackText] = useState('');
  const [query, setQuery] = useState('');
  const [chatContext, setChatContext] = useState<string | null>(null);
  const [messages, setMessages] = useState<{ question: string; criterionId: string }[]>([]);
  const [dialog, setDialog] = useState<'invite' | 'reject' | null>(null);
  const [slot, setSlot] = useState('');
  const [reason, setReason] = useState('');
  const [complete, setComplete] = useState(false);
  const [resolved, setResolved] = useState(false);
  const active = selected ? named(selected) : null;
  const t = reduced ? { duration: 0 } : transition;
  const gaps = demoAssessment.criteria.filter(row => row.state === 'unknown');
  const topic = current >= 13 ? 'Разговор об опыте' : 'Учебный кейс';
  const [outline, setOutline] = useState(false);

  function seek(evidence: AssessmentEvidence) {
    setSource(evidence.id);
    if (evidence.seconds !== null) {
      const video = media.current?.querySelector('video');
      if (video) { const apply = () => { video.currentTime = evidence.seconds!; video.dispatchEvent(new Event('timeupdate', { bubbles: true })); setCurrent(evidence.seconds!); }; if (video.readyState >= 1) apply(); else video.addEventListener('loadedmetadata', apply, { once: true }); }
    }
  }
  function addQuestion(criterion: AssessmentCriterion) { setAgenda(rows => rows.includes(criterion.id) ? rows.filter(id => id !== criterion.id) : [...rows, criterion.id]); }
  function chat(criterion: AssessmentCriterion) { setPane('chat'); setChatContext(criterion.id); setQuery(`Что уточнить: ${criterion.label.toLowerCase()}?`); }
  function ask() {
    if (!query.trim()) return;
    const id = chatContext ?? (/потреб|диагност|заяв/.test(query.toLowerCase()) ? 'A04' : /следующ|шаг|договор/.test(query.toLowerCase()) ? 'A07' : 'A10');
    setMessages(rows => [...rows, { question: query.trim(), criterionId: id }]); setQuery('');
  }
  function saveFeedback() {
    if (!active || !feedbackKind) return;
    setFeedback(rows => [...rows.filter(row => row.criterionId !== active.id), { criterionId: active.id, revisionId: demoAssessment.revisionId, evidenceIds: active.evidence.map(row => row.id), kind: feedbackKind, text: feedbackText.trim() }]);
    if (feedbackKind === 'check' && !agenda.includes(active.id)) setAgenda(rows => [...rows, active.id]);
    setFeedbackKind(null); setFeedbackText('');
  }
  function showAnalysis(id?: string) { setSideHidden(false); setPane('analysis'); if (id) setSelected(id); }
  function toggleFullscreen() {
    if (document.fullscreenElement) void document.exitFullscreen();
    else { setFull(true); void container.current?.requestFullscreen?.().catch(() => {}); }
  }
  useEffect(() => { const change = () => setFull(document.fullscreenElement === container.current); const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOutline(false); if (!document.fullscreenElement) setFull(false); } }; document.addEventListener('fullscreenchange', change); document.addEventListener('keydown', key); return () => { document.removeEventListener('fullscreenchange', change); document.removeEventListener('keydown', key); }; }, []);
  function interceptPlayer(event: MouseEvent<HTMLDivElement>) {
    const label = (event.target as HTMLElement).closest('button')?.getAttribute('aria-label');
    if (!label || !['На весь экран', 'Выйти из полноэкранного режима', 'Режим кинотеатра', 'Написать комментарий'].includes(label)) return;
    event.preventDefault(); event.stopPropagation();
    if (label.includes('экран')) toggleFullscreen();
    else if (label === 'Режим кинотеатра') setSideHidden(value => !value);
    else { const criterion = demoAssessment.criteria.find(row => row.evidence.some(evidence => evidence.seconds !== null && Math.abs(evidence.seconds - current) < 6)) ?? named('A10'); showAnalysis(criterion.id); setFeedbackText(feedback.find(row => row.criterionId === criterion.id)?.text ?? ''); setFeedbackKind('check'); }
  }
  const sourceButton = (evidence: AssessmentEvidence) => <Tooltip.Root key={evidence.id}><Tooltip.Trigger asChild><button className="aip-source" aria-label={`Открыть источник ${evidence.time || 'без таймкода'}`} onClick={() => seek(evidence)}><Play size={10} fill="currentColor"/>{evidence.time || 'Реплика'}</button></Tooltip.Trigger><Tooltip.Portal container={container.current}><Tooltip.Content className="aip-tooltip" side="top" sideOffset={8}>{evidence.text}</Tooltip.Content></Tooltip.Portal></Tooltip.Root>;
  function findingDetails(criterion: AssessmentCriterion) {
    const existingFeedback = feedback.find(row => row.criterionId === criterion.id);
    return <div className="aip-finding-detail">
      <p className="aip-summary">{criterion.summary}</p>
      {criterion.evidence.map(evidence => <div className={`aip-quote${source === evidence.id ? ' aip-quote-selected' : ''}`} key={evidence.id}><p>«{evidence.text}»</p>{sourceButton(evidence)}</div>)}
      <p className="aip-limitation">{criterion.limitation || criterion.missingReason}</p>
      <div className="aip-next-question"><span>Проверить на встрече</span><p>{criterion.nextQuestion}</p><button className="aip-link" aria-pressed={agenda.includes(criterion.id)} onClick={() => addQuestion(criterion)}>{agenda.includes(criterion.id) ? <><Check size={13}/>В плане встречи</> : 'Добавить вопрос к встрече'}</button></div>
      <div className="aip-context-actions"><button className="aip-link" onClick={() => chat(criterion)}><Sparkles size={13}/>Разобрать в чате</button><button className="aip-link" onClick={() => { setSelected(criterion.id); setFeedbackKind(existingFeedback?.kind ?? 'disagree'); setFeedbackText(existingFeedback?.text ?? ''); }}>Уточнить вывод</button></div>
      {existingFeedback && !feedbackKind && <div className="aip-feedback-saved"><span>Ваше мнение · {existingFeedback.kind === 'agree' ? 'Согласен' : existingFeedback.kind === 'check' ? 'Проверю на встрече' : 'Иной вывод'}</span>{existingFeedback.text && <p>{existingFeedback.text}</p>}</div>}
      {feedbackKind && selected === criterion.id && <div className="aip-feedback"><label>Как вы видите этот вывод?</label><div role="group" aria-label="Отношение к выводу">{([{ id: 'agree', title: 'Согласен' }, { id: 'check', title: 'Проверю' }, { id: 'disagree', title: 'Не согласен' }] as const).map(row => <button key={row.id} aria-pressed={feedbackKind === row.id} onClick={() => setFeedbackKind(row.id)}>{row.title}</button>)}</div><textarea autoFocus aria-label="Основание обратной связи" placeholder="Что стоит учесть?" value={feedbackText} onChange={event => setFeedbackText(event.target.value)}/><div><button className="aip-secondary" onClick={saveFeedback}>Сохранить мнение</button><button className="aip-link" onClick={() => setFeedbackKind(null)}>Отмена</button></div></div>}
    </div>;
  }

  return <div ref={container} className={`aip-card${sideHidden ? ' aip-side-hidden' : ''}${full ? ' aip-full iv-full' : ''}`}>
    <div className="aip-media" ref={media} onTimeUpdateCapture={event => { if (event.target instanceof HTMLVideoElement) setCurrent(event.target.currentTime); }} onClickCapture={interceptPlayer}>
      <CandidateInterviewPlayer name={demoAssessment.candidate.name} sourceId="assessment-interpretation-demo" kind="screening" media={{ src: '/assessment-preview/interview.webm', localPreview: true }} chapters={chapters} transcriptText={transcript()} canComment header={<div className="ecp-video-identity"><button className="aip-candidate-name" onClick={onProfile}>{demoAssessment.candidate.name}<ArrowUpRight size={14}/></button><EmployerResumeDialog text={demoAssessment.candidate.resumeText}/></div>}/>
      <div className="aip-video-persona" aria-hidden="true"><span>МС</span><p>Учебная запись</p></div>
      {sideHidden && <button className="aip-restore aip-icon" aria-label="Показать панель" onClick={() => setSideHidden(false)}><PanelRightOpen size={19}/></button>}
      {source && <div className="aip-seek-note" role="status">Открыт источник · {clock(current)}</div>}
    </div>
    {!sideHidden && <aside className="aip-panel">
      <header className="aip-panel-header"><div className="aip-panel-tabs" role="group" aria-label="Материалы кандидата">{([{ id: 'transcript', title: 'Расшифровка', Icon: AlignLeft }, { id: 'chat', title: 'Спросить ИИ', Icon: Sparkles }] as const).map(({ id, title, Icon }) => <Tooltip.Root key={id}><Tooltip.Trigger asChild><button aria-pressed={pane === id} aria-label={title} onClick={() => setPane(id)}><Icon size={18}/></button></Tooltip.Trigger><Tooltip.Portal container={container.current}><Tooltip.Content className="aip-tooltip" side="bottom">{title}</Tooltip.Content></Tooltip.Portal></Tooltip.Root>)}{variant === 'inspector' && <button aria-pressed={pane === 'analysis'} className="aip-analysis-tab" onClick={() => showAnalysis()}>Разбор</button>}</div><button className="aip-icon" aria-label="Свернуть разбор и материалы" onClick={() => setSideHidden(true)}><PanelRightClose size={18}/></button></header>
      <LayoutGroup id={`assessment-${variant}`}><AnimatePresence mode="wait" initial={false}>
      {pane === 'analysis' ? <motion.div key="analysis" className="aip-analysis aip-panel-scroll" initial={{ opacity: 0, x: reduced ? 0 : 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} transition={t}>
        <div className="aip-panel-intro"><span>По материалам интервью</span><h2>{variant === 'decision' ? 'Что стоит уточнить' : 'Что показал разговор'}</h2><p>{variant === 'decision' ? 'Откройте основание и решите, о чём говорить дальше.' : 'Отдельные примеры, их границы и вопросы для встречи.'}</p></div>
        {variant === 'decision' && <button className="aip-link aip-back" onClick={() => setPane('transcript')}><ArrowLeft size={13}/>К расшифровке</button>}
        <div className="aip-findings">{(variant === 'decision' && !resolved ? [...gaps, named('A04'), named('A10'), named('A07')] : demoAssessment.criteria).map(criterion => <section className={`aip-finding${selected === criterion.id ? ' aip-finding-open' : ''}`} key={criterion.id}><button className="aip-finding-trigger" aria-expanded={selected === criterion.id} onClick={() => { setSelected(selected === criterion.id ? null : criterion.id); setFeedbackKind(null); }}><span><small>{criterion.stateLabel}</small><strong>{criterion.label}</strong></span><ChevronDown size={15}/></button><AnimatePresence initial={false}>{selected === criterion.id && <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={t} style={{ overflow: 'hidden' }}>{findingDetails(criterion)}</motion.div>}</AnimatePresence></section>)}</div>
        {variant === 'decision' && !resolved && <button className="aip-link aip-resolve" onClick={() => { setResolved(true); setPane('transcript'); }}>Пока достаточно информации</button>}
      </motion.div> : pane === 'chat' ? <motion.div key="chat" className="aip-chat" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={t}>{chatContext && <button className="aip-pinned" onClick={() => showAnalysis(chatContext)}><ArrowLeft size={13}/><span>Разбираем вывод<strong>{named(chatContext).label}</strong></span></button>}<div className="aip-chat-history">{!messages.length ? <div className="aip-chat-empty"><Sparkles size={25}/><h2>Разобраться в интервью</h2><p>Уточните вывод или спросите об опыте. Источник останется рядом с ответом.</p><button className="aip-secondary" onClick={() => setQuery('Что стоит уточнить на встрече?')}>Что стоит уточнить?</button></div> : messages.map((message, index) => { const criterion = named(message.criterionId); return <div className="aip-message" key={index}><p className="aip-user-question">{message.question}</p><p>{criterion.summary} {criterion.evidence.map(evidence => sourceButton(evidence))}</p><p className="aip-chat-caveat">{criterion.limitation}</p><button className="aip-link" onClick={() => addQuestion(criterion)}>{agenda.includes(criterion.id) ? 'Вопрос добавлен к встрече' : 'Добавить вопрос к встрече'}</button></div>; })}</div><form className="aip-composer" onSubmit={event => { event.preventDefault(); ask(); }}><Sparkles size={17}/><input aria-label="Вопрос к интервью" placeholder="Спросите об интервью…" value={query} onChange={event => setQuery(event.target.value)}/><button type="submit" disabled={!query.trim()} aria-label="Отправить вопрос"><ArrowUp size={18}/></button></form></motion.div> : <motion.div key="transcript" className="aip-transcript aip-panel-scroll" ref={transcriptList} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={t}>
        <div className="aip-chapter"><button aria-expanded={outline} onClick={() => setOutline(value => !value)}>{topic}<ChevronDown size={14}/></button>{outline && <div>{chapters.filter(chapter => chapter.title !== topic).map(chapter => <button key={chapter.title} onClick={() => { const video = media.current?.querySelector('video'); if (video) video.currentTime = chapter.seconds; setCurrent(chapter.seconds); setOutline(false); }}>{chapter.title}</button>)}</div>}</div>
        {demoTurns.map(turn => { const criteria = demoAssessment.criteria.filter(criterion => criterion.evidence.some(evidence => evidence.turnIds?.includes(turn.id) && (variant !== 'lens' || !!evidence.text.trim()))); const isSource = criteria.some(criterion => criterion.evidence.some(evidence => evidence.id === source)); return <section key={turn.id} className={`aip-turn${isSource ? ' aip-turn-selected' : ''}`}><button className="aip-turn-text" onClick={() => { if (turn.seconds !== null) { const video = media.current?.querySelector('video'); if (video) video.currentTime = turn.seconds; setCurrent(turn.seconds); } }}><time>{turn.seconds === null ? '' : clock(turn.seconds)}</time><span><small>{turn.speaker}</small>{turn.text}</span></button>{variant === 'lens' && criteria.map(criterion => <div className="aip-lens" key={criterion.id}><button className="aip-lens-label" aria-expanded={selected === criterion.id} onClick={() => { setSelected(selected === criterion.id ? null : criterion.id); setFeedbackKind(null); }}><span>{criterion.label}</span><ChevronDown size={13}/></button><AnimatePresence initial={false}>{selected === criterion.id && <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={t} style={{ overflow: 'hidden' }}>{findingDetails(criterion)}</motion.div>}</AnimatePresence></div>)}</section>; })}
      </motion.div>}
      </AnimatePresence></LayoutGroup>
    </aside>}
    <footer className="aip-card-footer"><div className="aip-decision-context">{variant === 'decision' ? <button className="aip-decision-entry" onClick={() => showAnalysis('A06.1')}>{resolved ? 'Разбор просмотрен' : 'Перед встречей: как работает с сомнениями?'}<ChevronDown size={14}/></button> : agenda.length ? <span>{`В план встречи добавлено: ${agenda.length}`}</span> : variant === 'lens' ? null : <span>Разбор по материалам интервью</span>}{resolved && <button className="aip-link" onClick={() => setResolved(false)}>Вернуть вопрос</button>}</div><div className="aip-decisions"><button className="aip-link" onClick={() => { setDialog('reject'); setComplete(false); }}>Отказать</button><button className="aip-primary" onClick={() => { setDialog('invite'); setComplete(false); }}>Позвать на встречу<ArrowUpRight size={17}/></button></div></footer>
    <Dialog.Root open={!!dialog} onOpenChange={open => { if (!open) { setDialog(null); setComplete(false); } }}><Dialog.Portal container={full ? container.current : undefined}><Dialog.Overlay className="aip-scrim"/><Dialog.Content className="aip-dialog"><header><Dialog.Title>{complete ? 'Предпросмотр готов' : dialog === 'invite' ? 'Следующая встреча' : 'Причина решения'}</Dialog.Title><Dialog.Close aria-label="Закрыть предпросмотр" className="aip-icon"><X size={19}/></Dialog.Close></header><Dialog.Description>{complete ? 'Это макет. Кандидату ничего не отправлено.' : dialog === 'invite' ? 'Время из доступных слотов кандидата. Вопросы остаются внутренней повесткой.' : 'Решение работодателя сохраняется отдельно от оценки.'}</Dialog.Description>{complete ? <><div className="aip-outcome"><Check size={24}/><h3>{dialog === 'invite' ? `Встреча · ${slot}` : 'Отказ · с указанной причиной'}</h3><p>{dialog === 'invite' ? 'Выбранные вопросы включены в план встречи.' : reason}</p></div><Dialog.Close className="aip-primary">Вернуться к кандидату</Dialog.Close></> : dialog === 'invite' ? <><div className="aip-slots" aria-label="Выбрать время">{['27 октября, 11:00', '27 октября, 15:30', '28 октября, 12:00'].map(value => <button key={value} aria-pressed={slot === value} onClick={() => setSlot(value)}>{value}{slot === value && <Check size={14}/>}</button>)}</div><section className="aip-agenda-preview"><h3>О чём поговорим</h3>{agenda.length ? <ul>{agenda.map(id => <li key={id}>{named(id).nextQuestion}</li>)}</ul> : <p>Пока без отдельных вопросов. Их можно добавить из разбора.</p>}</section><button className="aip-primary" disabled={!slot} onClick={() => setComplete(true)}>Подготовить приглашение<ArrowUpRight size={16}/></button></> : <><div className="aip-reasons">{['Не подходит опыт', 'Не совпали условия', 'Выбрали другого кандидата'].map(value => <button key={value} aria-pressed={reason === value} onClick={() => setReason(value)}>{value}</button>)}</div><textarea aria-label="Причина отказа" placeholder="Коротко поясните решение" value={reason} onChange={event => setReason(event.target.value)}/><button className="aip-primary" disabled={!reason.trim()} onClick={() => setComplete(true)}>Подготовить отказ</button></>}</Dialog.Content></Dialog.Portal></Dialog.Root>
  </div>;
}
