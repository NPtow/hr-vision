import { useEffect, useId, useRef, type KeyboardEvent } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import { skillName } from '../radar/AssessmentRadar';
import { graphConfidence, graphNumber, graphValue, type AssessmentGraphProps } from './graph-contract';
import './petals-graph.css';

const CENTER = 180;
const INNER = 43;
const OUTER = 156;
const scoreRadius = (value: number) => 76 + (value - 1) * 20;

/** Every petal uses the same radial scale; area has no separate meaning. */
function petalPath(angle: number, end: number, width: number) {
  const length = end - INNER;
  const point = (radius: number, offset: number) => {
    const x = CENTER + Math.cos(angle) * radius - Math.sin(angle) * offset;
    const y = CENTER + Math.sin(angle) * radius + Math.cos(angle) * offset;
    return `${x.toFixed(3)} ${y.toFixed(3)}`;
  };
  return `M ${point(INNER, 0)} C ${point(INNER + length * .22, -width)} ${point(INNER + length * .81, -width)} ${point(end, 0)} C ${point(INNER + length * .81, width)} ${point(INNER + length * .22, width)} ${point(INNER, 0)} Z`;
}

export function PetalsGraph({ criteria, selectedId, onSelect }: AssessmentGraphProps) {
  const visible = criteria.filter(criterion => criterion.evidence.length > 0);
  const active = visible.find(criterion => criterion.id === selectedId) ?? visible[0];
  const value = active ? graphValue(active) : null;
  const confidence = active ? graphConfidence(active) : null;
  const reduced = useReducedMotion();
  const gradientId = `petals-${useId().replace(/:/g, '')}`;
  const list = useRef<HTMLDivElement>(null);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const activeIndex = visible.findIndex(criterion => criterion.id === active?.id);
  const hasUnknown = visible.some(criterion => graphValue(criterion) === null);
  const transition = reduced ? { duration: 0 } : { type: 'spring' as const, duration: .38, bounce: 0 };

  // Keep only this list in view; a selection must not move the interview page.
  useEffect(() => {
    const container = list.current;
    const button = buttons.current[activeIndex];
    if (!container || !button) return;
    const bounds = container.getBoundingClientRect();
    const item = button.getBoundingClientRect();
    if (item.top < bounds.top) container.scrollTop -= bounds.top - item.top;
    else if (item.bottom > bounds.bottom) container.scrollTop += item.bottom - bounds.bottom;
  }, [active?.id, activeIndex]);

  function navigate(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next: number;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % visible.length;
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index - 1 + visible.length) % visible.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = visible.length - 1;
    else return;
    event.preventDefault();
    onSelect(visible[next].id);
    buttons.current[next]?.focus({ preventScroll: true });
  }

  if (!visible.length) return <p className="petals-empty">Пока нет фрагментов для карты навыков.</p>;

  return <section className="petals-graph" aria-label="Лепестковая карта навыков">
    <div className="petals-layout">
      <div className="petals-visual">
        <div className="petals-flower">
          <svg className="petals-svg" viewBox="0 0 360 360" aria-hidden="true" focusable="false">
            <defs>
              <radialGradient id={gradientId} cx="50%" cy="50%" r="50%">
                <stop offset="12%" stopColor="#e1d8f5"/>
                <stop offset="100%" stopColor="#9275c7"/>
              </radialGradient>
            </defs>
            {[1, 3, 5].map(level => <circle key={level} className="petals-guide" cx={CENTER} cy={CENTER} r={scoreRadius(level)}/>)}
            {visible.map((criterion, index) => {
              const angle = -Math.PI / 2 + index * Math.PI * 2 / visible.length;
              const estimate = graphValue(criterion);
              const selected = criterion.id === active?.id;
              const width = Math.min(34, 100 * Math.sin(Math.PI / Math.max(visible.length, 3)) * .72);
              const end = estimate === null ? OUTER : scoreRadius(estimate);
              const filledWidth = width * Math.sqrt((end - INNER) / (OUTER - INNER));
              const numberX = CENTER + Math.cos(angle) * 174;
              const numberY = CENTER + Math.sin(angle) * 174;
              return <g key={criterion.id} className={`petals-petal${selected ? ' petals-petal-selected' : ''}${estimate === null ? ' petals-petal-unknown' : ''}`} onClick={() => onSelect(criterion.id)}>
                <path className="petals-track" d={petalPath(angle, OUTER, width)}/>
                {estimate !== null && <motion.path className="petals-value" initial={false}
                  animate={{ d: petalPath(angle, end, filledWidth), opacity: selected ? 1 : .7 }}
                  transition={transition} fill={`url(#${gradientId})`}/>}
                <motion.path className="petals-outline" initial={false}
                  animate={{ d: petalPath(angle, end, filledWidth), opacity: selected ? 1 : 0 }}
                  transition={transition}/>
                {selected && estimate !== null && <circle className="petals-tip" cx={CENTER + Math.cos(angle) * end} cy={CENTER + Math.sin(angle) * end} r="3"/>}
                <text className="petals-index" x={numberX} y={numberY} textAnchor="middle" dominantBaseline="central">{String(index + 1).padStart(2, '0')}</text>
              </g>;
            })}
          </svg>
          <div className="petals-center" aria-hidden="true">
            <motion.div key={`${active.id}:${value}`} initial={{ opacity: reduced ? 1 : .25 }} animate={{ opacity: 1 }} transition={{ duration: reduced ? 0 : .18 }}>
              <span className="petals-center-index">{String(activeIndex + 1).padStart(2, '0')}</span>
              <strong>{value === null ? '—' : graphNumber(value)}</strong>
              <span className="petals-center-unit">{value === null ? 'без балла' : 'из 5'}</span>
            </motion.div>
          </div>
        </div>
        <div className="petals-reading" aria-live="polite" aria-atomic="true">
          <p className="petals-meaning">Длина лепестка — балл из 5</p>
          {confidence !== null ? <p className="petals-confidence">Уверенность JEV <strong>{graphNumber(confidence * 100)}%</strong></p> : <p className="petals-confidence">{value === null ? 'Числовой оценки пока нет' : 'Оценка по материалам интервью'}</p>}
          {hasUnknown && <p className="petals-unknown-key"><span/>Пунктир — без балла</p>}
        </div>
      </div>
      <div ref={list} className="petals-list" role="group" aria-label="Навыки. Стрелки переключают выбранный навык.">
        {visible.map((criterion, index) => {
          const estimate = graphValue(criterion);
          const selected = criterion.id === active?.id;
          return <button key={criterion.id} ref={element => { buttons.current[index] = element; }} type="button"
            className={`petals-choice${selected ? ' petals-choice-selected' : ''}`}
            aria-pressed={selected} tabIndex={selected ? 0 : -1}
            aria-label={`${skillName(criterion)}. ${estimate === null ? 'Без числовой оценки' : `Балл ${graphNumber(estimate)} из 5`}. Открыть объяснение.`}
            onClick={() => onSelect(criterion.id)} onKeyDown={event => navigate(event, index)}>
            <span className="petals-choice-index" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
            <span className="petals-choice-name">{skillName(criterion)}</span>
            <span className={`petals-choice-value${estimate === null ? ' petals-choice-value-empty' : ''}`} aria-hidden="true">{estimate === null ? '—' : graphNumber(estimate)}</span>
          </button>;
        })}
      </div>
    </div>
  </section>;
}
