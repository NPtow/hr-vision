import React, { useEffect, useId, useState } from 'react';
import { motion, useReducedMotion, useTransform, type MotionValue } from 'motion/react';
import { ArrowUpRight, BriefcaseBusiness, Check, CheckCheck, Send } from 'lucide-react';
import { candidates } from '../candidate-data';
import { LOGO_URL } from '../assets';
import type { InterestVariant } from './interest-variants';
import './interest.css';

type Progress = MotionValue<number>;
const colors = ['#eeac91', '#96c8b0', '#b7a6e3'];
const replies = ['Давайте познакомимся.', 'Хочу обсудить задачу.', 'Мне это интересно.'];
const smooth = (x: number) => x * x * (3 - 2 * x);
function usePhase(progress: Progress, range: number[], values: number[]) {
  return useTransform(progress, range, values, { ease: smooth });
}
function useCompact() {
  const [compact, setCompact] = useState(() => matchMedia('(max-width: 600px)').matches);
  useEffect(() => {
    const query = matchMedia('(max-width: 600px)');
    const update = () => setCompact(query.matches);
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  return compact;
}
function Face({ i }: { i: number }) {
  return <span className={`ic-face ic-color-${i}`} aria-hidden="true">{candidates[i].initials}</span>;
}
function Identity({ i }: { i: number }) {
  return <div className="ic-identity"><Face i={i}/><div><strong>{candidates[i].name}</strong><span>{candidates[i].role}</span></div></div>;
}

function Letter({ i, progress, compact }: { i: number; progress: Progress; compact: boolean }) {
  const delay = i * .045;
  const x = usePhase(progress, [0, .08, .34, .9], [i * 7, i * 7, compact ? 0 : (i - 1) * 335, compact ? 0 : (i - 1) * 335]);
  const y = usePhase(progress, [0, .34, .88], [i * -9, compact ? (i - 1) * 176 : i === 1 ? 20 : 0, compact ? (i - 1) * 176 : 0]);
  const rotate = usePhase(progress, [0, .34, .87], [(i - 1) * -4, compact ? (i - 1) * 2 : (i - 1) * 5, 0]);
  const flap = usePhase(progress, [.34 + delay, .5 + delay], [0, 178]);
  const flapLayer = useTransform(() => progress.get() < .44 + delay ? 5 : 0);
  const lift = usePhase(progress, [.44 + delay, .65 + delay], [0, compact ? -34 : -125]);
  const flip = usePhase(progress, [.64 + delay, .79 + delay], [0, 180]);
  const envelope = usePhase(progress, [.66 + delay, .84 + delay], [1, 0]);
  const shadow = useTransform(() => `0 ${8 + smooth(Math.min(1, progress.get())) * 8}px 30px #34312912`);
  const check = usePhase(progress, [.79 + delay, .88 + delay], [0, 1]);
  return <motion.div className={`ic-letter ic-color-${i}`} style={{ x, y, rotate, zIndex: 3 - i }}>
    <motion.div className="ic-envelope-back" style={{ opacity: envelope }}/>
    <div className="ic-letter-window"><motion.div className="ic-letter-lift" style={{ y: lift }}>
      <motion.div className="ic-letter-sheet" style={{ rotateY: flip, boxShadow: shadow }}>
        <div className="ic-paper-face ic-paper-front"><BriefcaseBusiness size={23}/><strong>Менеджер<br/> по работе<br/> с клиентами</strong><span>Давайте познакомимся</span><Send size={19}/></div>
        <div className="ic-paper-face ic-paper-reply"><Identity i={i}/><strong className="ic-quote">{replies[i]}</strong><motion.span className="ic-reply-check" style={{ scale: check, opacity: check }}><Check size={18}/> Есть интерес</motion.span></div>
      </motion.div>
    </motion.div></div>
    <motion.div className="ic-envelope-front" style={{ opacity: envelope }}><span>{candidates[i].name}</span><ArrowUpRight size={21}/></motion.div>
    <motion.div className="ic-envelope-flap" style={{ rotateX: flap, zIndex: flapLayer, opacity: envelope }}/>
  </motion.div>;
}
function Letters({ progress, compact }: { progress: Progress; compact: boolean }) {
  const opacity = usePhase(progress, [0, .13, .28], [1, 1, 0]);
  const y = usePhase(progress, [0, .28], [0, -25]);
  return <div className="ic-letters-stage">
    <motion.div className="ic-job-label" style={{ opacity, y }}><BriefcaseBusiness size={22}/>Менеджер по работе с клиентами</motion.div>
    {[0, 1, 2].map(i => <Letter key={i} i={i} progress={progress} compact={compact}/>)}
  </div>;
}

// Like Magic UI's Animated Beam, the signal follows one SVG curve in both
// directions. A single external clock replaces free-running repeat animations.
function SignalThread({ i, progress, compact }: { i: number; progress: Progress; compact: boolean }) {
  const id = useId().replace(/:/g, '');
  const offset = i * .055;
  const draw = usePhase(progress, [.04 + offset, .27 + offset], [0, 1]);
  const travel = usePhase(progress, [.18 + offset, .47 + offset], [0, 1]);
  const answer = usePhase(progress, [.54 + offset, .84 + offset], [1, 0]);
  const incomingOpacity = usePhase(progress, [.16 + offset, .2 + offset, .43 + offset, .48 + offset], [0, 1, 1, 0]);
  const answerOpacity = usePhase(progress, [.53 + offset, .56 + offset, .81 + offset, .86 + offset], [0, 1, 1, 0]);
  const x1 = useTransform(() => compact ? 100 + travel.get() * (180 + i * 128) - 60 : 145 + travel.get() * 510);
  const x2 = useTransform(() => compact ? 100 + travel.get() * (180 + i * 128) + 60 : 265 + travel.get() * 510);
  const returnX1 = useTransform(() => compact ? 100 + answer.get() * (180 + i * 128) - 60 : 145 + answer.get() * 510);
  const returnX2 = useTransform(() => compact ? 100 + answer.get() * (180 + i * 128) + 60 : 265 + answer.get() * 510);
  const target = [80, 230, 380][i];
  const path = compact ? `M175 100 C${[30,175,320][i]} 160 ${[30,175,320][i]} 250 175 ${280+i*128}` : `M205 230 C430 230 440 ${target} 715 ${target}`;
  return <svg className="ic-thread" viewBox={compact ? '0 0 350 640' : '0 0 1050 460'} aria-hidden="true">
    <defs>
      <motion.linearGradient id={`${id}-out`} gradientUnits="userSpaceOnUse" x1={compact?175:x1} x2={compact?175:x2} y1={compact?x1:0} y2={compact?x2:0}>
        <stop offset="0" stopColor={colors[i]} stopOpacity="0"/><stop offset=".55" stopColor={colors[i]}/><stop offset="1" stopColor="#fff" stopOpacity="0"/>
      </motion.linearGradient>
      <motion.linearGradient id={`${id}-back`} gradientUnits="userSpaceOnUse" x1={compact?175:returnX1} x2={compact?175:returnX2} y1={compact?returnX1:0} y2={compact?returnX2:0}>
        <stop offset="0" stopColor="#4c9d7c" stopOpacity="0"/><stop offset=".45" stopColor="#4c9d7c"/><stop offset="1" stopColor={colors[i]} stopOpacity="0"/>
      </motion.linearGradient>
    </defs>
    <motion.path d={path} fill="none" stroke={colors[i]} strokeOpacity=".2" strokeWidth="2" style={{ pathLength: draw }}/>
    <motion.path d={path} fill="none" stroke={`url(#${id}-out)`} strokeWidth="16" strokeLinecap="round" style={{ opacity: incomingOpacity }}/>
    <motion.path d={path} fill="none" stroke={`url(#${id}-out)`} strokeWidth="4" strokeLinecap="round" style={{ opacity: incomingOpacity }}/>
    <motion.path d={path} fill="none" stroke={`url(#${id}-back)`} strokeWidth="7" strokeLinecap="round" style={{ opacity: answerOpacity }}/>
  </svg>;
}
function SignalPerson({ i, progress }: { i: number; progress: Progress }) {
  const offset = i * .055;
  const opacity = usePhase(progress, [.02 + offset, .15 + offset], [0, 1]);
  const x = usePhase(progress, [.02 + offset, .17 + offset], [35, 0]);
  const ringScale = usePhase(progress, [.4 + offset, .59 + offset], [.8, 1.8]);
  const ringOpacity = usePhase(progress, [.39 + offset, .44 + offset, .59 + offset], [0, .65, 0]);
  const received = usePhase(progress, [.43 + offset, .48 + offset, .57 + offset, .62 + offset], [0, 1, 1, 0]);
  const replied = usePhase(progress, [.64 + offset, .74 + offset], [0, 1]);
  const reveal = usePhase(progress, [.6 + offset, .74 + offset], [12, 0]);
  return <motion.div className={`ic-signal-person ic-color-${i}`} style={{ opacity, x }}>
    <motion.i className="ic-signal-ring" style={{ scale: ringScale, opacity: ringOpacity }}/>
    <Identity i={i}/>
    <motion.div className="ic-signal-received" style={{ opacity: received }}><CheckCheck size={17}/> Приглашение получено</motion.div>
    <motion.div className="ic-signal-response" style={{ opacity: replied, y: reveal }}><Check size={17}/>{replies[i]}</motion.div>
  </motion.div>;
}
function Signal({ progress, compact }: { progress: Progress; compact: boolean }) {
  const halo = usePhase(progress, [.08, .35, .85, 1], [.85, 1.15, 1.05, 1]);
  const reply = usePhase(progress, [.87, .97], [0, 1]);
  const invite = usePhase(progress, [.8, .86], [1, 0]);
  return <div className="ic-signal-stage">
    {[0, 1, 2].map(i => <SignalThread key={i} i={i} progress={progress} compact={compact}/>)}
    <div className="ic-signal-origin"><motion.div className="ic-signal-halo" style={{ scale: halo }}/><div className="ic-signal-core"><img src={LOGO_URL} alt="HR Vision"/><BriefcaseBusiness size={32}/><strong>Менеджер<br/> по работе<br/> с клиентами</strong><motion.span style={{ opacity: invite }}><Send size={14}/> Приглашение</motion.span><motion.span className="ic-origin-replies" style={{ opacity: reply }}><CheckCheck size={16}/> 3 отклика</motion.span></div></div>
    <div className="ic-signal-people">{[0, 1, 2].map(i => <SignalPerson key={i} i={i} progress={progress}/>)}</div>
  </div>;
}

function ReplyCard({ i, progress, compact }: { i: number; progress: Progress; compact: boolean }) {
  const offset = i * .045;
  const x = usePhase(progress, [0, .1, .37, .87], [(i - 1) * 30, (i - 1) * 30, compact ? (i - 1) * 19 : (i - 1) * 305, compact ? 0 : (i - 1) * 305]);
  const y = usePhase(progress, [0, .35, .84], [i * 10, compact ? (i - 1) * 55 : i === 1 ? -28 : 14, compact ? (i - 1) * 177 : 0]);
  const rotate = usePhase(progress, [0, .35, .87], [(i - 1) * 7, (i - 1) * 9, 0]);
  const rotateY = usePhase(progress, [.45 + offset, .62 + offset], [0, 180]);
  const scale = usePhase(progress, [0, .32, .79], [.95, 1, 1]);
  const arrived = usePhase(progress, [.23 + offset, .36 + offset], [0, 1]);
  const invitationClip = useTransform(() => `inset(${(1-arrived.get())*100}% 0 0 0)`);
  const response = usePhase(progress, [.51 + offset, .64 + offset], [0, 1]);
  return <motion.div className={`ic-card-position ic-color-${i}`} style={{ x, y, rotate, scale, zIndex: 3 - i }}>
    <motion.div className="ic-flip-card" style={{ rotateY }}>
      <div className="ic-card-side ic-card-front"><Identity i={i}/><div className="ic-card-art"><span/><span/><span/><b>{candidates[i].initials}</b></div><strong className="ic-card-detail">{candidates[i].detail}</strong><motion.div className="ic-card-invite" style={{ clipPath: invitationClip }}><Send size={21}/> Ваше приглашение</motion.div></div>
      <div className="ic-card-side ic-card-back"><Identity i={i}/><motion.div className="ic-card-answer" style={{ opacity: response }}><span className="ic-large-check"><Check size={32}/></span><strong>{replies[i]}</strong><span><CheckCheck size={18}/> Готовы к знакомству</span></motion.div></div>
    </motion.div>
  </motion.div>;
}
function Cards({ progress, compact }: { progress: Progress; compact: boolean }) {
  const opacity = usePhase(progress, [0, .13, .3], [1, 1, 0]);
  const y = usePhase(progress, [.13, .3], [0, 35]);
  return <div className="ic-cards-stage">
    {[2, 1, 0].map(i => <ReplyCard key={i} i={i} progress={progress} compact={compact}/>)}
    <motion.div className="ic-card-job" style={{ opacity, y }}><BriefcaseBusiness size={21}/><strong>Менеджер по работе с клиентами</strong><Send size={19}/></motion.div>
  </div>;
}

export function InterestConcept({ variant, progress }: { variant: Exclude<InterestVariant, 'original'>; progress: Progress }) {
  const compact = useCompact();
  const reduced = useReducedMotion();
  const clock = useTransform(() => reduced ? 1 : progress.get());
  return <section className={`interest-concept ic-${variant}`} data-interest-variant={variant} aria-label="Приглашаем кандидатов и получаем отклики">
    <h2>Вы видите кандидатов,<br/><em>которым вы интересны.</em></h2>
    {variant === 'letters' ? <Letters progress={clock} compact={compact}/> : variant === 'signal' ? <Signal progress={clock} compact={compact}/> : <Cards progress={clock} compact={compact}/>}
  </section>;
}
