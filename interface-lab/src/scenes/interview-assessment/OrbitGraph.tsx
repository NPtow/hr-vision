import { skillName } from './graph-contract';
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { motion, useReducedMotion, useSpring, useTransform } from 'motion/react';
import { graphNumber, graphValue, type AssessmentGraphProps } from './graph-contract';
import './orbit-graph.css';

const WIDTH = 400;
const HEIGHT = 360;
const CENTER = { x: 200, y: 180 };
const INNER_RADIUS = 48;
const OUTER_RADIUS = 120;
const LABEL_RADIUS = 151;
const wheelLabels: Record<string, string> = {
  A01: 'Диалог', A03: 'Квалификация', A04: 'Задача', A07: 'Следующий шаг',
  A08: 'CRM', A10: 'Продукт', 'NEW-01': 'Передача',
  'A06.1': 'Возражения', 'A11.1': 'Адаптация',
  A05: 'Ценность', 'A06.2': 'Условия',
};

function point(angle: number, radius: number) {
  const radians = angle * Math.PI / 180;
  return { x: CENTER.x + Math.cos(radians) * radius, y: CENTER.y + Math.sin(radians) * radius };
}

function nearestAngle(current: number, target: number) {
  return current + ((target - current) % 360 + 540) % 360 - 180;
}

const scoreRadius = (value: number) => INNER_RADIUS + (value - 1) / 4 * (OUTER_RADIUS - INNER_RADIUS);

