import React, { useEffect, useRef, useState, type ReactNode } from 'react';
import { motion, AnimatePresence, MotionConfig, useScroll, useTransform, useReducedMotion, useMotionValue, useMotionValueEvent, useInView, type MotionValue } from 'motion/react';
import { ArrowRight, ArrowUpRight, Check, Plus, X, CalendarDays, Clock3, Send, Play, Pause, Mic, Video, Phone, BriefcaseBusiness, MessageCircle, CheckCheck, Network, Volume2, CircleUserRound } from 'lucide-react';
import { Button } from '../../src/components/ui/button';
import { employerEntryUrl } from '../../src/lib/employer-entry';
import './style.css';
import { InterviewChapter } from './InterviewChapter';
import { FeedbackChapter } from './FeedbackChapter';
import { InterestChapter } from './InterestChapter';
import { SiteCountdownButton } from './SiteCountdownButton';
import { PersonPortrait } from './PersonPortrait';
import { DemoFunnel } from './DemoFunnel';
import { deriveRequirements, getDemoMatches } from './matching-demo';
import { candidates, type Candidate } from './candidate-data';
import { interviewChapters } from './interview-data';
export { interviewChapters } from './interview-data';

import { LOGO_URL, PLAY_URL } from './assets';
import './landing-refinements.css';
import './ux-polish.css';
import './employer-entry.css';
const initialNeed = 'Вернуть ушедших клиентов и навести порядок в B2B-продажах.';
const spring = { type: 'spring' as const, stiffness: 170, damping: 24 };

export type SceneControl = {
  progress: MotionValue<number>; playing: boolean;
  seek: (value: number) => void; play: () => void; pause: () => void; toggle: () => void;
};

