import { useEffect, useMemo, useRef, useState, type MouseEvent } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { Dialog, Tooltip } from 'radix-ui';
import { AlignLeft, ArrowUp, ArrowUpRight, ChartNoAxesCombined, Check, ChevronDown, MessageSquare, PanelRightClose, PanelRightOpen, Play, Sparkles, X } from 'lucide-react';
import { CandidateInterviewPlayer } from '../interview-review/CandidateInterviewPlayer';
import { EmployerResumeDialog } from '../employer-workspace/EmployerResumeDialog';
import { AssessmentRadar, skillName } from './radar/AssessmentRadar';
import { demoAssessment, demoTurns } from './assessment-fixture';
import type { AssessmentEvidence, CandidateAssessmentView } from './assessment-view';
import { GraphGallery } from './explorations/GraphGallery';
import { InterviewAnalysisSwitch } from './explorations/InterviewAnalysisSwitch';
import type { AssessmentGraphVariant } from './explorations/graph-contract';
import { blocksAtTime, feedbackBlocks, initialFeedbackPlayback, opinionKey, readOpinion, reduceFeedbackPlayback, visibleFeedbackBlock, type FeedbackEvent, type FragmentOpinion } from './explorations/fragment-feedback';
import './radar-candidate.css';

type Fit = 'yes' | 'no' | 'unknown';
const clock = (value: number) => `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(Math.floor(value % 60)).padStart(2, '0')}`;
export type AssessmentTurn = { id: number; seconds: number | null; endSeconds?: number; speaker: string; text: string };
export type AssessmentChapter = { title: string; seconds: number; detail: string; fragment: string; source: string };
const demoChapters = [{ title: 'Учебный кейс', seconds: 0, detail: '', fragment: '', source: '00:00' }, { title: 'Разговор об опыте', seconds: 13, detail: '', fragment: '', source: '00:13' }];
export type RadarCandidatePreviewProps = {
  onProfile: () => void;
  data?: CandidateAssessmentView;
  turns?: AssessmentTurn[];
  mediaSrc?: string;
  resumeUrl?: string;
  chapters?: AssessmentChapter[];
  realData?: boolean;
  initialEvidence?: AssessmentEvidence | null;
  initialMode?: 'video' | 'map';
  analysisInSidebar?: boolean;
  graphVariant?: AssessmentGraphVariant;
};
const fitOptions = [{ id: 'yes', label: 'Скорее да' }, { id: 'no', label: 'Скорее нет' }, { id: 'unknown', label: 'Не могу сказать' }] as const;

