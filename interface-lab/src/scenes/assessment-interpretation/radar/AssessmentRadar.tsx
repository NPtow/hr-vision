import { useRef, type KeyboardEvent } from 'react';
import type { AssessmentCriterion } from '../assessment-view';
import './assessment-radar.css';

export type AssessmentRadarProps = {
  criteria: AssessmentCriterion[];
  selectedId: string;
  onSelect: (id: string) => void;
};

const WIDTH = 640;
const HEIGHT = 430;
const CENTER = { x: 320, y: 215 };
const RADIUS = 128;
const LABEL_RADIUS = 186;
const aliases: Record<string, string> = {
  A01: 'Диалог и слушание',
  A03: 'Квалификация',
  A08: 'CRM и сопровождение',
  'NEW-01': 'Передача лида',
  A10: 'Продукт и обещания',
  A07: 'Следующий шаг',
  A04: 'Понимание задачи',
  'A06.1': 'Работа с возражениями',
  'A11.1': 'Гибкость в разговоре',
};

export const skillName = (criterion: Pick<AssessmentCriterion, 'id' | 'label'>) => aliases[criterion.id] || criterion.label;

function point(index: number, count: number, radius: number) {
  const angle = -Math.PI / 2 + index * 2 * Math.PI / count;
  return { x: CENTER.x + Math.cos(angle) * radius, y: CENTER.y + Math.sin(angle) * radius };
}

/** Absence of an observation has no numerical position and never closes a scored polygon. */
function availableLevel(criterion: AssessmentCriterion) {
  return criterion.state === 'supported'
    && typeof criterion.level === 'number' && Number.isFinite(criterion.level)
    && criterion.level >= 1 && criterion.level <= 5 ? criterion.level : null;
}

export function AssessmentRadar({ criteria, selectedId, onSelect }: AssessmentRadarProps) {
  const controls = useRef<(HTMLButtonElement | null)[]>([]);
  const count = criteria.length;
  const levels = criteria.map(availableLevel);
  const hasMissing = levels.some(level => level === null);
  const allKnown = count >= 3 && !hasMissing;
  const dataPoints = levels.map((level, index) => level === null ? null : point(index, count, RADIUS * level / 5));
  const polygon = (radius: number) => criteria.map((_, index) => {
    const position = point(index, count, radius);
    return `${position.x},${position.y}`;
  }).join(' ');

  function moveSelection(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next = index;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % count;
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index + count - 1) % count;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = count - 1;
    else return;
    event.preventDefault();
    onSelect(criteria[next].id);
    controls.current[next]?.focus();
  }

  if (!count) return <p className="asr-empty">Для профиля пока нет критериев.</p>;

  return <div className="asr" aria-label="Качества по материалам интервью">
    <svg className="asr-chart" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} aria-hidden="true" focusable="false">
      {[1, 2, 3, 4, 5].map(level => <polygon className="asr-grid-ring" key={level} points={polygon(RADIUS * level / 5)}/>)}
      {criteria.map((criterion, index) => {
        const end = point(index, count, RADIUS);
        const selected = selectedId === criterion.id;
        const unknown = levels[index] === null;
        return <g key={criterion.id} className={`asr-axis${selected ? ' asr-axis-selected' : ''}${unknown ? ' asr-axis-unknown' : ''}`} onClick={() => onSelect(criterion.id)}>
          <line className="asr-axis-hit" x1={CENTER.x} y1={CENTER.y} x2={end.x} y2={end.y}/>
          <line className="asr-axis-line" x1={CENTER.x} y1={CENTER.y} x2={end.x} y2={end.y}/>
        </g>;
      })}
      {allKnown && <polygon className="asr-data-fill" points={dataPoints.map(position => `${position!.x},${position!.y}`).join(' ')}/>}
      {criteria.map((criterion, index) => {
        const start = dataPoints[index];
        const end = dataPoints[(index + 1) % count];
        return start && end && count >= 3 ? <line key={criterion.id} className="asr-data-line" x1={start.x} y1={start.y} x2={end.x} y2={end.y}/> : null;
      })}
      {dataPoints.map((position, index) => position && <g key={criteria[index].id} className={`asr-point${selectedId === criteria[index].id ? ' asr-point-selected' : ''}`} onClick={() => onSelect(criteria[index].id)}>
        <circle className="asr-point-hit" cx={position.x} cy={position.y} r="18"/>
        <circle className="asr-point-halo" cx={position.x} cy={position.y} r="10"/>
        <circle className="asr-point-dot" cx={position.x} cy={position.y} r="4.5"/>
      </g>)}
      <circle className="asr-center" cx={CENTER.x} cy={CENTER.y} r="2"/>
    </svg>
    <div className="asr-axis-controls" role="group" aria-label="Выберите качество. Стрелки переключают между осями.">
      {criteria.map((criterion, index) => {
        const position = point(index, count, LABEL_RADIUS);
        const level = levels[index];
        const selected = selectedId === criterion.id;
        const label = skillName(criterion);
        return <button key={criterion.id} ref={element => { controls.current[index] = element; }} type="button"
          className={`asr-axis-label${selected ? ' asr-label-selected' : ''}${level === null ? ' asr-label-unknown' : ''}`}
          style={{ left: `${position.x / WIDTH * 100}%`, top: `${position.y / HEIGHT * 100}%` }}
          aria-pressed={selected}
          aria-label={`${criterion.label}. ${level === null ? 'Нет данных' : `Уровень ${level} из 5`}. Открыть основания.`}
          onClick={() => onSelect(criterion.id)} onKeyDown={event => moveSelection(event, index)}>
          <span className="asr-label-name">{label}</span>{level !== null && <span className="asr-label-level">{new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(level)} / 5</span>}
        </button>;
      })}
    </div>
  </div>;
}
