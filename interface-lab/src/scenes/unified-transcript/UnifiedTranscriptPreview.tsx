import { useCallback, useEffect, useId, useRef, useState, type MouseEvent } from 'react';
import { AnimatePresence, LayoutGroup, MotionConfig, motion, useReducedMotion } from 'motion/react';
import { Collapsible, Tabs, Tooltip } from 'radix-ui';
import { AlignLeft, ArrowUp, Check, ChevronDown, MessageSquare, PanelRightClose, PanelRightOpen, Play, RotateCcw, Search, Sparkles } from 'lucide-react';
import { CandidateInterviewPlayer } from '../interview-review/CandidateInterviewPlayer';
import { InterviewChatAnswer } from '../interview-review/InterviewChatAnswer';
import { interviewTime as time } from '../interview-review/candidate-player-model';
import type { InterviewAnswer } from '../interview-review/interview-assistant-api';
import { EmployerResumeDialog } from '../employer-workspace/EmployerResumeDialog';
import '../employer-workspace/employer-candidate-profile.css';
import './unified-transcript.css';

// Local presentation fixture, intentionally separate from candidate records and AI APIs.
const sections = [
  { title: 'Опыт и работа с клиентами', start: 0, turns: [
    { start: 0, speaker: 'Рекрутер', text: 'Расскажите про ваш опыт в продажах.' },
    { start: 2, speaker: 'Кандидат', text: 'Работала с постоянными клиентами: вела переговоры, готовила предложения и сопровождала сделки. Отвечала за весь цикл — от первого обращения до подписания договора.' },
  ] },
  { title: 'Работа с возражениями', start: 6, turns: [
    { start: 6, speaker: 'Рекрутер', text: 'Как вы работаете с возражением по цене?' },
    { start: 8, speaker: 'Кандидат', text: 'Сначала уточняю, с чем сравнивают наше предложение. Дальше разбираем задачу клиента, комплектацию и условия. Стараюсь понять, что для него действительно важно, прежде чем обсуждать скидку.' },
  ] },
  { title: 'Мотивация и следующий шаг', start: 12, turns: [
    { start: 12, speaker: 'Рекрутер', text: 'Что для вас важно при выборе следующего места работы?' },
    { start: 15, speaker: 'Кандидат', text: 'Понятная система мотивации, сильный продукт и возможность развиваться в B2B-продажах. На встрече хотела бы подробнее узнать о команде и о том, как распределяются клиенты.' },
  ] },
];
const turns = sections.flatMap(section => section.turns);
const chapters = sections.map(section => ({ title: section.title, seconds: section.start, detail: '', fragment: '', source: time(section.start) }));
const transcript = turns.map((turn, i) => `[${time(turn.start)}-${time(turns[i + 1]?.start ?? 20)}] ${turn.speaker === 'Кандидат' ? 'К' : 'Р'}: ${turn.text}`).join('\n');
const resume = 'Демонстрационное резюме\n\nАнастасия Глухова\nМенеджер по продажам\n\nРабота с постоянными клиентами, переговоры, коммерческие предложения и сопровождение сделок.\n\nДанные используются только для просмотра макета.';
type Tab = 'transcript' | 'assistant' | 'comments';

