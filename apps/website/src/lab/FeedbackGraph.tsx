import React, { useEffect, useId, useRef, useState } from 'react';
import { motion, useMotionValueEvent, useReducedMotion, useTransform, type MotionValue } from 'motion/react';
import { ArrowRight, Check, CornerDownLeft, MessageSquare, Network, Plus, RotateCcw, Video } from 'lucide-react';
import { candidates, type Candidate } from '../candidate-data';
import type { SceneControl } from '../site';
import './feedback-graph.css';

type Progress = MotionValue<number>;
type Moment = 'review' | 'meeting';
const smooth = (v: number) => v * v * (3 - 2 * v);
const phaseFor = (p: number) => p < .18 ? 0 : p < .36 ? 1 : p < .52 ? 2 : p < .76 ? 3 : 4;
const reducedPhaseProgress = [0, .27, .46, .68, 1] as const;
const usePhase = (p: Progress, range: number[], values: number[]) => useTransform(p, range, values, { ease: smooth });

export const feedbackAnswers = [
  { id: 'bitrix', label: 'Не хватает опыта в Битрикс24', requirement: 'Опыт в Битрикс24', person: 1, proof: 'Битрикс24 · 4 года', quote: 'Вёл сделки и настраивал воронки в Битрикс24.', evidence: ['Возвращал B2B-клиентов', 'Работал в вашей CRM'], skill: 'CRM', replaced: 'Опыт в любой CRM', time: '09:35' },
  { id: 'clients', label: 'Нужен опыт возврата ушедших клиентов', requirement: 'Возвращать ушедших', person: 2, proof: 'Возвращала ушедших клиентов', quote: 'Разбирала причины ухода и заново договаривалась с клиентами.', evidence: ['Развивала клиентскую базу', 'Возвращала ушедших клиентов'], skill: 'Возврат клиентов', replaced: 'Развивать клиентскую базу', time: '04:20' },
  { id: 'ownership', label: 'Нужно самому выстроить процесс', requirement: 'Выстроить процесс', person: 3, proof: 'Выстроил процесс продаж', quote: 'Сам собрал процесс: от первого контакта до повторной продажи.', evidence: ['Работал с B2B-продажами', 'Сам выстраивал процесс'], skill: 'Самостоятельность', replaced: 'Работать по готовому процессу', time: '12:10' },
] as const;
type Answer = typeof feedbackAnswers[number];
const graphPeople = [
  { person: 0, x: 522, y: 259 },
  { person: 1, x: 429, y: 410 },
  { person: 2, x: 133, y: 410 },
  { person: 3, x: 61, y: 260 },
];
const stars = Array.from({ length: 22 }, (_, i) => {
  const angle = i * 2.399963;
  return { x: 290 + Math.cos(angle) * (135 + (i % 4) * 25), y: 243 + Math.sin(angle) * (108 + (i % 3) * 32) };
});

function CandidateNode({ item, answer, progress }: { item: typeof graphPeople[number]; answer: Answer; progress: Progress }) {
  const selected = item.person === answer.person;
  const active = usePhase(progress, [.52, .73], [0, 1]);
  const opacity = usePhase(progress, [.48, .75], [item.person === 0 ? 1 : .58, selected ? 1 : .32]);
  const scale = usePhase(progress, [.52, .76], [1, selected ? 1.12 : .95]);
  const ringOpacity = usePhase(progress, [.55, .68, .79], [0, selected ? .7 : 0, 0]);
  const ringScale = usePhase(progress, [.55, .79], [1, 1.65]);
  const person = candidates[item.person];
  return <motion.div className={`fg-person fg-person-${person.color} ${selected ? 'is-match' : ''}`} style={{ left: `${item.x / 6}%`, top: `${item.y / 4.7}%`, opacity, scale }}>
    <motion.i className="fg-person-wave" style={{ opacity: ringOpacity, scale: ringScale }}/>
    <span className="fg-avatar">{person.initials}<motion.b style={{ opacity: selected ? active : 0 }}><Check size={12}/></motion.b></span>
    <strong>{person.name.split(' ')[0]}</strong>
  </motion.div>;
}

function CandidateEdge({ item, answer, progress }: { item: typeof graphPeople[number]; answer: Answer; progress: Progress }) {
  const selected = item.person === answer.person;
  const oldOpacity = usePhase(progress, [.5, .7], [item.person === 0 ? .7 : .23, selected ? .14 : .07]);
  const draw = usePhase(progress, [.52, .72], [0, selected ? 1 : 0]);
  const sweep = usePhase(progress, [.55, .73], [0, 1]);
  const glow = usePhase(progress, [.54, .58, .71, .76], [0, selected ? 1 : 0, selected ? 1 : 0, 0]);
  const base = `M290 235 Q${item.x} 235 ${item.x} ${item.y}`;
  const learned = `M278 342 Q${item.x} 342 ${item.x} ${item.y}`;
  return <>
    <motion.path d={base} className="fg-base-edge" style={{ opacity: oldOpacity }}/>
    <motion.path d={learned} className="fg-learned-halo" style={{ pathLength: draw }}/>
    <motion.path d={learned} className="fg-learned-edge" style={{ pathLength: draw }}/>
    <motion.path d={learned} className="fg-edge-pulse" style={{ pathLength: .1, pathOffset: sweep, pathSpacing: 1, opacity: glow }}/>
  </>;
}

