import React, { useEffect, useState } from 'react';
import { motion, useReducedMotion, useTransform, type MotionValue } from 'motion/react';
import { ArrowRight, Check, CheckCheck, MessageCircle, Plus, Video } from 'lucide-react';
import { candidates } from '../candidate-data';
import type { SceneControl } from '../site';
import { LOGO_URL } from '../assets';
import type { FeedbackVariant } from './feedback-variants';
import type { ReviewMatch } from './review-data';
import { useFrameProgressChange } from './useFrameProgressChange';
import './feedback.css';

type Progress = MotionValue<number>;
const smooth = (n: number) => n * n * (3 - 2 * n);
const usePhase = (p: Progress, times: number[], values: number[]) => useTransform(p, times, values, { ease: smooth });
function useCompact() {
  const [compact, setCompact] = useState(() => matchMedia('(max-width: 600px)').matches);
  useEffect(() => {
    const media = matchMedia('(max-width: 600px)');
    const update = () => setCompact(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return compact;
}

const cardPeople = [candidates[0], candidates[2], candidates[1]];
const headings = [<>Возвращает<br/>клиентов.</>, <>Развивает<br/>клиентскую базу.</>, <>Битрикс24.<br/><em>4 года.</em></>];
const skills = ['amoCRM', '6 лет опыта', 'B2B-продажи'];

// Depth, rotation and the lift/settle gesture adapt Aceternity's free
// Animated Testimonials example. All values use the external lab clock;
// seeded poses replace random rotations and independent autoplay timers.
function CandidateCard({ index, progress, compact, match, onOpen, ready = false }: { index: number; progress: Progress; compact: boolean; match?: ReviewMatch; onOpen?: () => void; ready?: boolean }) {
  const person = index === 2 && match ? match.person : cardPeople[index];
  const fan = compact ? [-22, 22, 0][index] : [-245, 245, 0][index];
  const initialX = compact ? index * 8 : 185 + index * 11;
  const x = usePhase(progress, [0, .37, .55, .73], [initialX, initialX, fan, fan]);
  const initialY = (compact ? 219 : 94) - index * 11;
  const finalY = (compact ? 174 : 109) + (index === 2 ? 0 : 9);
  const y = usePhase(progress, [0, .37, .55, .64, .76], [initialY, initialY, finalY, index === 2 ? finalY - 72 : finalY + 8, finalY]);
  const rotate = usePhase(progress, [0, .37, .55, .76], [[-3, 5, -7][index], [-3, 5, -7][index], index === 2 ? -5 : [-11, 11][index], index === 2 ? 0 : [-9, 9][index]]);
  const scale = usePhase(progress, [0, .37, .56, .76], [1 - index * .035, 1 - index * .035, index === 2 ? 1.02 : .89, index === 2 ? 1 : .88]);
  const opacity = usePhase(progress, [.42, .63, .84], [1, index === 2 ? 1 : .8, index === 2 ? 1 : .52]);
  const zIndex = useTransform(() => index === 2 && progress.get() > .57 ? 40 : 30 - index * 10);
  const proof = usePhase(progress, [.74, .85], [0, 1]);
  const ring = useTransform(() => index === 2 ? `0 0 0 ${proof.get() * 2}px #75a58b55, 0 14px 38px #23352512` : '0 8px 25px #37302a0c');
  return <motion.article className={`fb-person-card fb-person-${index}`} style={{ x, y, rotate, scale, opacity, zIndex, boxShadow: ring }} aria-label={person.name}>
    <div className="fb-person-top"><span className="fb-person-avatar">{person.initials}</span><div><strong>{person.name}</strong><span>{person.role}</span></div></div>
    <div className="fb-card-art" aria-hidden="true"><span/><i/><b/></div>
    <h3>{index === 2 && match ? <>{match.headline[0]}<br/><em>{match.headline[1]}</em></> : headings[index]}</h3>
    <div className="fb-person-skills"><span>{index === 2 && match ? match.proof : skills[index]}</span>{index === 2 && !match && <motion.span className="fb-found-skill" style={{ opacity: proof }}><Check size={14}/>Опыт подтверждён</motion.span>}</div>
    {index === 2 && onOpen ? <button className="fb-card-footer fb-open-interview" type="button" disabled={!ready} onClick={onOpen}><Video size={16}/>Смотреть интервью<ArrowRight size={17}/></button> : <div className="fb-card-footer"><Video size={16}/>Интервью готово<ArrowRight size={17}/></div>}
  </motion.article>;
}

function FeedbackNote({ progress, compact, onApply }: { progress: Progress; compact: boolean; onApply: () => void }) {
  const width = usePhase(progress, [0, .29, .43], [compact ? 326 : 330, compact ? 326 : 330, 250]);
  const height = usePhase(progress, [0, .29, .43], [compact ? 140 : 172, compact ? 140 : 172, 52]);
  const x = usePhase(progress, [0, .3, .46], [compact ? 0 : -329, compact ? 0 : -329, 0]);
  const y = usePhase(progress, [0, .3, .46], [compact ? 27 : 196, compact ? 27 : 196, compact ? 37 : 12]);
  const rotate = usePhase(progress, [0, .22, .45], [-2, -2, 0]);
  const radius = usePhase(progress, [.29, .44], [24, 26]);
  const note = usePhase(progress, [.29, .36], [1, 0]);
  const filter = usePhase(progress, [.37, .46], [0, 1]);
  const color = useTransform(progress, [.29, .44], ['#ffffff', '#e1eee7']);
  const underline = usePhase(progress, [.1, .23], [0, 1]);
  const underlineWidth = useTransform(() => `${underline.get() * 100}%`);
  return <motion.button type="button" className="fb-note" style={{ width, height, x, y, rotate, borderRadius: radius, backgroundColor: color }} onClick={onApply} aria-label="Учесть фидбек об опыте в Битрикс24">
    <motion.span className="fb-note-copy" style={{ opacity: note }}><MessageCircle size={18}/><span>Не хватило опыта<br/>в <span className="fb-note-word">Битрикс24<motion.i style={{ width: underlineWidth }}/></span>.</span><span className="fb-note-send"><ArrowRight size={17}/></span></motion.span>
    <motion.span className="fb-filter-copy" style={{ opacity: filter }}><Plus size={17}/>Опыт в Битрикс24<Check size={16}/></motion.span>
  </motion.button>;
}

export function Reshuffle({ progress, compact, onApply, match, onOpen, showNote = true }: { progress: Progress; compact: boolean; onApply: () => void; match?: ReviewMatch; onOpen?: () => void; showNote?: boolean }) {
  const [ready, setReady] = useState(false);
  useEffect(() => { setReady(progress.get() >= .8); }, [progress]);
  useFrameProgressChange(progress, value => setReady(value >= .8));
  return <div className="fb-reshuffle-stage">
    {[2, 1, 0].map(index => <CandidateCard key={index} index={index} progress={progress} compact={compact} match={match} onOpen={onOpen} ready={ready}/>)}
    {showNote && <FeedbackNote progress={progress} compact={compact} onApply={onApply}/>}
  </div>;
}

function ResultSheet({ after, hidden, progress }: { after: boolean; hidden: boolean; progress: Progress }) {
  const person = candidates[after ? 1 : 0];
  const profileOpacity = usePhase(progress, after ? [.56, .65] : [.43, .52], after ? [0, 1] : [1, 0]);
  const evidenceOpacity = usePhase(progress, after ? [.65, .77] : [.57, .61], after ? [0, 1] : [1, 0]);
  return <div className={`fb-result-sheet ${after ? 'fb-after' : 'fb-before'}`} aria-hidden={hidden}>
    <div className="fb-sheet-toolbar"><img src={LOGO_URL} alt="HR Vision"/><span>Менеджер по работе с клиентами</span><span>{after ? <CheckCheck size={17}/> : <Video size={17}/>}</span></div>
    <div className="fb-sheet-body">
      <motion.div className="fb-sheet-profile" style={{ opacity: profileOpacity }}><div className="fb-sheet-avatar"><span>{person.initials}</span><i/><b/></div><h3>{person.name}</h3><span>{person.role}</span></motion.div>
      <motion.div className="fb-sheet-evidence" style={{ opacity: evidenceOpacity }}><span className="fb-sheet-label">Опыт в CRM</span><strong>{after ? <>Битрикс24.<br/><em>4 года.</em></> : <>amoCRM.<br/><em>Без Битрикс24.</em></>}</strong><span className="fb-sheet-status">{after ? <><Check size={16}/>С учётом вашего фидбека</> : <><Video size={16}/>Интервью проведено</>}</span></motion.div>
    </div>
  </div>;
}

// Aceternity Compare's two-layer clip-path reveal, adapted to live UI blocks.
// The glow stays on the boundary; no particle engine or independent interval.
function Compare({ progress, onApply }: { progress: Progress; onApply: () => void }) {
  const [after, setAfter] = useState(false);
  useEffect(() => setAfter(progress.get() > .67), [progress]);
  useFrameProgressChange(progress, p => setAfter(p > .67));
  const reveal = usePhase(progress, [.41, .81], [0, 1]);
  const clip = useTransform(() => `inset(0 ${(1 - reveal.get()) * 100}% 0 0)`);
  const left = useTransform(() => `${reveal.get() * 100}%`);
  const sweepOpacity = usePhase(progress, [.38, .43, .8, .84], [0, 1, 1, 0]);
  const noteOpacity = usePhase(progress, [.23, .31], [1, 0]);
  const criterionOpacity = usePhase(progress, [.3, .4], [0, 1]);
  const noteY = usePhase(progress, [.23, .31], [0, -12]);
  const criterionY = usePhase(progress, [.3, .4], [12, 0]);
  return <div className="fb-compare-stage">
    <button className="fb-compare-note" type="button" onClick={onApply} aria-label="Применить фидбек и сравнить подборку">
      <motion.span style={{ opacity: noteOpacity, y: noteY }}><MessageCircle size={20}/>«Не хватило опыта в Битрикс24»<ArrowRight size={17}/></motion.span>
      <motion.span className="fb-compare-criterion" style={{ opacity: criterionOpacity, y: criterionY }}><Check size={18}/>Опыт в Битрикс24</motion.span>
    </button>
    <div className="fb-compare-window">
      <ResultSheet after={false} hidden={after} progress={progress}/>
      <motion.div className="fb-revealed-sheet" style={{ clipPath: clip }}><ResultSheet after hidden={!after} progress={progress}/></motion.div>
      <motion.div className="fb-sweep" aria-hidden="true" style={{ left, opacity: sweepOpacity }}><i/><span/></motion.div>
    </div>
  </div>;
}

export function FeedbackConcept({ variant, control }: { variant: Exclude<FeedbackVariant, 'original' | 'graph'>; control: SceneControl }) {
  const compact = useCompact();
  const reduced = useReducedMotion();
  const progress = useTransform(() => reduced ? 1 : control.progress.get());
  return <section className={`feedback-concept fb-${variant}`}>
    <h2>Ваш фидбек меняет<br/><em>следующую подборку.</em></h2>
    {variant === 'reshuffle' ? <Reshuffle progress={progress} compact={compact} onApply={() => control.seek(.9)}/> : <Compare progress={progress} onApply={() => control.seek(.9)}/>}
  </section>;
}