export function UnifiedTranscriptPreview({ initiallyOpen = false }: { initiallyOpen?: boolean }) {
  const id = useId();
  const reduced = useReducedMotion();
  const root = useRef<HTMLDivElement>(null);
  const media = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const pendingSection = useRef<number | null>(null);
  const pulseTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const demoTimers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const commentInput = useRef<HTMLTextAreaElement>(null);
  const [tab, setTab] = useState<Tab>('transcript');
  const [open, setOpen] = useState(initiallyOpen);
  const [current, setCurrent] = useState(0);
  const [reading, setReading] = useState(0);
  const [follow, setFollow] = useState(true);
  const [pulse, setPulse] = useState<number | null>(null);
  const [sideHidden, setSideHidden] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [demo, setDemo] = useState(false);
  const [query, setQuery] = useState('');
  const [matches, setMatches] = useState<number[] | null>(null);
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState<{ question: string; answer: InterviewAnswer } | null>(null);
  const [note, setNote] = useState('');
  const [comments, setComments] = useState<{ seconds: number; text: string }[]>([]);
  const activeTurn = turns.reduce((found, turn, i) => turn.start <= current ? i : found, 0);
  const chapterAt = sections.reduce((found, section, i) => section.start <= current ? i : found, 0);
  const transition = { duration: reduced ? 0 : .24, ease: [0.22, 1, 0.36, 1] as const };

  const scrollTo = useCallback((selector: string, smooth = true) => {
    const container = list.current;
    const row = container?.querySelector<HTMLElement>(selector);
    if (!container || !row) return;
    const offset = selector.includes('data-turn') ? 48 : 0;
    container.scrollTo({ top: Math.max(0, row.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop - offset), behavior: smooth && !reduced ? 'smooth' : 'instant' });
  }, [reduced]);
  const seek = useCallback((seconds: number) => {
    const player = media.current?.querySelector('video');
    if (!player || player.readyState < 1) return;
    player.currentTime = Math.min(seconds, player.duration - .05);
    player.dispatchEvent(new Event('timeupdate', { bubbles: true }));
    setCurrent(seconds);
  }, []);
  const flushSection = useCallback(() => {
    const index = pendingSection.current;
    if (index === null) return;
    pendingSection.current = null;
    scrollTo(`[data-section="${index}"]`);
    setReading(index); setPulse(index);
    requestAnimationFrame(() => list.current?.querySelector<HTMLButtonElement>(`[data-section="${index}"] .uct-chapter-trigger`)?.focus({ preventScroll: true }));
    clearTimeout(pulseTimer.current);
    pulseTimer.current = setTimeout(() => setPulse(null), 1100);
  }, [scrollTo]);
  function cancelDemo() { demoTimers.current.forEach(clearTimeout); demoTimers.current = []; setDemo(false); }
  function chooseSection(index: number) {
    pendingSection.current = index;
    setFollow(false); seek(sections[index].start); setOpen(false);
    if (!open || reduced) requestAnimationFrame(flushSection);
  }
  function previewTransition() {
    cancelDemo(); setDemo(true); setSideHidden(false); setTab('transcript'); setMatches(null); setQuery('');
    setFollow(false); seek(0); setReading(0); scrollTo('[data-section="0"]', false); setOpen(true);
    demoTimers.current.push(setTimeout(() => {
      pendingSection.current = 2; seek(12); setOpen(false);
      if (reduced) requestAnimationFrame(flushSection);
    }, 1300), setTimeout(() => setDemo(false), 3000));
  }
  function reset() {
    cancelDemo(); clearTimeout(pulseTimer.current); pendingSection.current = null;
    setOpen(initiallyOpen); setTab('transcript'); setCurrent(0); setReading(0); setPulse(null); setFollow(true);
    setQuery(''); setMatches(null); setAnswer(null); setQuestion(''); setComments([]); setNote(''); setSideHidden(false);
    media.current?.querySelector('video')?.pause(); seek(0); scrollTo('[data-section="0"]', false);
  }
  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) { void document.exitFullscreen(); setFullscreen(false); }
    else { setFullscreen(true); void root.current?.requestFullscreen?.().catch(() => {}); }
  }, []);
  useEffect(() => {
    const changed = () => setFullscreen(document.fullscreenElement === root.current);
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') { setOpen(false); if (!document.fullscreenElement) setFullscreen(false); } };
    document.addEventListener('fullscreenchange', changed); document.addEventListener('keydown', key);
    return () => { document.removeEventListener('fullscreenchange', changed); document.removeEventListener('keydown', key); };
  }, []);
  useEffect(() => () => { demoTimers.current.forEach(clearTimeout); clearTimeout(pulseTimer.current); }, []);
  useEffect(() => {
    const container = list.current;
    if (!container) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.height > 0) container.style.setProperty('--uct-list-height', `${entry.contentRect.height}px`);
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (follow && !open && tab === 'transcript') {
      setReading(chapterAt);
      scrollTo(turns[activeTurn].start === sections[chapterAt].start ? `[data-section="${chapterAt}"]` : `[data-turn="${activeTurn}"]`);
    }
  }, [activeTurn, chapterAt, follow, open, tab, scrollTo]);
  function interceptPlayer(event: MouseEvent<HTMLDivElement>) {
    const label = (event.target as HTMLElement).closest('button')?.getAttribute('aria-label');
    if (!label || !['На весь экран', 'Выйти из полноэкранного режима', 'Режим кинотеатра', 'Написать комментарий'].includes(label)) return;
    event.preventDefault(); event.stopPropagation(); cancelDemo();
    if (label.includes('экран')) toggleFullscreen();
    else if (label === 'Режим кинотеатра') setSideHidden(value => !value);
    else { setSideHidden(false); setTab('comments'); requestAnimationFrame(() => commentInput.current?.focus()); }
  }
  function search() {
    const normalized = query.toLowerCase().trim();
    const result = turns.flatMap((turn, index) => turn.text.toLowerCase().includes(normalized)
      || (/цен|возраж|скид/.test(normalized) && index === 3)
      || (/мотивац|команд/.test(normalized) && index === 5) ? [index] : []);
    setMatches(result); setOpen(false); setFollow(false);
    if (result.length) requestAnimationFrame(() => scrollTo(`[data-turn="${result[0]}"]`));
  }
  function ask() {
    if (!question.trim()) return;
    const source = /мотивац|важно|команд/.test(question.toLowerCase()) ? 5 : /цен|возраж|скид/.test(question.toLowerCase()) ? 3 : 1;
    const turn = turns[source];
    setAnswer({ question, answer: { mode: 'chat', text: turn.text, parts: [{ text: turn.text, sourceIds: ['preview-source'] }],
      evidence: [{ id: 'preview-source', quote: turn.text, startMs: turn.start * 1000, speakerName: turn.speaker }], supported: true,
      coverage: { selected: turns.length, omitted: 0 }, model: 'local-preview', version: 'preview' } });
    setQuestion('');
  }

  return <MotionConfig reducedMotion="user"><Tooltip.Provider delayDuration={650}><div className={`uct-preview${fullscreen ? ' uct-fullscreen' : ''}`} ref={root}>
    <div className="uct-labbar"><span>Макет · Главы в расшифровке</span><small>Демонстрационные данные</small><button type="button" onClick={previewTransition} disabled={demo}><Play size={13}/>{demo ? 'Переходим к главе…' : 'Показать переход'}</button><button type="button" aria-label="Сбросить макет" onClick={reset}><RotateCcw size={14}/></button></div>
    <main className={`uct-workspace${sideHidden ? ' uct-side-hidden' : ''}`}>
      <div className="uct-player" ref={media} onTimeUpdateCapture={event => { if (event.target instanceof HTMLVideoElement) setCurrent(event.target.currentTime); }} onClickCapture={interceptPlayer}
        onDoubleClickCapture={event => { if (event.target instanceof HTMLVideoElement) { event.stopPropagation(); toggleFullscreen(); } }}>
        <CandidateInterviewPlayer sourceId="unified-transcript-preview" name="Анастасия Глухова" kind="screening"
          media={{ src: '/viewport-preview/candidate-interview.mp4', localPreview: true }} chapters={chapters} transcriptText={transcript}
          canComment comments={comments.map((comment, i) => ({ ...comment, id: `preview-${i}`, createdAt: '2026-10-25T12:00:00Z' }))}
          header={<div className="ecp-video-identity"><h2>Анастасия Глухова</h2><EmployerResumeDialog text={resume}/></div>}/>
        {sideHidden && <button type="button" className="uct-restore" aria-label="Показать материалы интервью" onClick={() => setSideHidden(false)}><PanelRightOpen size={19}/></button>}
      </div>
      {!sideHidden && <Tabs.Root className="uct-materials" value={tab} onValueChange={value => { cancelDemo(); setTab(value as Tab); }}>
        <header className="uct-tabs-header"><Tabs.List className="uct-tabs" aria-label="Материалы интервью">
          {[{ value: 'transcript', title: 'Расшифровка', icon: AlignLeft }, { value: 'assistant', title: 'Спросить ИИ', icon: Sparkles }, { value: 'comments', title: 'Комментарии', icon: MessageSquare }].map(({ value, title, icon: Icon }) => <Tooltip.Root key={value}><Tooltip.Trigger asChild><Tabs.Trigger value={value} aria-label={title}><Icon size={19}/>{value === 'comments' && comments.length > 0 && <i/>}</Tabs.Trigger></Tooltip.Trigger><Tooltip.Portal container={root.current}><Tooltip.Content className="iv-tooltip" side="bottom" sideOffset={9}>{title}</Tooltip.Content></Tooltip.Portal></Tooltip.Root>)}
        </Tabs.List><button type="button" className="uct-icon" aria-label="Скрыть боковую панель" onClick={() => { cancelDemo(); setSideHidden(true); }}><PanelRightClose size={19}/></button></header>
        <Tabs.Content value="transcript" className="uct-transcript-tab" forceMount hidden={tab !== 'transcript'}>
          <div className="uct-transcript-scroll" ref={list} onWheel={() => { cancelDemo(); setFollow(false); setOpen(false); }} onTouchMove={() => { cancelDemo(); setFollow(false); setOpen(false); }} onScroll={() => {
            const container = list.current; if (!container || open) return;
            let index = 0; container.querySelectorAll<HTMLElement>('[data-section]').forEach(section => { if (section.getBoundingClientRect().top - container.getBoundingClientRect().top <= 75) index = Number(section.dataset.section); });
            if (container.scrollHeight > container.clientHeight + 2 && container.scrollTop + container.clientHeight >= container.scrollHeight - 2) index = sections.length - 1;
            setReading(index);
          }}>
            {sections.map((section, sectionIndex) => <section key={section.start} data-section={sectionIndex} className={`uct-section${pulse === sectionIndex ? ' uct-section-pulse' : ''}`}>
              <Collapsible.Root open={open && reading === sectionIndex} onOpenChange={value => { cancelDemo(); setReading(sectionIndex); setOpen(value); }} className="uct-chapter-nav" data-current={reading === sectionIndex}>
                <h2>{reading === sectionIndex ? <Collapsible.Trigger asChild><button type="button" className="uct-chapter-trigger" aria-label={`Переключить главу: ${section.title}`}><span>{section.title}</span><motion.span className="uct-chevron" animate={{ rotate: open ? 180 : 0 }} transition={transition}><ChevronDown size={13}/></motion.span></button></Collapsible.Trigger> : <span className="uct-chapter-title">{section.title}</span>}</h2>
                <AnimatePresence initial={false} onExitComplete={flushSection}>{open && reading === sectionIndex && <Collapsible.Content forceMount asChild><motion.nav className="uct-outline" aria-label="Выбрать главу" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={transition}><LayoutGroup id={`${id}-${sectionIndex}`}>{sections.filter(other => other !== section).map(other => { const index = sections.indexOf(other); return <button type="button" key={other.start} aria-label={`Перейти к главе: ${other.title}`} onClick={() => { cancelDemo(); chooseSection(index); }}><span>{other.title}</span></button>; })}</LayoutGroup></motion.nav></Collapsible.Content>}</AnimatePresence>
              </Collapsible.Root>
              {section.turns.map(turn => { const index = turns.indexOf(turn); return <button type="button" key={turn.start} data-turn={index} className={`uct-turn${index === activeTurn ? ' uct-turn-current' : ''}${matches?.includes(index) ? ' uct-turn-match' : ''}`} aria-label={`${time(turn.start)} · ${turn.speaker}: ${turn.text}`} onClick={() => { cancelDemo(); seek(turn.start); setFollow(false); }}><time>{time(turn.start)}</time><span><small>{turn.speaker}</small>{turn.text}</span></button>; })}
            </section>)}
          </div>
          <div className="uct-transcript-footer">
            {!follow && <button className="uct-follow" type="button" onClick={() => { cancelDemo(); setFollow(true); setOpen(false); setReading(chapterAt); scrollTo(`[data-turn="${activeTurn}"]`); }}>К текущему моменту · {time(current)}</button>}
            {matches && <div className="uct-search-state" role="status"><span>{matches.length ? `Найдено фрагментов: ${matches.length}` : 'Ничего не найдено'}</span><button type="button" onClick={() => { setMatches(null); setQuery(''); }}>Сбросить</button></div>}
            <form className="uct-composer" onSubmit={event => { event.preventDefault(); search(); }}><Search size={18}/><input aria-label="Поиск по смыслу в расшифровке" placeholder="Найти по смыслу в расшифровке…" value={query} onChange={event => setQuery(event.target.value)}/><button type="submit" aria-label="Найти фрагменты" disabled={query.trim().length < 2}><ArrowUp size={18}/></button></form>
          </div>
        </Tabs.Content>
        <Tabs.Content value="assistant" className="uct-other-tab" forceMount hidden={tab !== 'assistant'}><div className="uct-chat-body">{answer ? <><p className="uct-question">{answer.question}</p><InterviewChatAnswer answer={answer.answer} seek={seek} canSeek portal={root.current}/></> : <><span className="uct-empty-icon"><Sparkles size={24}/></span><h2>Что хотите узнать?</h2><p>Спросите об опыте, мотивации или подходе к работе.</p><button className="uct-suggestion" type="button" onClick={() => setQuestion('Как работает с возражениями?')}>Как работает с возражениями?</button></>}</div><form className="uct-composer" onSubmit={event => { event.preventDefault(); ask(); }}><Sparkles size={18}/><input aria-label="Спросить ИИ об интервью" placeholder="Спросите ИИ об интервью…" value={question} onChange={event => setQuestion(event.target.value)}/><button type="submit" aria-label="Отправить вопрос ИИ" disabled={!question.trim()}><ArrowUp size={18}/></button></form></Tabs.Content>
        <Tabs.Content value="comments" className="uct-other-tab" forceMount hidden={tab !== 'comments'}><div className="uct-comments-body">{comments.length ? comments.map((comment, i) => <article key={i}><button type="button" onClick={() => seek(comment.seconds)}>{time(comment.seconds)}</button><p>{comment.text}</p></article>) : <><span className="uct-empty-icon"><MessageSquare size={24}/></span><h2>Отметьте важное</h2><p>Комментарий сохранится у текущего момента записи.</p></>}</div><form className="uct-note-form" onSubmit={event => { event.preventDefault(); if (!note.trim()) return; setComments(rows => [...rows, { seconds: current, text: note.trim() }]); setNote(''); }}><label htmlFor={`${id}-note`}>Комментарий к {time(current)}</label><textarea id={`${id}-note`} ref={commentInput} placeholder="Что хочется отметить?" value={note} onChange={event => setNote(event.target.value)}/><button type="submit" disabled={!note.trim()}><Check size={15}/>Сохранить</button></form></Tabs.Content>
      </Tabs.Root>}
    </main>
  </div></Tooltip.Provider></MotionConfig>;
}