function Avatar({ person = candidates[0], size = '', icon = false }: { person?: Candidate; size?: string; icon?: boolean }) {
  return <span className={`avatar ${person.color} ${size}`} aria-hidden="true">{icon ? <CircleUserRound /> : person.initials}</span>;
}
function Action({ children, onClick, secondary = false, className = '', type = 'button', disabled = false }: { children: ReactNode; onClick?: () => void; secondary?: boolean; className?: string; type?: 'button' | 'submit'; disabled?: boolean }) {
  return <Button type={type} className={`story-button ${secondary ? 'secondary' : ''} ${className}`} onClick={onClick} disabled={disabled}>{children}</Button>;
}
function jump(id: string) { document.getElementById(id)?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' }); }
function SectionTitle({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`section-title ${className}`}><h2>{children}</h2></div>;
}
function Chapter({ id, className = '', children }: { id: string; className?: string; children: (progress: MotionValue<number>) => ReactNode }) {
  const ref = useRef<HTMLElement>(null);
  const { scrollYProgress } = useScroll({ target: ref, offset: ['start start', 'end end'] });
  // Keep every property on the same progress clock. Safari's native timeline
  // can otherwise interpolate opacity differently from transform keyframes.
  const sceneProgress = useMotionValue(0);
  useMotionValueEvent(scrollYProgress, 'change', value => sceneProgress.set(value));
  const reduced = useReducedMotion();
  const staticProgress = useMotionValue(.74);
  return <section ref={ref} id={id} className={`chapter ${className}`}><div className="chapter-sticky">{children(reduced ? staticProgress : sceneProgress)}</div></section>;
}

function Intro({ progress }: { progress: MotionValue<number> }) {
  const firstOpacity = useTransform(progress, [0, .12, .29], [1, 1, 0]);
  const firstY = useTransform(progress, [.12, .32], [0, -48]);
  const secondOpacity = useTransform(progress, [.19, .36], [0, 1]);
  const secondY = useTransform(progress, [.19, .36], [45, 0]);
  const lineScale = useTransform(progress, [.03, .15], [0, 1]);
  const haloScale = useTransform(progress, [0, .5, 1], [.7, 1.18, 2]);
  const haloOpacity = useTransform(progress, [0, .48, 1], [.22, .55, .1]);
  return <>
    <motion.div className="intro-orbit" style={{ scale: haloScale, opacity: haloOpacity }} aria-hidden="true"><div/><div/><div/></motion.div>
    <motion.div className="intro-message first" style={{ opacity: firstOpacity, y: firstY }}><h1>Никто не хочет<br/><span className="crossed">нанимать<motion.i style={{ scaleX: lineScale }}/></span><br/>сотрудника.</h1></motion.div>
    <motion.div className="intro-message second" style={{ opacity: secondOpacity, y: secondY }}><h2>Все хотят решить<br/><span>проблему<br className="mobile-only"/> бизнеса.</span></h2></motion.div>


  </>;
}

function Needs({ progress, need, setNeed, onNext, nextLabel = 'Показать кандидатов' }: { progress: MotionValue<number>; need: string; setNeed: (s: string) => void; onNext?: () => void; nextLabel?: string }) {
  const [editing, setEditing] = useState(false);
  const [typed, setTyped] = useState(0);
  const [compact, setCompact] = useState(() => matchMedia('(max-width:600px)').matches);
  const reduced = useReducedMotion();
  useEffect(() => {
    const media = matchMedia('(max-width:600px)');
    const update = () => setCompact(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  useMotionValueEvent(progress, 'change', p => setTyped(Math.round(Math.max(0, Math.min(1, (p + .08) / .42)) * need.length)));
  const shownNeed = editing || reduced || compact ? need : need.slice(0, typed);
  const requirements = deriveRequirements(shownNeed);
  const y = useTransform(progress, [0, .2], [24, 0]);
  return <div className="split-layout needs-layout">
    <SectionTitle>Опишите задачу.<br/><em>Мы найдём, <br/>кто её решит.</em></SectionTitle>
    <motion.div className={`brief-object brief-interactive ${editing ? 'is-editing' : ''}`} style={{ y: editing || compact ? 0 : y }}>
      <label className="brief-label" htmlFor="need">Ваша задача</label>
      <textarea aria-label="Что вы хотите изменить?" id="need" value={shownNeed} placeholder="Например, вернуть ушедших клиентов…" maxLength={280} onFocus={() => setEditing(true)} onChange={e => setNeed(e.target.value)} rows={3}/>
      <div className="need-tags" aria-label="Что важно в опыте кандидата"><AnimatePresence initial={false}>{requirements.map(requirement => <motion.span key={requirement.id} initial={{opacity:0,y:12,scale:.96}} animate={{opacity:1,y:0,scale:1}} exit={{opacity:0,scale:.96}} transition={{type:'spring',bounce:0,duration:.3}}>{requirement.label}</motion.span>)}</AnimatePresence></div>
      <div className="brief-footer"><button className="brief-next" disabled={!need.trim()} onClick={onNext ?? (() => jump('people'))}>{nextLabel}</button></div>
    </motion.div>
  </div>;
}

const matchingNodeIndices = [1, 8, 14];
const graphNodes = Array.from({ length: 42 }, (_, i) => {
  const a = i * 2.3999632;
  const radius = 100 + Math.sqrt(i / 41) * 340;
  const selectedIndex = matchingNodeIndices.indexOf(i);
  const selectedPositions = [[325, 230], [930, 380], [360, 505]];
  return { x: selectedIndex < 0 ? 600 + Math.cos(a) * radius : selectedPositions[selectedIndex][0], y: selectedIndex < 0 ? 325 + Math.sin(a) * radius * .63 : selectedPositions[selectedIndex][1], person: candidates[i % candidates.length], selected: selectedIndex >= 0 };
});
function GraphNode({ i, progress, person, selected }: { i: number; progress: MotionValue<number>; person?: Candidate; selected: boolean }) {
  const n = graphNodes[i];
  const scale = useTransform(progress, [0, .35, .8], [.35, 1, selected ? 1.15 : .85]);
  const opacity = useTransform(progress, [0, .17 + i * .003, .54, .85], [0, .85, .85, selected ? 1 : .24]);
  return <motion.div className={`graph-node ${selected ? 'match' : ''}`} style={{ left: `${n.x / 12}%`, top: `${n.y / 6.5}%`, scale, opacity }}><Avatar person={person ?? n.person}/>{selected && <span className="graph-node-name">{(person ?? n.person).detail}</span>}</motion.div>;
}
function Connections({ progress, need }: { progress: MotionValue<number>; need: string }) {
  const matches = getDemoMatches(need).matches;
  const requirements = deriveRequirements(need);
  const pathProgress = useTransform(progress, [.1, .55], [0, 1]);
  const colorOpacity = useTransform(progress, [.38, .73], [0, 1]);
  const graphScale = useTransform(progress, [0, .5, 1], [1.23, 1, .95]);
  return <>
    <SectionTitle className="center-title">Связываем вашу задачу<br/><em>с опытом людей.</em></SectionTitle>
    <motion.div className="graph-field" style={{ scale: graphScale }}>
      <svg viewBox="0 0 1200 650" aria-hidden="true" className="graph-lines">
        {graphNodes.map((n, i) => <motion.path key={i} d={`M600 325 Q${(600+n.x)/2+80} ${(325+n.y)/2-45} ${n.x} ${n.y}`} fill="none" stroke="#8190b8" strokeOpacity=".22" strokeWidth="1" style={{ pathLength: pathProgress }}/>) }
        {graphNodes.map((n, i) => <motion.path key={`web-${i}`} d={`M${n.x} ${n.y} L${graphNodes[(i+7)%42].x} ${graphNodes[(i+7)%42].y}`} fill="none" stroke="#a6b7c5" strokeOpacity=".18" strokeWidth=".8" style={{ pathLength: pathProgress }}/>) }
        <motion.g style={{ opacity: colorOpacity }}>{graphNodes.filter(n => n.selected).slice(0,matches.length).map((n,i) => <path key={i} d={`M600 325 Q${(600+n.x)/2} ${(325+n.y)/2-60} ${n.x} ${n.y}`} fill="none" stroke={['#487bdf','#729e84','#a18ad9'][i]} strokeWidth="2.4" className="signal-line"/>)}</motion.g>
      </svg>
      {graphNodes.map((n, i) => <GraphNode key={i} i={i} progress={progress} selected={n.selected && matchingNodeIndices.indexOf(i)<matches.length} person={n.selected ? matches[matchingNodeIndices.indexOf(i)]?.person : undefined}/>)}
      <div className="graph-center"><Network size={24}/><strong>{need || initialNeed}</strong></div>
    </motion.div>
    <div className="graph-requirements">{requirements.map(requirement => <span key={requirement.id}>{requirement.label}</span>)}</div>
  </>;
}

function Ticker({ reverse = false, offset = 0, progress }: { reverse?: boolean; offset?: number; progress?: MotionValue<number> }) {
  const fallback = useMotionValue(0);
  const x = useTransform(progress ?? fallback, [0, 1], reverse ? ['-45%', '0%'] : ['0%', '-45%']);
  return <div className={`ticker ${reverse ? 'reverse' : ''}`}><motion.div className="ticker-track" style={progress ? { x, animation: 'none' } : undefined}>{[0,1].map(copy => <div className="ticker-copy" key={copy} aria-hidden={copy===1}>{candidates.map((_, i) => { const p = candidates[(i+offset)%candidates.length]; return <div className="person-token" key={p.name}><PersonPortrait person={p}/><div><strong>{p.name}</strong><span>{p.role}</span></div></div>;})}</div>)}</motion.div></div>;
}
function Pool({ paused, control }: { paused: boolean; control?: SceneControl }) {
  const ref = useRef<HTMLElement>(null);
  const inView = useInView(ref, { amount: .15 });
  const reduced = useReducedMotion();
  const [newPerson, setNewPerson] = useState(8);
  const fallback = useMotionValue(0);
  useMotionValueEvent(control?.progress ?? fallback, 'change', v => { if(control) setNewPerson(Math.min(candidates.length-1, Math.floor(v * candidates.length))); });
  useEffect(() => { if(control || !inView || reduced || paused) return; const timer = setInterval(() => setNewPerson(v => (v+1)%candidates.length), 6500); return () => clearInterval(timer); },[inView,reduced,paused,control]);
  return <section id="people" ref={ref} className={`pool-section ${inView ? 'is-active' : ''}`}>
    <div className="pool-number"><strong>2 000<span>+</span></strong></div>
    <SectionTitle className="center-title">кандидатов.<br/><em>Ваш — среди них.</em></SectionTitle>
    <div className="people-flow"><Ticker progress={control?.progress}/><Ticker reverse offset={3} progress={control?.progress}/><Ticker offset={5} progress={control?.progress}/></div>
    <div className="new-person"><AnimatePresence mode="wait"><motion.div className="pool-arrival" key={newPerson} initial={{ opacity: 0, y: 22, scale: .98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -20, scale: .98 }} transition={{type:'spring',bounce:0,duration:.4}}><PersonPortrait person={candidates[newPerson]}/><div><span className="arrival-label"><span className="status-dot"/>Новый кандидат</span><strong>{candidates[newPerson].name}</strong><span className="arrival-role">{candidates[newPerson].role}</span></div></motion.div></AnimatePresence></div>
  </section>;
}

function InterestedPerson({ i, progress }: { i: number; progress: MotionValue<number> }) {
  const opacity = useTransform(progress, [.05+i*.07,.28+i*.07], [0, 1]);
  const y = useTransform(progress, [.05+i*.07,.28+i*.07], [65, 0]);
  const packetY = useTransform(progress, [.32+i*.06,.54+i*.06], [130, 0]);
  const packetOpacity = useTransform(progress, [.29+i*.06,.4+i*.06,.6+i*.06], [0, 1, 0]);
  const accepted = useTransform(progress, [.53+i*.07,.67+i*.07], [0, 1]);
  return <motion.div className="interested-person" style={{ opacity, y }}><Avatar person={candidates[i]} size="large"/><strong>{candidates[i].name}</strong><span>{candidates[i].role}</span><motion.div className="interest-answer" style={{ opacity: accepted }}><Check size={16}/> Хочу познакомиться</motion.div><motion.div className="vacancy-packet" style={{ y: packetY, opacity: packetOpacity }}><BriefcaseBusiness size={18}/><span>Ваша вакансия</span></motion.div></motion.div>;
}
function Interest({ progress }: { progress: MotionValue<number> }) {
  return <>
    <SectionTitle className="center-title">Зовём тех,<br/><em>кому интересны вы.</em></SectionTitle>
    <div className="interest-stage"><svg className="interest-paths" viewBox="0 0 900 390" aria-hidden="true"><path d="M450 365 C450 215 150 350 150 175 M450 365 V175 M450 365 C450 215 750 350 750 175"/></svg><div className="interested-people">{[0,1,2].map(i=><InterestedPerson key={i} i={i} progress={progress}/>)}</div><div className="vacancy-origin"><BriefcaseBusiness size={20}/> Менеджер по работе с клиентами</div></div>
  </>;
}

function Interview({ control }: { control?: SceneControl } = {}) {
  const [chapter, setChapter] = useState(control ? 0 : 1);
  const [localPlaying, setLocalPlaying] = useState(false);
  const playing = control ? control.playing : localPlaying;
  const setPlaying = (value: boolean | ((v:boolean)=>boolean)) => { const next = typeof value === 'function' ? value(playing) : value; if(control){next ? control.play() : control.pause();} else setLocalPlaying(next); };
  const fallback = useMotionValue(0);
  useMotionValueEvent(control?.progress ?? fallback, 'change', value => { if(control) setPosition(Math.min(31, Math.floor(value * 31))); });
  const [position, setPosition] = useState(control ? 0 : 8);
  useEffect(() => {
    if(control || !playing) return;
    const timer=setInterval(()=>setPosition(v=>{if(v>=31){setPlaying(false);return 0;}return v+1;}),1000);
    return()=>clearInterval(timer);
  },[playing,control]);
  useEffect(()=>setChapter(Math.min(3,Math.floor(position/8))),[position]);
  function choose(i:number){control?.seek(i*8/31);setChapter(i);setPosition(i*8);}
  return <section className="interview-section" id="interview">
    <SectionTitle className="center-title">Сразу интервью.<br/><em>И причины выбора.</em></SectionTitle>
    <motion.div className="profile-window" initial={{opacity:0,y:70}} whileInView={{opacity:1,y:0}} viewport={{once:true,amount:.12}} transition={{duration:.7,ease:[.22,1,.36,1]}}>
      <div className="profile-top"><img src={LOGO_URL} width="150" height="24" alt="HR Vision"/><span>Менеджер по работе с клиентами</span><span className="window-dots"><i/><i/><i/></span></div>
      <div className="profile-body">
        <aside className="profile-sidebar"><span className="selected">Задачи</span><span>Кандидаты</span><span>Встречи</span><span>Офферы</span></aside>
        <div className="profile-main"><div className="profile-heading"><Avatar size="medium"/><div><h3>Анна Миронова</h3><p>Key Account Manager · 5 лет опыта</p></div><span className="interested-label"><span className="status-dot"/> Ей интересна ваша задача</span></div>
          <div className="profile-tabs"><span className="selected">Интервью</span><span>Профиль</span><span>Встречи</span><span>Решение и оффер</span></div>
          <div className="interview-columns"><div><div className="video-heading">Интервью с рекрутером <span>Коротко</span></div><div className={`interview-player ${playing?'playing':''}`}>
              <div className="video-grid"/><Avatar size="video-avatar"/><div className="sound-waves" aria-hidden="true">{Array.from({length:18},(_,i)=><i key={i} style={{'--delay':`${i*.08}s`,'--height':`${12+(i*13)%44}px`} as React.CSSProperties}/>)}</div>
              <button className="play-button" onClick={()=>setPlaying(v=>!v)} aria-label={playing?'Приостановить пример интервью':'Воспроизвести пример интервью'}>{playing?<Pause size={20}/>:<img src={PLAY_URL} width="20" height="20" alt=""/>}</button><span className="video-name">Анна Миронова</span>
            </div><div className="playback"><button onClick={()=>setPlaying(v=>!v)} aria-label={playing?'Пауза':'Воспроизвести'}>{playing?<Pause size={15}/>:<Play size={15}/>}</button><span>{interviewChapters[chapter].time}</span><input aria-label="Позиция примера интервью" type="range" min="0" max="31" value={position} onChange={e=>{const value=Number(e.target.value);control?.seek(value/31);setPosition(value);}}/><Volume2 size={16}/></div>
            <p className="interview-quote" aria-live="polite">«{interviewChapters[chapter].quote}»</p></div>
            <div className="reason-column"><h4>Почему предлагаем Анну</h4><div className="reason-line"><Check/> Возвращала ушедших клиентов</div><div className="reason-line"><Check/> Развивала B2B-продажи</div><div className="reason-line"><Check/> Ей интересна работа с вашей базой</div><div className="chapters">{interviewChapters.map((c,i)=><button key={c.time} className={chapter===i?'selected':''} onClick={()=>choose(i)}><span>{c.title}</span><time>{c.time}</time></button>)}</div></div>
          </div>
        </div>
      </div>
    </motion.div>
  </section>;
}

function Calendar({ progress, control }: { progress: MotionValue<number>; control?: SceneControl }) {
  const [slot,setSlot]=useState(0);
  const [status,setStatus]=useState<'idle'|'sending'|'confirmed'>('idle');
  const [autoPlayed,setAutoPlayed]=useState(false);
  const slots=['12 октября · 11:00','12 октября · 14:00','13 октября · 12:00'];
  const chooseSlot = (index: number) => {control?.seek(.28);setSlot(index);setStatus('idle');setAutoPlayed(true);};
  const onSlotKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next = index;
    if(event.key==='ArrowRight'||event.key==='ArrowDown') next=(index+1)%slots.length;
    else if(event.key==='ArrowLeft'||event.key==='ArrowUp') next=(index+slots.length-1)%slots.length;
    else if(event.key==='Home') next=0;
    else if(event.key==='End') next=slots.length-1;
    else return;
    event.preventDefault();chooseSlot(next);
    event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="radio"]')[next]?.focus();
  };
  useMotionValueEvent(progress,'change',v=>{if(control){setStatus(v < .46 ? 'idle' : v < .76 ? 'sending' : 'confirmed');}else if(v>.68&&!autoPlayed){setAutoPlayed(true);setStatus('sending');}});
  const flightX=useTransform(progress,[.46,.73],[0,140]);
  const flightY=useTransform(progress,[.46,.73],[70,-135]);
  const flightRotate=useTransform(progress,[.46,.73],[-10,12]);
  const flightScale=useTransform(progress,[.46,.73],[1,.5]);
  const flightOpacity=useTransform(progress,[0,.46,.50,.68,.74,1],[0,0,1,1,0,0]);
  useEffect(()=>{if(control || status!=='sending')return;const t=setTimeout(()=>setStatus('confirmed'),1800);return()=>clearTimeout(t);},[status,control]);
  return <div className="split-layout calendar-layout"><SectionTitle>Встретьтесь.<br/>Без «а когда <br/><em>вам удобно?»</em></SectionTitle>
    <div className="calendar-stage"><div className="calendar-card"><div className="calendar-header"><span>Октябрь</span><CalendarDays size={21}/></div><div className="calendar-days">{['Пн','Вт','Ср','Чт','Пт','Сб','Вс'].map(d=><small key={d}>{d}</small>)}{Array.from({length:21},(_,i)=><span key={i} className={i+5===12||i+5===13?'available':''}>{i+5}</span>)}</div><div className="slots-label"><Avatar size="tiny"/><span>Свободное время Анны</span></div><div className="time-slots" role="radiogroup" aria-label="Свободные слоты встречи">{slots.map((s,i)=><button key={s} role="radio" aria-checked={slot===i} tabIndex={slot===i?0:-1} className={slot===i?'active':''} onKeyDown={e=>onSlotKeyDown(e,i)} onClick={()=>chooseSlot(i)}>{s}</button>)}</div><Action onClick={()=>{if(control){control.seek(.47);control.play();}setStatus('sending');setAutoPlayed(true);}} disabled={status==='sending'}>{status==='confirmed'?'Назначить другое время':'Назначить встречу'}<ArrowUpRight size={18}/></Action></div>
      <div className="candidate-destination"><Avatar size="large"/><span>Анна</span><AnimatePresence mode="wait">{status==='confirmed'?<motion.div key="ok" className="calendar-confirmed" initial={{opacity:0,scale:.8}} animate={{opacity:1,scale:1}}><CheckCheck size={16}/> Подтвердила</motion.div>:<motion.span key="waiting" className="calendar-waiting">{status==='sending'?'Ждём ответ':'Ждёт приглашение'}</motion.span>}</AnimatePresence></div>
      {control ? <motion.div className="flying-invite" style={{x:flightX,y:flightY,rotate:flightRotate,scale:flightScale,opacity:flightOpacity}} aria-hidden="true"><Send size={20}/><strong>Встретимся?</strong><span>{slots[slot]}</span></motion.div> : <AnimatePresence>{status==='sending'&&<motion.div className="flying-invite" initial={{x:0,y:70,rotate:-10,scale:1,opacity:0}} animate={{x:[0,60,140],y:[70,-30,-135],rotate:[-10,4,12],scale:[1,1,.5],opacity:[0,1,1,0]}} exit={{opacity:0}} transition={{duration:1.65,ease:'easeInOut'}}><Send size={20}/><strong>Встретимся?</strong><span>{slots[slot]}</span></motion.div>}</AnimatePresence>}
      <div className="calendar-status" role="status">{status==='confirmed'?`${slots[slot]} · Встреча подтверждена`:status==='sending'?'Приглашение в пути':''}</div>
    </div>
  </div>;
}

function Meeting({ progress }: { progress: MotionValue<number> }) {
  const transform = useTransform(progress,[0,.25,.8,1],['translateY(65px) scale(.91)','translateY(0px) scale(1)','translateY(0px) scale(1)','translateY(-30px) scale(.97)']);
  const noteOpacity=useTransform(progress,[.34,.55],[0,1]);
  const noteY=useTransform(progress,[.34,.55],[35,0]);
  return <>
    <SectionTitle className="center-title">Встреча закончится.<br/><em>Всё важное останется.</em></SectionTitle>
    <motion.div className="meeting-window" style={{transform}}><div className="meeting-bar"><span className="status-dot"/> Встреча с Анной <span><Clock3 size={14}/> 00:24:18</span></div><div className="meeting-people"><div className="meeting-person manager"><Avatar person={{...candidates[3],initials:'ВЫ'}} size="call-avatar"/><span>Вы</span><div className="meeting-wave"><i/><i/><i/><i/><i/></div></div><div className="meeting-person anna"><Avatar size="call-avatar"/><span>Анна Миронова</span><div className="meeting-wave"><i/><i/><i/><i/><i/></div></div></div><div className="meeting-controls" aria-hidden="true"><span><Mic size={18}/></span><span><Video size={18}/></span><span><MessageCircle size={18}/></span><span className="hang-up"><Phone size={18}/></span></div><motion.div className="meeting-note" style={{opacity:noteOpacity,y:noteY}}><MessageCircle size={18}/><span>«А какой у вас опыт в Битрикс24?»</span></motion.div></motion.div>
  </>;
}

function Feedback({ progress, onMatch, control }: { progress: MotionValue<number>; onMatch: (person: Candidate) => void; control?: SceneControl }) {
  const [updated,setUpdated]=useState(false);
  const [manual,setManual]=useState(false);
  const [feedback,setFeedback]=useState('Не хватило опыта в Битрикс24.');
  useMotionValueEvent(progress,'change',v=>{if(control || !manual)setUpdated(v>.58);});
  const feedbackBitrix=/битрикс|crm/i.test(feedback);
  const p=feedbackBitrix?candidates[1]:candidates[2];
  useEffect(() => { if(updated) onMatch(p); }, [updated, p, onMatch]);
  return <>
    <SectionTitle className="center-title">Ваш фидбек меняет<br/><em>следующую подборку.</em></SectionTitle>
    <div className={`feedback-stage ${updated?'updated':''}`}>
      <div className="feedback-bubble"><label htmlFor="feedback-text">Чего не хватило?</label><textarea id="feedback-text" rows={2} value={feedback} onChange={e=>{setFeedback(e.target.value);setUpdated(false);setManual(true);}} maxLength={180}/><button onClick={()=>{control?.seek(updated?.24:.78);setManual(true);setUpdated(v=>!v);}} aria-label={updated?'Повторить уточнение подбора':'Учесть обратную связь в подборе'}>{updated?<Check size={20}/>:<ArrowRight size={20}/>}</button></div>
      <div className="feedback-web"><svg viewBox="0 0 400 330" aria-hidden="true">{Array.from({length:14},(_,i)=>{const x=200+Math.cos(i*.9)*160;const y=165+Math.sin(i*.9)*135;return <g key={i}><path d={`M200 165 Q${x} 165 ${x} ${y}`}/><circle cx={x} cy={y} r={i%3===0?8:4}/></g>;})}<path className="learning-path" d="M0 165 H165 Q200 110 235 165 H400"/></svg><div className="learning-core"><Network size={31}/></div><AnimatePresence>{updated&&<motion.span className="learned-tag" initial={{opacity:0,y:14,scale:.9}} animate={{opacity:1,y:0,scale:1}} exit={{opacity:0}}><Plus size={13}/>{feedbackBitrix?'Опыт в Битрикс24':'Развитие клиентской базы'}</motion.span>}</AnimatePresence></div>
      <div className="next-candidate"><AnimatePresence mode="wait">{updated?<motion.div key={p.initials} className="next-candidate-card" initial={{opacity:0,x:55,rotate:5}} animate={{opacity:1,x:0,rotate:0}} exit={{opacity:0,x:-20}} transition={spring}><Avatar person={p} size="large"/><h3>{p.name}</h3><p>{p.role}</p><span className="new-match"><Check size={16}/>{p.detail}</span></motion.div>:<motion.div key="previous" className="previous-candidate" initial={{opacity:0}} animate={{opacity:1}} exit={{opacity:0}}><Avatar size="large"/><h3>Анна Миронова</h3></motion.div>}</AnimatePresence></div>
      <div className="feedback-return"><svg viewBox="0 0 940 90" preserveAspectRatio="none" aria-hidden="true"><path d="M830 0 V40 Q830 70 800 70 H140 Q110 70 110 40 V0"/></svg></div>
    </div>
  </>;
}

function Offer({ progress, person, control }: { progress: MotionValue<number>; person: Candidate; control?: SceneControl }) {
  const [step,setStep]=useState(0);
  const [manual,setManual]=useState(false);
  useMotionValueEvent(progress,'change',v=>{if(control || !manual)setStep(v>.78?3:v>.55?2:v>.27?1:0);});
  const steps=['Условия согласованы','Оффер отправлен','Оффер принят','Первый день в команде'];
  const chooseStep = (next: number) => {control?.seek([0,.4,.65,.9][next]);setManual(true);setStep(next);};
  return <div className="split-layout offer-layout offer-timeline-layout">
    <SectionTitle>Выбрали кандидата.<br/><em>Поможем довести<br/>до выхода.</em></SectionTitle>
    <div className="offer-stage">
      <div className="offer-paper">
        <div className="offer-top"><img src={LOGO_URL} width="150" height="24" alt="HR Vision"/><span>Предложение о работе</span></div>
        <Avatar person={person} size="medium"/><h3>{person.name.split(' ')[0]},<br/>будем работать вместе.</h3>
        <p>{person.role}</p><div className="offer-terms"><strong>Развивать клиентскую базу<br/>и повторные продажи</strong></div>
        <div className="offer-response" aria-live="polite"><AnimatePresence mode="wait" initial={false}><motion.div key={step} initial={{opacity:0,y:8}} animate={{opacity:1,y:0}} exit={{opacity:0,y:-8}} transition={{duration:.18}}>
          {step===0 ? <span>Условия согласованы. Можно отправлять.</span> : step===1 ? <span>Оффер отправлен. Ждём ответ.</span> : step===2 ? <span className="offer-agreed"><CheckCheck size={18}/>«Принимаю предложение. До встречи!»</span> : <span className="offer-agreed"><CheckCheck size={18}/>Первый день согласован. До встречи в команде.</span>}
        </motion.div></AnimatePresence></div>
        <Action onClick={()=>chooseStep((step+1)%4)}>{['Отправить оффер','Посмотреть ответ','Согласовать первый день','Повторить пример'][step]}</Action>
      </div>
      <ol className="offer-timeline" aria-label="Путь до выхода в команду">{steps.map((label,i)=><li key={label} className={`${i<step?'complete':''} ${i===step?'current':''}`}>
        <button type="button" aria-current={i===step?'step':undefined} onClick={()=>chooseStep(i)}><span className="offer-milestone">{i<step?<Check size={15}/>:i+1}</span><span>{label}</span></button>
      </li>)}</ol>
    </div>
  </div>;
}

function Finish({ onStart }: { onStart: () => void }) {
  return <section className="finish-section" id="start"><div className="finish-orbits" aria-hidden="true"><i/><i/><i/></div><h2>Оплата — только<br/><em>после найма.</em></h2><Action onClick={onStart} className="final-cta">Найти кандидата</Action><footer><img src={LOGO_URL} width="150" height="24" alt="HR Vision"/><a href="#top" onClick={e=>{e.preventDefault();jump('top');}}>В начало</a></footer></section>;
}

function StartDialog({ open, setOpen, need, setNeed, onOpenProfile }: { open: boolean; setOpen: (v:boolean)=>void; need:string; setNeed:(s:string)=>void; onOpenProfile?:()=>void }) {
  return <DemoFunnel open={open} setOpen={setOpen} need={need} setNeed={setNeed} onOpenProfile={onOpenProfile}/>;
}

export function CompanySite() {
  const [need,setNeed]=useState(initialNeed);
  const [needEdited,setNeedEdited]=useState(false);
  const [matchedPerson,setMatchedPerson]=useState(candidates[1]);
  const reduced=useReducedMotion();
  function editNeed(value: string) { setNeed(value); setNeedEdited(true); }
  function startHiring() { window.location.assign(employerEntryUrl('register', needEdited ? need : '')); }
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (!id) return;
    const frame = requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView({ behavior: 'instant', block: 'start' }));
    return () => cancelAnimationFrame(frame);
  }, []);
  return <MotionConfig reducedMotion="user"><div className={`company-site ${reduced ? 'reduced-motion motion-paused' : ''}`}>
    <a className="skip-link" href="#needs">Перейти к задаче</a>
    <header className="site-header"><a className="brand" href="#top" aria-label="HR Vision — в начало"><img src={LOGO_URL} width="150" height="24" alt="HR Vision"/></a><nav aria-label="Кабинет работодателя"><a className="employer-login" href={employerEntryUrl('login')}>Войти</a><SiteCountdownButton startAtSection="calendar" onStart={startHiring}/></nav></header>
    <main>
      <Chapter id="top" className="intro-chapter">{p=><Intro progress={p}/>}</Chapter>
      <Chapter id="needs" className="needs-chapter">{p=><Needs progress={p} need={need} setNeed={editNeed} onNext={startHiring} nextLabel="Начать подбор"/>}</Chapter>
      <Pool paused={!!reduced}/>
      <Chapter id="connections" className="connections-chapter">{p=><Connections progress={p} need={need}/>}</Chapter>
      <InterestChapter/>
      <InterviewChapter paused={!!reduced}/>
      <Chapter id="calendar" className="calendar-chapter">{p=><Calendar progress={p}/>}</Chapter>
      <Chapter id="meeting" className="meeting-chapter">{p=><Meeting progress={p}/>}</Chapter>
      <FeedbackChapter onMatch={setMatchedPerson} paused={!!reduced}/>
      <Chapter id="offer" className="offer-chapter">{p=><Offer progress={p} person={matchedPerson}/>}</Chapter>
      <Finish onStart={startHiring}/>
    </main>
  </div></MotionConfig>;
}

export { Intro, Needs, Connections, Pool, Interest, Interview, Calendar, Meeting, Feedback, Offer, Finish, StartDialog, initialNeed, candidates };
export type { Candidate };
