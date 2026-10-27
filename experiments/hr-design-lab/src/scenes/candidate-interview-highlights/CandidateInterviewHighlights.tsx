import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUpRight, BriefcaseBusiness, Check, ChevronRight, Clock3, ExternalLink, FileText, Film, MapPin, MessageSquareQuote, Pause, Play, RotateCcw, Sparkles, X } from 'lucide-react';
import { categories, formatTime, highlights, mediaBase, type Highlight } from './data';
import './highlights.css';

type Watch = { index: number; full: boolean; playlist: boolean; autoplay: boolean };

export function CandidateInterviewHighlights() {
  const [category, setCategory] = useState<string>('Все ответы');
  const [watch, setWatch] = useState<Watch | null>(null);
  const [skill, setSkill] = useState<string | null>(null);
  const [sourceOpen, setSourceOpen] = useState(false);
  const reduce = useReducedMotion();
  const skillRef = useRef<HTMLElement>(null);
  const visible = highlights.filter(h => category === 'Все ответы' || h.category === category);

  function openSkill(id: string) {
    setSkill(id);
    requestAnimationFrame(() => skillRef.current?.scrollIntoView({ behavior: reduce ? 'instant' : 'smooth', block: 'center' }));
  }

  return <div className="ih-lab">
    <div className="ih-labbar">
      <a href="/?path=/story/hr-vision-candidate-research--gallery"><ArrowLeft size={14} /> К референсам</a>
      <span>Макет · вымышленный профиль · видео без звука</span>
      <button onClick={() => setSourceOpen(v => !v)} aria-expanded={sourceOpen} aria-controls="ih-reference">Идея BrightHire <ArrowUpRight size={14} /></button>
    </div>
    {sourceOpen && <aside className="ih-source" id="ih-reference">
      <strong>Интервью как набор конкретных ответов.</strong>
      <p>У BrightHire берём тематические фрагменты и возврат к контексту. Добавляем связь «ответ → навык → описание уровня». В этой сцене все цитаты и уровни придуманы для проверки интерфейса. Видео — 32-секундный аниматик, а не запись речи.</p>
      <a href="https://brighthire.com/blog/share-great-interview-moments-with-brighthire-clips/" target="_blank" rel="noreferrer">Как работают Clips у BrightHire <ExternalLink size={14} /></a>
    </aside>}
    <div className="ih-product">
      <header className="ih-topbar"><span className="ih-brand">HR Vision<span className="ih-brand-mark" aria-hidden="true">↗</span></span><span>Знакомство начинается с человека</span><span className="ih-preview-label">Предпросмотр профиля</span></header>
      <main>
        <section className="ih-person" aria-labelledby="ih-name">
          <div className="ih-avatar" aria-hidden="true">АМ<span><Check size={13} strokeWidth={3}/></span></div>
          <div className="ih-person-copy"><p className="ih-eyebrow">ПРОФИЛЬ КАНДИДАТА</p><h1 id="ih-name">Анна Морозова</h1><p className="ih-role">Customer Success Manager <span>· B2B SaaS</span></p></div>
          <dl className="ih-facts"><div><dt><MapPin size={15}/>Город</dt><dd>Москва</dd></div><div><dt><BriefcaseBusiness size={15}/>Опыт</dt><dd>4 года</dd></div><div><dt><Film size={15}/>HR-интервью</dt><dd className="ih-good">Пройдено <Check size={14}/></dd></div></dl>
        </section>
        <nav className="ih-profile-nav" aria-label="Разделы профиля"><a className="ih-selected" href="#ih-interview"><Film size={16}/>Интервью <span>4 ответа</span></a><a href="#ih-skills">Навыки <span>4</span></a><a href="#ih-about">О кандидате</a></nav>

        <section className="ih-interview" id="ih-interview" aria-labelledby="ih-highlights-title">
          <div className="ih-section-head"><div><p className="ih-eyebrow"><span className="ih-status-dot"/>ЗНАКОМСТВО С АННОЙ</p><h2 id="ih-highlights-title">Главное из интервью</h2><p>Как работает с клиентами, задаёт вопросы и решает сложные ситуации.<br className="ih-desktop"/> Выберите ответ — или посмотрите их по порядку.</p></div>
            <button className="ih-primary" onClick={() => setWatch({index:0,full:false,playlist:true,autoplay:true})}><Play size={16} fill="currentColor"/>Смотреть подборку<span>0:32</span></button>
          </div>
          <div className="ih-filter-row"><div role="group" aria-label="Темы интервью">{categories.map(c => <button key={c} onClick={() => setCategory(c)} aria-pressed={category === c}>{c}</button>)}</div><span><Clock3 size={14}/>4 коротких ответа</span></div>
          <motion.div layout={!reduce} className="ih-cards">
            <AnimatePresence mode="popLayout" initial={false}>{visible.map(h => <motion.article key={h.id} layout={!reduce} initial={{opacity:0,y:12}} animate={{opacity:1,y:0}} exit={{opacity:0}} transition={{duration:reduce?0:.22}} className={`ih-card ih-${h.tone}`}>
              <button className="ih-card-watch" onClick={() => setWatch({index:highlights.indexOf(h),full:false,playlist:false,autoplay:true})} aria-label={`Смотреть: ${h.title}`}>
                <div className="ih-filmstill"><div className="ih-film-grain"/><span className="ih-shot-label">АННА · HR-ИНТЕРВЬЮ</span><div className="ih-shot-avatar" aria-hidden="true">АМ</div><span className="ih-shot-demo">Демонстрация</span><span className="ih-play"><Play size={17} fill="currentColor"/></span><span className="ih-duration">{formatTime(h.end-h.start)}</span></div>
                <div className="ih-card-copy"><p className="ih-card-topic">{h.category}</p><h3>{h.title}</h3><p>{h.summary}</p></div>
              </button>
              <button className="ih-skill-link" onClick={() => openSkill(h.id)}><span><span className="ih-mini-bars" aria-hidden="true">▮▮▮<i>▮▮</i></span>{h.skill}</span><ChevronRight size={15}/></button>
            </motion.article>)}</AnimatePresence>
          </motion.div>
          <div className="ih-full-recording"><div className="ih-recording-icon"><FileText size={19}/></div><div><strong>Весь разговор, без сокращений</strong><p>Вопросы, ответы и то, что было между ними.</p></div><button onClick={() => setWatch({index:0,full:true,playlist:false,autoplay:false})}>Открыть интервью <span>0:32</span><ArrowUpRight size={17}/></button></div>
        </section>

        <section className="ih-skills" id="ih-skills" ref={skillRef} aria-labelledby="ih-skills-title">
          <div className="ih-small-heading"><div><p className="ih-eyebrow">ОТВЕТЫ И НАВЫКИ</p><h2 id="ih-skills-title">За каждым уровнем — конкретный пример</h2></div><span className="ih-example-badge">Пример отображения оценки</span></div>
          <div className="ih-skill-grid">{highlights.map(h => <div key={h.id} className={`ih-skill-item ${skill === h.id?'ih-skill-open':''}`}>
            <button aria-expanded={skill === h.id} aria-controls={`ih-evidence-${h.id}`} onClick={() => setSkill(skill === h.id?null:h.id)}><span>{h.skill}<small>Самостоятельное выполнение</small></span><span className="ih-level">{h.level}<small>/5</small></span><ArrowDown size={17}/></button>
            {skill === h.id && <div className="ih-evidence" id={`ih-evidence-${h.id}`}><p>{h.behaviour}</p><blockquote>«{h.quote}»</blockquote><button onClick={() => setWatch({index:highlights.indexOf(h),full:false,playlist:false,autoplay:true})}><Play size={14}/>Открыть ответ <span>{formatTime(h.start)}–{formatTime(h.end)}</span></button><small>Иллюстрация связи с ответом. Это не результат оценки реального кандидата.</small></div>}
          </div>)}</div>
        </section>
        <section className="ih-about" id="ih-about"><p className="ih-eyebrow">НЕМНОГО О СЕБЕ</p><p>«Мне нравится разбираться, почему продукт не прижился у клиента, и помогать команде увидеть результат. Ищу роль, где можно влиять на весь клиентский путь».</p><span>Анна Морозова</span></section>
      </main>
      <footer className="ih-footer"><span>HR Vision</span><p>Профиль, в котором можно услышать человека.</p></footer>
    </div>
    {watch && <InterviewDialog watch={watch} onChange={setWatch} onClose={() => setWatch(null)}/>}
  </div>;
}