/** Rotation chooses a skill; only the radial length encodes its score. */
export function OrbitGraph({ criteria, selectedId, onSelect, onEvidence }: AssessmentGraphProps) {
  const available = criteria.filter(criterion => criterion.state !== 'not_applicable');
  const activeIndex = Math.max(0, available.findIndex(criterion => criterion.id === selectedId));
  const active = available[activeIndex];
  const value = active ? graphValue(active) : null;
  const [sourceId, setSourceId] = useState<string | null>(null);
  const sourceIndex = Math.max(0, active?.evidence.findIndex(evidence => evidence.id === sourceId) ?? 0);
  const source = active?.evidence[sourceIndex];
  const reduced = useReducedMotion();
  const rotation = useSpring(0, { stiffness: 280, damping: 34, mass: 0.85 });
  const counterRotation = useTransform(rotation, angle => -angle);
  const controls = useRef<(HTMLButtonElement | null)[]>([]);
  const count = available.length;
  const compact = count > 7;
  const selectionKey = available.map(criterion => criterion.id).join('|');

  useEffect(() => {
    if (!count) return;
    if (reduced) rotation.jump(0);
    else rotation.set(nearestAngle(rotation.get(), -activeIndex * 360 / count));
  }, [activeIndex, count, reduced, rotation, selectionKey]);

  function moveSelection(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next: number;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % count;
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index + count - 1) % count;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = count - 1;
    else return;
    event.preventDefault();
    onSelect(available[next].id);
    controls.current[next]?.focus({ preventScroll: true });
  }

  if (!active) return <div className="ia-orbit ia-orbit--empty"><p>Пока нет фрагментов для анализа навыков.</p></div>;

  const explanation = source?.explanation?.trim() || active.summary.trim() || source?.text || active.missingReason || 'В интервью недостаточно данных для оценки этого навыка.';
  const chooseSource = (index: number) => {
    const next = active.evidence[index];
    if (next) setSourceId(next.id);
  };

  return <section className={`ia-orbit${compact ? ' ia-orbit--compact' : ''}`} data-count={count} aria-label="Колесо навыков по фрагментам интервью">
    <div className="ia-orbit__body">
      <div className="ia-orbit__wheel">
        <svg className="ia-orbit__grid" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} aria-hidden="true">
          {[1, 2, 3, 4, 5].map(level => <circle key={level} cx={CENTER.x} cy={CENTER.y} r={scoreRadius(level)} />)}
        </svg>
        <motion.div className="ia-orbit__rotor" style={{ rotate: rotation }}>
          <svg className="ia-orbit__rays" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} aria-hidden="true">
            {available.map((criterion, index) => {
              const angle = -90 + index * 360 / count;
              const start = point(angle, INNER_RADIUS);
              const end = point(angle, OUTER_RADIUS);
              const score = graphValue(criterion);
              const scored = score === null ? null : point(angle, scoreRadius(score));
              return <g className={`ia-orbit__axis${index === activeIndex ? ' is-selected' : ''}${score === null ? ' is-unscored' : ''}`} key={criterion.id}>
                <line className="ia-orbit__track" x1={start.x} y1={start.y} x2={end.x} y2={end.y} />
                {scored && <>
                  <line className="ia-orbit__value" x1={start.x} y1={start.y} x2={scored.x} y2={scored.y} />
                  <circle className="ia-orbit__point" cx={scored.x} cy={scored.y} r={index === activeIndex ? 4.5 : 3.5} />
                </>}
              </g>;
            })}
          </svg>
          <div className="ia-orbit__controls" role="group" aria-label="Выберите навык. Стрелки переключают навыки.">
            {available.map((criterion, index) => {
              const position = point(-90 + index * 360 / count, LABEL_RADIUS);
              const score = graphValue(criterion);
              const label = wheelLabels[criterion.id] ?? skillName(criterion);
              const selected = index === activeIndex;
              return <div className="ia-orbit__anchor" style={{ left: `${position.x / WIDTH * 100}%`, top: `${position.y / HEIGHT * 100}%` }} key={criterion.id}>
                <motion.button type="button" className={`ia-orbit__skill${selected ? ' is-selected' : ''}`}
                  style={{ rotate: counterRotation }} ref={element => { controls.current[index] = element; }}
                  tabIndex={selected ? 0 : -1} aria-pressed={selected} title={skillName(criterion)}
                  aria-label={`${skillName(criterion)}. ${score === null ? 'Без числовой оценки' : `Балл ${graphNumber(score)} из 5`}. Открыть объяснение.`}
                  onClick={() => onSelect(criterion.id)} onKeyDown={event => moveSelection(event, index)}>
                  {compact ? <span className="ia-orbit__node" aria-hidden="true" /> : <span>{label}</span>}
                </motion.button>
              </div>;
            })}
          </div>
        </motion.div>
        <div className="ia-orbit__center" aria-live="polite" aria-atomic="true" title={active.modelEstimate ? 'Оценка модели по источникам интервью' : 'Уровень навыка по источникам интервью'}>
          <strong>{value === null ? '—' : graphNumber(value)}</strong>
          <span>{value === null ? 'Без балла' : 'из 5'}</span>
        </div>
      </div>
      <div className="ia-orbit__summary">
        {compact ? <select className="ia-orbit__skill-select" aria-label="Выбранный навык" value={active.id} onChange={event => onSelect(event.target.value)}>
          {available.map(criterion => <option key={criterion.id} value={criterion.id}>{skillName(criterion)}</option>)}
        </select> : <h3>{skillName(active)}</h3>}
        <div className="ia-orbit__explanation" aria-live="polite" aria-atomic="true">
          <motion.p key={`${active.id}:${source?.id || 'unassessed'}`} initial={reduced ? false : { opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: reduced ? 0 : 0.14 }}>{explanation}</motion.p>
        </div>
        <div className="ia-orbit__footer">
          {active.evidence.length > 1 && <nav className="ia-orbit__pages" aria-label="Фрагменты выбранного навыка">
            <button type="button" aria-label="Предыдущий фрагмент" disabled={sourceIndex === 0} onClick={() => chooseSource(sourceIndex - 1)}>
              <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m10 4-4 4 4 4"/></svg>
            </button>
            <span aria-label={`Фрагмент ${sourceIndex + 1} из ${active.evidence.length}`}>{sourceIndex + 1}<span aria-hidden="true"> / </span>{active.evidence.length}</span>
            <button type="button" aria-label="Следующий фрагмент" disabled={sourceIndex === active.evidence.length - 1} onClick={() => chooseSource(sourceIndex + 1)}>
              <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m6 4 4 4-4 4"/></svg>
            </button>
          </nav>}
          {onEvidence && source && source.sourceKind !== 'resume' && source.availability !== 'unavailable' && source.availability !== 'text_only' && source.seconds !== null && Number.isFinite(source.seconds) && source.seconds >= 0 ? <button type="button" className="ia-orbit__source" aria-label={`Открыть фрагмент ${source.time || 'в расшифровке'}`} onClick={() => onEvidence(source, active.id)}>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.5 12 8l-7 4.5Z"/></svg>
            <span>{source.time || 'К источнику'}</span>
          </button> : <span className="ia-orbit__source-time">{source ? source.sourceKind === 'resume' ? 'Резюме' : source.time || 'Без таймкода' : 'Нет подтверждённого фрагмента'}</span>}
        </div>
      </div>
    </div>
  </section>;
}
