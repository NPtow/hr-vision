import { useId, useRef, type KeyboardEvent } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import { Play } from 'lucide-react';
import { skillName } from '../radar/AssessmentRadar';
import { graphConfidence, graphNumber, graphValue, type AssessmentGraphProps } from './graph-contract';
import './timeline-graph.css';

const timeLabel = (value: number) => `${Math.floor(value / 60).toString().padStart(2, '0')}:${Math.floor(value % 60).toString().padStart(2, '0')}`;
const validTime = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0;

/** A source's width is its duration. Model rating has its own numerical column. */
export function TimelineGraph({ criteria, selectedId, onSelect, onEvidence, duration }: AssessmentGraphProps) {
  const rows = criteria.filter(criterion => criterion.evidence.length > 0);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const instanceId = useId();
  const reduced = useReducedMotion();
  const lastSource = Math.max(1, ...rows.flatMap(criterion => criterion.evidence.flatMap(evidence => [evidence.seconds, evidence.endSeconds].filter(validTime))));
  const end = validTime(duration) && duration > 0 ? Math.max(duration, lastSource) : Math.ceil(lastSource / 60) * 60;
  const selected = rows.find(criterion => criterion.id === selectedId);
  const confidence = selected ? graphConfidence(selected) : null;
  const indexFor = (index: number, event: KeyboardEvent<HTMLButtonElement>) => {
    const next = event.key === 'ArrowDown' ? (index + 1) % rows.length : event.key === 'ArrowUp' ? (index + rows.length - 1) % rows.length : event.key === 'Home' ? 0 : event.key === 'End' ? rows.length - 1 : null;
    if (next !== null) { event.preventDefault(); onSelect(rows[next].id); buttons.current[next]?.focus(); }
  };
  if (!rows.length) return <p className="tlg-empty">Для этой записи пока нет источников по навыкам.</p>;
  return <section className="tlg" aria-label="Навыки и источники на временной ленте интервью">
    <div className="tlg-axis" aria-hidden="true"><span>Навык</span><span>JEV · /5</span><div>{[0, .25, .5, .75, 1].map(fraction => <time key={fraction} style={{ left: `${fraction * 100}%` }}>{timeLabel(end * fraction)}</time>)}</div></div>
    <div className="tlg-rows">{rows.map((criterion, index) => {
      const active = criterion.id === selectedId;
      const value = graphValue(criterion);
      return <div className={`tlg-row${active ? ' tlg-row-selected' : ''}`} key={criterion.id}>
        {active && <motion.div className="tlg-selection" layoutId={`timeline-selected-row-${instanceId}`} transition={{ type: 'spring', bounce: 0, duration: reduced ? 0 : .24 }} aria-hidden="true"/>}
        <button ref={element => { buttons.current[index] = element; }} className="tlg-skill" aria-pressed={active} onKeyDown={event => indexFor(index, event)} onClick={() => onSelect(criterion.id)}>{skillName(criterion)}</button>
        <span className="tlg-value" aria-label={value === null ? 'Оценка JEV отсутствует' : `Оценка JEV: ${graphNumber(value)} из 5`}>{value === null ? '—' : graphNumber(value)}</span>
        <div className="tlg-track"><span className="tlg-baseline" aria-hidden="true"/>{criterion.evidence.map((evidence, evidenceIndex) => {
          const timed = validTime(evidence.seconds) && evidence.sourceKind !== 'resume';
          const span = timed && validTime(evidence.endSeconds) && evidence.endSeconds > evidence.seconds! ? (evidence.endSeconds - evidence.seconds!) / end * 100 : 0;
          if (!timed) return <button className="tlg-untimed" key={evidence.id} onClick={() => { onSelect(criterion.id); onEvidence?.(evidence); }} title={evidence.explanation || 'Открыть источник'} style={{ top: `${evidenceIndex % 2 * 15}px` }}>Без времени</button>;
          return <button className={`tlg-source${span > 0 ? ' tlg-source-range' : ''}`} key={evidence.id} style={{ left: `${evidence.seconds! / end * 100}%`, width: span > 0 ? `max(5px, ${span}%)` : '5px' }} aria-label={`${evidence.time || timeLabel(evidence.seconds!)}. ${skillName(criterion)}. ${evidence.explanation || 'Открыть источник'}`} title={`${evidence.time || timeLabel(evidence.seconds!)} · ${evidence.explanation || skillName(criterion)}`} onClick={() => { onSelect(criterion.id); onEvidence?.(evidence); }}><span/><Play size={9} fill="currentColor" aria-hidden="true"/></button>;
        })}</div>
      </div>;
    })}</div>
    <div className="tlg-caption"><span>Фрагменты расположены по времени интервью</span>{confidence !== null && <span>Уверенность JEV: {Math.round(confidence * 100)}%</span>}</div>
  </section>;
}