function InterviewDialog({watch,onChange,onClose}:{watch:Watch;onChange:(watch:Watch)=>void;onClose:()=>void}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const pendingStart = useRef(true);
  const endedClip = useRef(false);
  const closeButton = useRef<HTMLButtonElement>(null);
  const [playing,setPlaying] = useState(false);
  const [position,setPosition] = useState<number>(highlights[watch.index].start);
  const [error,setError] = useState('');
  const [pane,setPane] = useState<'answer'|'skill'>('answer');
  const [speed,setSpeed] = useState(1);
  const h = highlights[watch.index];
  const start = watch.full?0:h.start;
  const end = watch.full?32:h.end;
  const activeH: Highlight = highlights.find(item => position >= item.start && position < item.end) || highlights[3];
  const context = watch.full?activeH:h;

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const element = dialog.current;
    element?.showModal();
    closeButton.current?.focus();
    return () => {element?.close();opener?.focus();};
  },[]);

  function play() {
    const el = video.current;
    if (!el) return;
    setError('');
    void el.play().catch(() => setError('Не удалось начать воспроизведение. Нажмите «Воспроизвести» ещё раз.'));
  }
  function initialize() {
    if (!pendingStart.current || !video.current || video.current.readyState < 1) return;
    pendingStart.current = false;
    endedClip.current = false;
    video.current.currentTime = h.start;
    video.current.playbackRate = speed;
    setPosition(h.start);
    if(watch.autoplay) play();
  }
  useEffect(() => {
    pendingStart.current = true;
    endedClip.current = false;
    setError('');
    initialize();
    // Each selected answer starts at its own source timestamp.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[watch.index]);

  function choose(index:number) {
    if(index === watch.index) {
      endedClip.current = false;
      if(video.current) video.current.currentTime = highlights[index].start;
      setPosition(highlights[index].start);
      play();
    } else onChange({...watch,index,autoplay:true});
  }
  function finish() {
    if(endedClip.current) return;
    endedClip.current = true;
    video.current?.pause();
    if(video.current) video.current.currentTime = Math.min(end,video.current.duration || end);
    setPosition(end);
    if(watch.playlist && !watch.full && watch.index < highlights.length-1) onChange({...watch,index:watch.index+1,autoplay:true});
  }
  function update() {
    const time = video.current?.currentTime ?? 0;
    setPosition(time);
    if(!watch.full && time >= end-.04) finish();
  }
  function togglePlay() {
    const el=video.current;
    if(!el) return;
    if(!el.paused) {el.pause();return;}
    if(el.currentTime >= end-.1 || el.currentTime < start) {
      endedClip.current=false;el.currentTime=start;setPosition(start);
    }
    play();
  }
  function seek(value:number) {
    endedClip.current=false;
    const time=Math.min(end,Math.max(start,value));
    if(video.current) video.current.currentTime=time;
    setPosition(time);
  }

  return <dialog className="ih-dialog" ref={dialog} aria-labelledby="ih-player-title" onCancel={e=>{e.preventDefault();onClose();}} onClick={e=>{if(e.target===e.currentTarget) onClose();}}>
    <header><div><span className="ih-dialog-person">АМ</span><div><p>АННА МОРОЗОВА · HR-ИНТЕРВЬЮ</p><h2 id="ih-player-title">{watch.full?'Полный разговор':h.title}</h2></div></div><button ref={closeButton} className="ih-icon" onClick={onClose} aria-label="Закрыть интервью"><X size={21}/></button></header>
    <div className="ih-watch-layout"><div className="ih-player-column">
      <div className="ih-video-stage"><video ref={video} src={`${mediaBase}/sample-interview.mp4`} poster={`${mediaBase}/sample-interview.jpg`} preload="metadata" playsInline muted onLoadedMetadata={initialize} onTimeUpdate={update} onPlay={()=>setPlaying(true)} onPause={()=>setPlaying(false)} onEnded={finish} onError={()=>setError('Видео не загрузилось. Текст ответов доступен справа; попробуйте загрузить запись снова.')} aria-label="Демонстрационное интервью, аниматик без звука"/>
        {!playing && !error && <button className="ih-stage-play" aria-label="Воспроизвести видео" onClick={togglePlay}><Play fill="currentColor" size={25}/></button>}
        <span className="ih-video-note">Демо · без звука</span>
      </div>
      <div className="ih-player-controls"><button className="ih-icon" onClick={togglePlay} aria-label={playing?'Пауза':'Воспроизвести'}>{playing?<Pause size={18}/>:<Play size={18}/>}</button><span className="ih-time">{formatTime(Math.max(0,position-start))} / {formatTime(end-start)}</span><label className="ih-sr-only" htmlFor="ih-seek">Позиция воспроизведения</label><input id="ih-seek" type="range" min={start} max={end} step={.05} value={Math.min(end,Math.max(start,position))} onChange={e=>seek(Number(e.target.value))}/><label className="ih-sr-only" htmlFor="ih-speed">Скорость</label><select id="ih-speed" value={speed} onChange={e=>{const next=Number(e.target.value);setSpeed(next);if(video.current)video.current.playbackRate=next;}}><option value={1}>1×</option><option value={1.25}>1.25×</option><option value={1.5}>1.5×</option><option value={2}>2×</option></select></div>
      {error && <div className="ih-media-error" role="alert">{error}<button onClick={()=>{setError('');pendingStart.current=true;video.current?.load();}}><RotateCcw size={15}/>Повторить</button></div>}
      <div className="ih-play-mode"><span>{watch.full?'Все вопросы и ответы':`Фрагмент ${formatTime(h.start)}–${formatTime(h.end)}`}</span>{!watch.full?<button onClick={()=>{endedClip.current=false;onChange({...watch,full:true,playlist:false});}}>Контекст в полном интервью <ArrowUpRight size={15}/></button>:<button onClick={()=>{video.current?.pause();endedClip.current=false;const index=highlights.indexOf(activeH);onChange({index,full:false,playlist:false,autoplay:false});if(video.current)video.current.currentTime=highlights[index].start;setPosition(highlights[index].start);}}>Вернуться к фрагменту <ArrowLeft size={15}/></button>}</div>
      <div className="ih-chapter-header"><h3>{watch.full?'Темы разговора':'Все фрагменты'}</h3>{!watch.full && <label><input type="checkbox" checked={watch.playlist} onChange={e=>onChange({...watch,playlist:e.target.checked})}/>Играть по порядку</label>}</div>
      <div className="ih-chapters">{highlights.map((clip,index)=><button key={clip.id} aria-current={(watch.full?context.id===clip.id:watch.index===index)?'true':undefined} onClick={()=>choose(index)}><span className={`ih-chapter-art ih-${clip.tone}`}><Play size={14}/></span><span><strong>{clip.title}</strong><small>{formatTime(clip.start)}–{formatTime(clip.end)}</small></span>{(watch.full?context.id===clip.id:watch.index===index)&&<span className="ih-now-playing">{playing?'Играет':'Выбрано'}</span>}</button>)}</div>
    </div>
    <aside className="ih-context"><div className="ih-context-tabs" role="group" aria-label="Подробности ответа"><button aria-pressed={pane==='answer'} onClick={()=>setPane('answer')}><MessageSquareQuote size={15}/>Ответ</button><button aria-pressed={pane==='skill'} onClick={()=>setPane('skill')}><Sparkles size={15}/>Связанный навык</button></div>
      {pane==='answer'?<div className="ih-answer"><span className="ih-eyebrow">ВОПРОС ИНТЕРВЬЮЕРА</span><h3>{context.question}</h3><button className="ih-quote-time" onClick={()=>choose(highlights.indexOf(context))}><Play size={12}/>{formatTime(context.start)}<span>Анна</span></button><blockquote>{context.quote}</blockquote><div className="ih-followup"><span className="ih-eyebrow">СЛЕДОМ УТОЧНЯЕМ</span><p>{context.followup}</p><span>Пример продолжения вопроса</span></div></div>:<div className="ih-answer"><span className="ih-eyebrow">{context.skill}</span><div className="ih-assessment-level"><strong>{context.level}<small>/5</small></strong><span>Самостоятельное<br/>выполнение</span></div><p className="ih-behaviour">{context.behaviour}</p><h3 className="ih-why">Что в ответе указывает на этот уровень</h3><p>{context.why}</p><button className="ih-inline-action" onClick={()=>setPane('answer')}>Вернуться к словам Анны <ArrowRight size={15}/></button></div>}
      <p className="ih-fixture-note">Учебный пример: цитаты и оценки вымышлены. Видео показывает работу плеера, а не подтверждает эти слова.</p>
    </aside></div>
  </dialog>;
}