/** Presentation-only interaction model. Data and time references come from the caller. */
export function RadarCandidatePreview({ onProfile, data = demoAssessment, turns = demoTurns, mediaSrc = '/assessment-preview/interview.webm', resumeUrl, chapters = demoChapters, realData = false, initialEvidence = null, initialMode = 'video', analysisInSidebar = false, graphVariant }: RadarCandidatePreviewProps) {
  const exploration = realData && !!graphVariant;
  const sidebarAnalysis = exploration && analysisInSidebar;
  const criteria = useMemo(() => data.criteria.filter(criterion => criterion.evidence.length > 0), [data.criteria]);
  const blocks = useMemo(() => feedbackBlocks(criteria, turns), [criteria, turns]);
  const selectedRef = useRef(criteria[0]?.id ?? '');
  const criterionFor = (evidence: AssessmentEvidence) => (exploration ? criteria.find(criterion => criterion.id === selectedRef.current && criterion.evidence.some(item => item.id === evidence.id)) : undefined) ?? criteria.find(criterion => criterion.evidence.some(item => item.id === evidence.id));
  const transcript = turns.filter(turn => turn.seconds !== null).map((turn, index, rows) => `[${clock(turn.seconds!)}-${clock(turn.endSeconds ?? rows[index + 1]?.seconds ?? (turn.seconds! + 1))}] ${/^(?:Р|Рекрутер|recruiter|interviewer)$/i.test(turn.speaker.trim()) ? 'Р' : 'К'}: ${turn.text}`).join('\n');
  const feedbackKey = `hrv-assessment-feedback:${data.candidateId ?? data.candidate.name}:${data.revisionId}`;
  const reduced = useReducedMotion();
  const root = useRef<HTMLDivElement>(null);
  const media = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const mapTrigger = useRef<HTMLButtonElement>(null);
  const returnVideo = useRef<HTMLButtonElement>(null);
  const pendingSeek = useRef<number | null>(null);
  const reading = useRef(false);
  const [mode, setMode] = useState<'video' | 'map'>(sidebarAnalysis ? 'video' : initialMode);
  const [selected, setSelected] = useState(criteria[0]?.id ?? '');
  const [pane, setPane] = useState<'transcript' | 'chat' | 'analysis'>(sidebarAnalysis ? 'analysis' : realData ? 'transcript' : 'chat');
  const [source, setSource] = useState<AssessmentEvidence | null>(null);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playback, setPlayback] = useState(initialFeedbackPlayback);
  const [opinions, setOpinions] = useState<Record<string, FragmentOpinion>>(() => {
    if (!exploration) return {};
    const values: Record<string, FragmentOpinion> = {};
    for (const block of blocks) {
      const key = opinionKey(data.candidateId ?? data.candidate.name, data.revisionId, block);
      try { const value = readOpinion(localStorage.getItem(key)); if (value) values[key] = value; } catch { /* Storage is optional. */ }
    }
    return values;
  });
  const [feedback, setFeedback] = useState<Record<string, Fit>>(() => {
    if (!realData) return {};
    try { return JSON.parse(localStorage.getItem(feedbackKey) || '{}'); } catch { return {}; }
  });
  useEffect(() => { if (realData && !exploration) { try { localStorage.setItem(feedbackKey, JSON.stringify(feedback)); } catch { /* Browser storage may be disabled. */ } } }, [feedback, feedbackKey, realData, exploration]);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [agenda, setAgenda] = useState<string[]>([]);
  const [question, setQuestion] = useState('');
  const [answers, setAnswers] = useState<{ question: string; criterionId: string }[]>([]);
  const [hidden, setHidden] = useState(false);
  const [full, setFull] = useState(false);
  const [dialog, setDialog] = useState<'invite' | 'reject' | null>(null);
  const [slot, setSlot] = useState('');
  const [reason, setReason] = useState('');
  const [complete, setComplete] = useState(false);
  const [outline, setOutline] = useState(false);
  const active = criteria.find(criterion => criterion.id === selected) ?? criteria[0];
  const chapter = [...chapters].reverse().find(item => item.seconds <= current) ?? chapters[0];
  const reviewSkill = criteria.find(criterion => criterion.id === reviewing);
  const feedbackBlock = visibleFeedbackBlock(playback, blocks);
  const feedbackStorageKey = feedbackBlock ? opinionKey(data.candidateId ?? data.candidate.name, data.revisionId, feedbackBlock) : '';
  const opinion = opinions[feedbackStorageKey] ?? { comment: '' };
  const currentQuestions = blocksAtTime(blocks, playback.time).filter((block, index, all) => all.findIndex(item => item.criterionId === block.criterionId) === index);
  function feedbackEvent(event: FeedbackEvent) { setPlayback(state => reduceFeedbackPlayback(state, event, blocks)); }
  function selectCriterion(id: string) {
    selectedRef.current = id; setSelected(id);
    if (exploration) feedbackEvent({ type: 'prefer', criterionId: id });
  }
  function saveOpinion(change: Partial<FragmentOpinion>) {
    if (!feedbackStorageKey) return;
    const next = { ...opinion, ...change };
    setOpinions(values => ({ ...values, [feedbackStorageKey]: next }));
    try { localStorage.setItem(feedbackStorageKey, JSON.stringify(next)); } catch { /* Keep the current session usable without storage. */ }
  }
  const t = reduced ? { duration: 0 } : { type: 'spring' as const, duration: .36, bounce: 0 };
  const video = () => media.current?.querySelector('video');
  function openMap() {
    if (sidebarAnalysis) { setPane('analysis'); setHidden(false); return; }
    video()?.pause(); setMode('map');
    if (!exploration) requestAnimationFrame(() => returnVideo.current?.focus({ preventScroll: true }));
  }
  function showVideo() {
    setMode('video');
    if (!exploration) requestAnimationFrame(() => mapTrigger.current?.focus({ preventScroll: true }));
  }
  function seek(evidence: AssessmentEvidence, criterionId?: string) {
    const criterion = criteria.find(item => item.id === criterionId) ?? criterionFor(evidence);
    if (criterion) {
      selectedRef.current = criterion.id; setSelected(criterion.id); setReviewing(criterion.id);
      if (exploration) feedbackEvent({ type: 'source', target: { criterionId: criterion.id, evidenceId: evidence.id }, time: evidence.seconds });
    }
    reading.current = false;
    pendingSeek.current = null;
    setSource(evidence); setMode('video'); setHidden(false); if (!sidebarAnalysis || pane !== 'analysis') setPane('transcript'); setOutline(false);
    if (evidence.seconds !== null) {
      pendingSeek.current = evidence.seconds;
      const player = video();
      if (player && player.readyState >= 1) { player.pause(); player.currentTime = evidence.seconds; setCurrent(evidence.seconds); pendingSeek.current = null; }
    }
    requestAnimationFrame(() => {
      if (sidebarAnalysis && pane === 'analysis') { media.current?.querySelector<HTMLButtonElement>('button[aria-label="Воспроизвести"]')?.focus({ preventScroll: true }); return; }
      const row = Array.from(list.current?.querySelectorAll<HTMLElement>('[data-turn-id]') ?? []).find(item => item.dataset.evidence === evidence.id || evidence.turnIds?.includes(Number(item.dataset.turnId))); row?.scrollIntoView({ block: 'center', behavior: reduced ? 'instant' : 'smooth' }); row?.focus({ preventScroll: true });
    });
  }
  useEffect(() => { if (initialEvidence) seek(initialEvidence); }, [initialEvidence?.id]);
  function inspectVisibleTurn() {
    if (exploration) return;
    if (!reading.current || mode !== 'video' || !list.current) return;
    const bounds = list.current.getBoundingClientRect();
    const candidates = Array.from(list.current.querySelectorAll<HTMLElement>('[data-criterion]'))
      .map(row => ({ row, rect: row.getBoundingClientRect() }))
      .filter(({ rect }) => Math.min(rect.bottom, bounds.bottom) - Math.max(rect.top, bounds.top) >= Math.min(rect.height * .5, bounds.height * .4))
      .sort((a, b) => Math.abs(a.rect.top - bounds.top - 50) - Math.abs(b.rect.top - bounds.top - 50));
    const id = candidates[0]?.row.dataset.criterion;
    setReviewing(id ?? null);
    if (id) setSelected(id);
  }
  function watchMoment(player: HTMLVideoElement, cause: 'tick' | 'play' | 'pause' | 'seek' | 'end' = 'tick') {
    setCurrent(player.currentTime);
    if (exploration) {
      feedbackEvent({ type: 'media', time: player.currentTime, playing: !player.paused && !player.ended, cause });
      return;
    }
    if (mode !== 'video' || player.paused || player.seeking || reading.current) return;
    const turn = [...turns].reverse().find(item => item.seconds !== null && item.seconds <= player.currentTime);
    const criterion = criteria.find(item => item.evidence.some(evidence => turn && evidence.turnIds?.includes(turn.id)));
    setReviewing(criterion?.id ?? null);
    if (criterion) setSelected(criterion.id);
  }
  function ask() {
    if (!question.trim()) return;
    setAnswers(items => [...items, { question: question.trim(), criterionId: selected }]); setQuestion('');
  }
  function fullscreen() {
    if (document.fullscreenElement) void document.exitFullscreen();
    else if (full) setFull(false);
    else { setFull(true); void root.current?.requestFullscreen?.().catch(() => {}); }
  }
  useEffect(() => { const change = () => setFull(document.fullscreenElement === root.current); const escape = (event: KeyboardEvent) => { if (event.key === 'Escape' && !document.fullscreenElement) setFull(false); }; document.addEventListener('fullscreenchange', change); document.addEventListener('keydown', escape); return () => { document.removeEventListener('fullscreenchange', change); document.removeEventListener('keydown', escape); }; }, []);
  function intercept(event: MouseEvent<HTMLDivElement>) {
    const label = (event.target as HTMLElement).closest('button')?.getAttribute('aria-label');
    if (!label || !['На весь экран', 'Выйти из полноэкранного режима', 'Режим кинотеатра', 'Написать комментарий'].includes(label)) return;
    event.preventDefault(); event.stopPropagation();
    if (label.includes('экран')) fullscreen();
    else if (label === 'Режим кинотеатра') setHidden(value => !value);
    else openMap();
  }
  const citation = (evidence: AssessmentEvidence) => <Tooltip.Root key={evidence.id}><Tooltip.Trigger asChild><button className="arv-citation" aria-label={`Открыть источник ${evidence.time || 'в расшифровке'}`} onClick={() => seek(evidence)}><Play size={11} fill="currentColor"/><span>{evidence.time || 'Реплика'}</span></button></Tooltip.Trigger><Tooltip.Portal container={root.current}><Tooltip.Content className="aip-tooltip" side="top">{evidence.text}</Tooltip.Content></Tooltip.Portal></Tooltip.Root>;
  return <article ref={root} data-mode={mode} data-graph={graphVariant} data-real={realData || undefined} className={`arv${exploration ? ' arv-exploration' : ''}${sidebarAnalysis ? ' arv-sidebar-analysis' : ''}${full ? ' arv-full iv-full' : ''}${hidden ? ' arv-wide' : ''}`}>
    <div className="arv-stage">
      {exploration && !sidebarAnalysis && <div className="arv-mode-row"><InterviewAnalysisSwitch mode={mode} onChange={next => { if (next === 'map') openMap(); else showVideo(); }}/></div>}
      <motion.div ref={media} className="arv-media aip-media" data-hidden={mode === 'map'} animate={{ opacity: mode === 'map' ? 0 : 1, scale: mode === 'map' && !reduced ? .97 : 1 }} transition={t} aria-hidden={mode === 'map'} inert={mode === 'map'} onClickCapture={intercept}
        onPointerDownCapture={() => { reading.current = false; }}
        onKeyDownCapture={() => { reading.current = false; }}
        onPlayCapture={event => { reading.current = false; if (event.target instanceof HTMLVideoElement) watchMoment(event.target, 'play'); }}
        onPauseCapture={event => { if (event.target instanceof HTMLVideoElement && exploration) watchMoment(event.target, 'pause'); }}
        onSeekingCapture={event => { if (event.target instanceof HTMLVideoElement && exploration) watchMoment(event.target, 'seek'); }}
        onSeekedCapture={event => { if (event.target instanceof HTMLVideoElement && exploration) watchMoment(event.target, 'seek'); }}
        onEndedCapture={event => { if (event.target instanceof HTMLVideoElement && exploration) watchMoment(event.target, 'end'); }}
        onTimeUpdateCapture={event => { if (event.target instanceof HTMLVideoElement) watchMoment(event.target); }}
        onLoadedMetadata={event => { if (event.target instanceof HTMLVideoElement) { setDuration(Number.isFinite(event.target.duration) ? event.target.duration : 0); if (pendingSeek.current !== null) { event.target.currentTime = pendingSeek.current; setCurrent(pendingSeek.current); pendingSeek.current = null; } } }}>
        <CandidateInterviewPlayer sourceId={`assessment:${data.candidateId ?? 'demo'}:${data.revisionId}`} name={data.candidate.name} kind="screening" media={mediaSrc ? { src: mediaSrc, localPreview: !realData } : undefined} emptyMessage="Запись недоступна. Читайте расшифровку справа." chapters={chapters} transcriptText={transcript}
          header={<div className="ecp-video-identity"><button className="aip-candidate-name" onClick={onProfile}>{data.candidate.name}<ArrowUpRight size={14}/></button><EmployerResumeDialog text={data.candidate.resumeText} resume={resumeUrl ? { id: resumeUrl, name: 'Резюме.pdf', load: async signal => { const response = await fetch(resumeUrl, { signal, cache: 'no-store' }); if (!response.ok) throw new Error('Не удалось открыть оригинал резюме.'); return response.blob(); } } : undefined}/></div>}/>
        {!realData && <div className="aip-video-persona" aria-hidden="true"><span>МС</span><p>Учебная запись</p></div>}
      </motion.div>
      <AnimatePresence initial={false}>{!sidebarAnalysis && mode === 'map' && <motion.section className="arv-map" key="map" initial={{ opacity: 0, y: reduced ? 0 : 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: reduced ? 0 : 8 }} transition={t} aria-label="Карта навыков кандидата">
        <header className="arv-map-header"><h2>{data.candidate.name}</h2>{!exploration && <button ref={returnVideo} onClick={showVideo} className="arv-video-pill"><Play size={13} fill="currentColor"/>Интервью</button>}</header>
        <div className={exploration ? 'arv-exploration-chart' : 'arv-chart'}>{exploration && graphVariant ? <GraphGallery variant={graphVariant} criteria={criteria} selectedId={selected} onSelect={selectCriterion} onEvidence={(evidence: AssessmentEvidence) => seek(evidence)} duration={duration}/> : <AssessmentRadar criteria={criteria} selectedId={selected} onSelect={setSelected}/>}</div>
        {active && !(exploration && (graphVariant === 'orbit' || graphVariant === 'matrix')) && <section className="arv-detail" aria-live="polite">
          <h3>{active ? skillName(active) : 'Материалы интервью'}</h3>
          <div className="arv-quotes">{active.evidence.map(evidence => <button key={evidence.id} className="arv-quote" onClick={() => seek(evidence, active.id)} aria-label={`Посмотреть ${exploration ? 'источник объяснения' : 'реплику'} ${evidence.time || 'без таймкода'}`}>
            <span><Play size={12} fill="currentColor"/>{evidence.time || 'Источник'}</span><p>{exploration ? evidence.explanation || active.summary || 'Посмотрите исходный фрагмент для проверки этого навыка.' : `«${evidence.text}»`}</p>
          </button>)}</div>
        </section>}
      </motion.section>}</AnimatePresence>
      {mode === 'video' && <div className="arv-below-player">
        {!exploration && <button ref={mapTrigger} className="arv-map-entry" onClick={openMap}><ChartNoAxesCombined size={18}/><span>Карта навыков</span><ArrowUpRight size={14}/></button>}
        {exploration && feedbackBlock && <motion.section key={feedbackBlock.key} className="arv-fragment-feedback" data-playing={playback.playing && !opinion.answer || undefined} aria-label={`Ваше мнение по фрагменту: ${skillName(feedbackBlock.criterion)}`} initial={{ opacity: reduced ? 1 : 0, backgroundColor: playback.playing && !reduced ? '#eee6f8' : '#ffffff' }} animate={{ opacity: 1, backgroundColor: '#ffffff' }} transition={{ duration: reduced ? 0 : .55 }}>
          <div className="arv-fragment-heading"><p>{feedbackBlock.evidence.feedbackQuestion || `По этому фрагменту навык «${skillName(feedbackBlock.criterion)}» проявлен достаточно для вашей задачи?`}</p><button className="arv-fragment-comment" aria-label={playback.commentLock ? 'Закрыть комментарий' : 'Добавить комментарий к фрагменту'} aria-expanded={!!playback.commentLock} onClick={() => feedbackEvent({ type: playback.commentLock ? 'comment-close' : 'comment-open' })}><MessageSquare size={17}/>{opinion.comment && <i aria-label="Есть комментарий"/>}</button></div>
          {!playback.commentLock && currentQuestions.length > 1 && <div className="arv-fragment-context"><span>В этом фрагменте:</span>{currentQuestions.map(block => <button key={block.criterionId} aria-pressed={feedbackBlock.criterionId === block.criterionId} onClick={() => selectCriterion(block.criterionId)}>{skillName(block.criterion)}</button>)}</div>}
          <div className="arv-fragment-answers" role="group" aria-label="Ваш ответ по фрагменту">{fitOptions.map(item => <button key={item.id} data-answer={item.id} aria-pressed={opinion.answer === item.id} onClick={() => saveOpinion({ answer: item.id })}>{item.label}</button>)}</div>
          {playback.commentLock && <div className="arv-fragment-note"><label htmlFor="arv-fragment-comment">Комментарий к фрагменту {feedbackBlock.evidence.time}</label><textarea id="arv-fragment-comment" value={opinion.comment} placeholder="Что вы заметили или хотите уточнить?" onChange={event => saveOpinion({ comment: event.target.value })}/><div><span>Сохраняется в этом браузере</span><button onClick={() => feedbackEvent({ type: 'comment-close' })}>Готово</button></div></div>}
        </motion.section>}
        <AnimatePresence initial={false}>{!exploration && reviewSkill && <motion.section className="arv-fit" key="fit" aria-label={`Ваше мнение: ${skillName(reviewSkill)}`} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: reduced ? 0 : .15 }}>
          <h3>Подходит вашей задаче по навыку «{skillName(reviewSkill)}»?</h3>
          <div role="group" aria-label={`Оценить навык: ${skillName(reviewSkill)}`}>{fitOptions.map(item => <button key={item.id} aria-pressed={feedback[reviewSkill.id] === item.id} onClick={() => setFeedback(values => ({ ...values, [reviewSkill.id]: item.id }))}>{item.label}</button>)}</div>
        </motion.section>}</AnimatePresence>
      </div>}
    </div>
    {!hidden && <aside className="arv-panel"><header className="arv-panel-top"><div className="aip-panel-tabs" role="group" aria-label="Материалы интервью">{([{ id: 'transcript', label: 'Расшифровка', Icon: AlignLeft }, { id: 'chat', label: realData ? 'Источники оценки' : 'Спросить ИИ', Icon: sidebarAnalysis ? Sparkles : realData ? ChartNoAxesCombined : Sparkles }, ...(sidebarAnalysis ? [{ id: 'analysis' as const, label: 'Оценка навыков', Icon: ChartNoAxesCombined }] : [])] as const).map(({ id, label, Icon }) => <Tooltip.Root key={id}><Tooltip.Trigger asChild><button aria-label={label} aria-pressed={pane === id} onClick={() => setPane(id)}><Icon size={18}/></button></Tooltip.Trigger><Tooltip.Portal container={root.current}><Tooltip.Content className="aip-tooltip" side="bottom">{label}</Tooltip.Content></Tooltip.Portal></Tooltip.Root>)}</div><button className="aip-icon" aria-label="Свернуть материалы" onClick={() => setHidden(true)}><PanelRightClose size={18}/></button></header>
      {sidebarAnalysis && graphVariant && <div className="arv-analysis-pane" hidden={pane !== 'analysis'} aria-label="Оценка навыков"><GraphGallery variant={graphVariant} criteria={criteria} selectedId={selected} onSelect={selectCriterion} onEvidence={evidence => seek(evidence)} duration={duration}/></div>}
      {pane === 'analysis' ? null : pane === 'transcript' ? <div className="arv-transcript" ref={list}
        onWheel={() => { reading.current = true; inspectVisibleTurn(); }}
        onTouchMove={() => { reading.current = true; }}
        onPointerDown={() => { reading.current = true; }}
        onKeyDown={event => { if (['PageDown', 'PageUp', 'ArrowDown', 'ArrowUp', 'Home', 'End', ' '].includes(event.key)) reading.current = true; }}
        onScroll={inspectVisibleTurn}>
        <div className="aip-chapter"><button aria-expanded={outline} onClick={() => setOutline(value => !value)}>{chapter?.title ?? 'Интервью'}<ChevronDown size={14}/></button>{outline && <div>{chapters.map(chapter => <button key={chapter.title} onClick={() => { const player = video(); if (player) player.currentTime = chapter.seconds; setCurrent(chapter.seconds); setReviewing(null); if (exploration) feedbackEvent({ type: 'media', time: chapter.seconds, playing: !!player && !player.paused, cause: 'seek' }); setOutline(false); }}>{chapter.title}</button>)}</div>}</div>
        {turns.map(turn => {
          const matchingCriteria = criteria.filter(item => item.evidence.some(evidence => evidence.turnIds?.includes(turn.id)));
          const criterion = matchingCriteria.find(item => item.id === selected) ?? matchingCriteria[0];
          const evidence = criterion?.evidence.find(item => item.turnIds?.includes(turn.id));
          return <div className="arv-turn-block" data-criterion={criterion?.id} key={turn.id}>
            <button className={`arv-turn${source?.turnIds?.includes(turn.id) || (source?.id === evidence?.id && evidence) ? ' arv-turn-source' : ''}`} data-evidence={evidence?.id} data-turn-id={turn.id}
              onClick={() => { if (evidence) seek(evidence, criterion?.id); else if (turn.seconds !== null) { const player = video(); if (player) player.currentTime = turn.seconds; setCurrent(turn.seconds); setReviewing(null); if (exploration) feedbackEvent({ type: 'media', time: turn.seconds, playing: !!player && !player.paused, cause: 'seek' }); } }}>
              <time>{turn.seconds === null ? '' : clock(turn.seconds)}</time><span><small>{turn.speaker}</small>{turn.text}</span>
            </button>
            {matchingCriteria.map(item => <button key={item.id} className="arv-turn-skill" onClick={() => { selectCriterion(item.id); openMap(); }}>{skillName(item)}<ArrowUpRight size={12}/></button>)}
          </div>;
        })}
      </div> : realData ? <div className="arv-grounded-sources">
        {criteria.map(criterion => <section key={criterion.id}><button className="arv-source-skill" onClick={() => { selectCriterion(criterion.id); openMap(); }}>{skillName(criterion)}<ArrowUpRight size={13}/></button>{criterion.evidence.map(evidence => <button className="arv-source-excerpt" key={evidence.id} onClick={() => seek(evidence, criterion.id)}><span>{evidence.time || 'Реплика'}</span><p>{exploration ? evidence.explanation || criterion.summary || 'Посмотрите исходный фрагмент для проверки этого навыка.' : `«${evidence.text}»`}</p></button>)}</section>)}
        {!criteria.length && <p className="arv-source-empty">В этой записи пока нет подтверждённых источников для оценки.</p>}
      </div> : <div className="arv-chat"><div className="arv-chat-context"><button onClick={openMap}>{active ? skillName(active) : 'Материалы интервью'}<ArrowUpRight size={12}/></button></div><div className="arv-chat-history">{!answers.length ? <div className="arv-empty"><Sparkles size={25}/><h2>Что важно уточнить?</h2><button onClick={() => setQuestion('На чём основана эта оценка?')}>На чём основана оценка?</button><button onClick={() => setQuestion(active?.nextQuestion ?? '')}>Что проверить на встрече?</button></div> : answers.map((answer, index) => { const criterion = data.criteria.find(item => item.id === answer.criterionId); if (!criterion) return null; return <div className="arv-answer" key={index}><p className="aip-user-question">{answer.question}</p><p>{criterion.summary} {criterion.evidence.map(citation)}</p></div>; })}</div><form className="aip-composer" onSubmit={event => { event.preventDefault(); ask(); }}><Sparkles size={17}/><input aria-label="Вопрос по навыку" placeholder="Спросите об интервью…" value={question} onChange={event => setQuestion(event.target.value)}/><button type="submit" aria-label="Отправить вопрос" disabled={!question.trim()}><ArrowUp size={17}/></button></form></div>}
    </aside>}
    {hidden && <button className="arv-restore aip-icon" aria-label="Показать материалы" onClick={() => setHidden(false)}><PanelRightOpen size={18}/></button>}
    <footer className="arv-footer"><span/><div>{(sidebarAnalysis ? ['invite', 'reject'] as const : ['reject', 'invite'] as const).map(action => <button key={action} className={action === 'invite' ? 'aip-primary' : 'aip-link'} onClick={() => { setDialog(action); setComplete(false); }}>{action === 'invite' ? <>Позвать на встречу<ArrowUpRight size={16}/></> : 'Отказать'}</button>)}</div></footer>
    <Dialog.Root open={!!dialog} onOpenChange={open => { if (!open) setDialog(null); }}><Dialog.Portal container={full ? root.current : undefined}><Dialog.Overlay className="aip-scrim"/><Dialog.Content className="aip-dialog"><header><Dialog.Title>{complete ? 'Предпросмотр готов' : dialog === 'invite' ? 'Следующая встреча' : 'Причина решения'}</Dialog.Title><Dialog.Close className="aip-icon" aria-label="Закрыть предпросмотр"><X size={18}/></Dialog.Close></header><Dialog.Description>{complete ? 'Это макет. Кандидату ничего не отправлено.' : dialog === 'invite' ? realData ? 'Это локальный предпросмотр решения. Время встречи и отправка кандидату здесь не назначаются.' : 'Выберите время из слотов кандидата. Вопросы останутся в плане встречи.' : 'Решение сохраняется отдельно от оценки навыков.'}</Dialog.Description>{complete ? <><div className="aip-outcome"><Check size={24}/><h3>{dialog === 'invite' ? `Встреча · ${slot}` : 'Отказ подготовлен'}</h3><p>{dialog === 'invite' ? 'Вопросы к навыкам добавлены к встрече.' : reason}</p></div><Dialog.Close className="aip-primary">Вернуться к кандидату</Dialog.Close></> : dialog === 'invite' ? <><div className="aip-slots">{(realData ? ['Обсудить следующую встречу'] : ['27 октября, 11:00', '27 октября, 15:30', '28 октября, 12:00']).map(value => <button key={value} aria-pressed={slot === value} onClick={() => setSlot(value)}>{value}{slot === value && <Check size={13}/>}</button>)}</div><div className="aip-agenda-preview"><h3>Вопросы к встрече</h3><div className="arv-agenda-options">{criteria.map(criterion => <label key={criterion.id}><input type="checkbox" checked={agenda.includes(criterion.id)} onChange={() => setAgenda(items => items.includes(criterion.id) ? items.filter(id => id !== criterion.id) : [...items, criterion.id])}/><span>{criterion.nextQuestion}</span></label>)}</div></div><button className="aip-primary" disabled={!slot} onClick={() => setComplete(true)}>Подготовить приглашение<ArrowUpRight size={15}/></button></> : <><div className="aip-reasons">{['Не подходит опыт', 'Не совпали условия', 'Выбрали другого кандидата'].map(value => <button key={value} aria-pressed={reason === value} onClick={() => setReason(value)}>{value}</button>)}</div><textarea aria-label="Причина отказа" placeholder="Коротко поясните решение" value={reason} onChange={event => setReason(event.target.value)}/><button className="aip-primary" disabled={!reason.trim()} onClick={() => setComplete(true)}>Подготовить отказ</button></>}</Dialog.Content></Dialog.Portal></Dialog.Root>
  </article>;
}