function MatchingGraph({ answer, progress, phase }: { answer: Answer; progress: Progress; phase: number }) {
  const incoming = usePhase(progress, [.18, .35], [0, 1]);
  const incomingPulse = usePhase(progress, [.19, .23, .32, .37], [0, 1, 1, 0]);
  const incomingOffset = usePhase(progress, [.2, .36], [0, .96]);
  const requirement = usePhase(progress, [.3, .43], [0, 1]);
  const requirementScale = usePhase(progress, [.3, .43], [.86, 1]);
  const requirementY = usePhase(progress, [.3, .43], [12, 0]);
  const toTask = usePhase(progress, [.36, .51], [0, 1]);
  const coreRing = usePhase(progress, [.36, .48, .58], [0, .8, .24]);
  const originalOpacity = usePhase(progress, [.35, .52], [1, .16]);
  const uid = useId();
  return <div className="fg-network" aria-label={`Граф задачи: ${phase >= 2 ? answer.requirement : answer.replaced}`} data-requirement={phase >= 2 ? answer.id : 'initial'}>
    <div className="fg-graph-halo"/>
    <svg className="fg-wires" viewBox="0 0 600 470" aria-hidden="true">
      <defs><linearGradient id={uid} x1="0" y1="0" x2="1" y2="1"><stop stopColor="#9bade2"/><stop offset="1" stopColor="#68a890"/></linearGradient></defs>
      <g className="fg-constellation">{stars.map((s, i) => <g key={i}><path d={`M290 235 Q${s.x} 235 ${s.x} ${s.y}`}/><path d={`M${s.x} ${s.y} L${stars[(i + 5) % stars.length].x} ${stars[(i + 5) % stars.length].y}`}/><circle cx={s.x} cy={s.y} r={i % 4 === 0 ? 3.5 : 2}/></g>)}</g>
      <path className="fg-preserved-edge" d="M290 235 Q144 223 145 92 M290 235 Q290 120 315 59 M290 235 Q475 226 476 101"/>
      <motion.path d="M0 322 C70 322 130 342 205 342" className="fg-answer-edge" style={{ pathLength: incoming }}/>
      <motion.path d="M0 322 C70 322 130 342 205 342" className="fg-edge-pulse" style={{ pathLength: .14, pathOffset: incomingOffset, pathSpacing: 1, opacity: incomingPulse }}/>
      <motion.path d="M278 320 Q258 293 281 268" fill="none" stroke={`url(#${uid})`} strokeWidth="3" style={{ pathLength: toTask }}/>
      {graphPeople.map(item => <CandidateEdge key={item.person} item={item} answer={answer} progress={progress}/>)}
    </svg>
    <span className="fg-known fg-known-return"><Check size={11}/>Вернуть клиентов</span>
    <span className="fg-known fg-known-b2b"><Check size={11}/>B2B-продажи</span>
    <span className="fg-known fg-known-interest"><Check size={11}/>Интерес к задаче</span>
    <div className="fg-task"><motion.i style={{ opacity: coreRing }}/><Network size={21}/><strong>Ваша задача</strong><span>Развивать продажи</span></div>
    <motion.span className="fg-original-criterion" style={{ opacity: originalOpacity }}>{answer.replaced}</motion.span>
    <motion.div className="fg-added-criterion" style={{ opacity: requirement, scale: requirementScale, y: requirementY }}><Plus size={14}/><strong>{answer.requirement}</strong></motion.div>
    {graphPeople.map(item => <CandidateNode key={item.person} item={item} answer={answer} progress={progress}/>)}
    <span className="fg-network-caption" aria-live="polite">{['Сохраняем требования к задаче', 'Ваш ответ входит в граф', 'Добавили конкретное требование', 'Выделяем кандидата с нужным опытом', 'Подтверждаем выбор его интервью'][phase]}</span>
  </div>;
}

export function FeedbackGraph({ control, moment = 'meeting', onOtherMoment, onMatch }: { control: SceneControl; moment?: Moment; onOtherMoment: () => void; onMatch?: (person: Candidate) => void }) {
  const root = useRef<HTMLElement>(null);
  const [answerIndex, setAnswerIndex] = useState(0);
  const [phase, setPhase] = useState(0);
  const reduced = useReducedMotion();
  const answer = feedbackAnswers[answerIndex];
  const person = candidates[answer.person];
  useEffect(() => setPhase(phaseFor(control.progress.get())), [control.progress]);
  useMotionValueEvent(control.progress, 'change', p => setPhase(phaseFor(p)));
  useEffect(() => { if (phase === 4) onMatch?.(person); }, [onMatch, person, phase]);
  useEffect(() => {
    const section = root.current;
    const viewport = section?.closest<HTMLElement>('.chapter-sticky, [data-graph-viewport]');
    if (!section || !viewport || !window.matchMedia('(max-width: 600px)').matches) return;
    if (reduced && viewport.hasAttribute('data-graph-viewport')) return;
    const target = phase >= 4 ? section.querySelector('.fg-output') : phase >= 2 ? section.querySelector('.fg-network') : null;
    const top = target ? viewport.scrollTop + target.getBoundingClientRect().top - viewport.getBoundingClientRect().top - 24 : 0;
    viewport.scrollTo({ top, behavior: reduced ? 'instant' : 'smooth' });
  }, [phase, reduced]);
  const progress = useTransform(() => reduced ? reducedPhaseProgress[phaseFor(control.progress.get())] : control.progress.get());
  const quoteOpacity = usePhase(progress, [.13, .23], [0, 1]);
  const oldResult = usePhase(progress, [.71, .78], [1, 0]);
  const newResult = usePhase(progress, [.75, .84], [0, 1]);
  const newY = usePhase(progress, [.75, .86], [20, 0]);
  const backPath = usePhase(progress, [.88, 1], [0, 1]);
  const choose = (index: number) => { setAnswerIndex(index); control.seek(.2); };
  const apply = () => { control.seek(reduced ? 1 : .23); if (!reduced) control.play(); };
  const retry = () => control.seek(.2);

  return <section ref={root} className="feedback-graph" data-moment={moment} data-phase={phase} data-answer={answer.id}>
    <h2>{moment === 'review' ? <>Уже первое впечатление<br/><em>делает подбор точнее.</em></> : <>Ваш ответ меняет<br/><em>следующую подборку.</em></>}</h2>
    <div className="fg-flow">
      <div className="fg-input">
        <div className="fg-source"><span className="fg-source-avatar">АМ</span><div><strong>Анна Миронова</strong><span>{moment === 'review' ? <><Video size={12}/>Интервью просмотрено</> : <><Check size={12}/>Встреча завершена</>}</span></div></div>
        <fieldset className="fg-question"><legend>{moment === 'meeting' ? 'Чего не хватило на встрече?' : 'Чего не хватило?'}</legend><div className="fg-answers">{feedbackAnswers.map((item, index) => <button key={item.id} type="button" aria-pressed={answerIndex === index} onClick={() => choose(index)}><span className="fg-radio">{answerIndex === index && <i/>}</span>{item.label}</button>)}</div></fieldset>
        <button className="fg-apply" type="button" onClick={apply}>Уточнить подбор<ArrowRight size={16}/></button>
        <motion.div className="fg-quote" style={{ opacity: quoteOpacity }}><MessageSquare size={14}/><span>«{answer.label}»</span></motion.div>
      </div>
      <MatchingGraph answer={answer} progress={progress} phase={phase}/>
      <div className="fg-output">
        <motion.div className="fg-result-before" style={{ opacity: oldResult }} aria-hidden={phase >= 4}>
          <span className="fg-output-label">До уточнения</span>
          <span className="fg-result-avatar fg-peach">АМ</span><h3>Анна Миронова</h3><span className="fg-result-role">Key Account Manager</span>
          <div className="fg-proof-row"><Check size={14}/>Возвращала клиентов</div>
          <div className="fg-proof-row"><Check size={14}/>Развивала B2B-базу</div>
          <div className="fg-missing"><span>Нужно уточнить</span><strong>{answer.skill}</strong></div>
        </motion.div>
        <motion.div className="fg-result-after" style={{ opacity: newResult, y: newY }} aria-hidden={phase < 4}>
          <span className="fg-output-label"><Check size={13}/>С учётом вашего ответа</span>
          <span className={`fg-result-avatar fg-${person.color}`}>{person.initials}</span><h3>{person.name}</h3><span className="fg-result-role">{person.role}</span>
          <strong className="fg-match-proof">{answer.proof}</strong>
          {answer.evidence.map(text => <div key={text} className="fg-proof-row"><Check size={14}/>{text}</div>)}
          <div className="fg-interview-proof"><span><Video size={13}/>Из интервью · {answer.time}</span><q>{answer.quote}</q></div>
        </motion.div>
      </div>
      <div className="fg-return" aria-hidden="true"><svg viewBox="0 0 1100 45" preserveAspectRatio="none"><motion.path d="M975 0 V10 Q975 35 950 35 H145 Q120 35 120 10 V0" style={{ pathLength: backPath }}/></svg></div>
    </div>
    <div className="fg-bottom"><button type="button" onClick={onOtherMoment}>{moment === 'review' ? <CornerDownLeft size={14}/> : <Video size={14}/>} {moment === 'review' ? 'Цикл после встречи' : 'Обзор интерфейса'}</button><button type="button" onClick={retry}><RotateCcw size={13}/>Попробовать другой ответ</button></div>
  </section>;
}
