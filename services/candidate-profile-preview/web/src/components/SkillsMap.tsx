import { useId, useState } from 'react';
import { normalizeAssessmentForSkillMap, type AssessmentCriterion } from '../assessment-model';

export interface SkillsMapProps {
  assessment: unknown;
  candidateName?: string;
  selectedId?: string;
  onSelect?: (id: string) => void;
}
const wheelSkills = [
  ['A01', 'Диалог'], ['A03', 'Квалификация'], ['A04', 'Потребность'], ['A05', 'Ценность'],
  ['A06.1', 'Возражения'], ['A06.2', 'Переговоры'], ['A07', 'Следующий шаг'], ['A08', 'Воронка'],
] as const;
const colors = ['#a5b1ef', '#a9cebe', '#dec68b', '#d6add0'];
function levelOf(criterion: AssessmentCriterion | undefined) {
  if (!criterion?.selectedLevel) return null;
  const index = criterion.levels.findIndex(level => level.id === criterion.selectedLevel!.id);
  return index >= 0 ? index + 1 : null;
}
function point(radius: number, angle: number) {
  const a = (angle - 90) * Math.PI / 180;
  return { x: 260 + radius * Math.cos(a), y: 260 + radius * Math.sin(a) };
}
function wedge(inner: number, outer: number, start: number, end: number) {
  const a = point(outer, start), b = point(outer, end), c = point(inner, end), d = point(inner, start);
  return `M ${a.x} ${a.y} A ${outer} ${outer} 0 0 1 ${b.x} ${b.y} L ${c.x} ${c.y} A ${inner} ${inner} 0 0 0 ${d.x} ${d.y} Z`;
}
export function SkillsMap({ assessment, candidateName, selectedId, onSelect }: SkillsMapProps) {
  const headingId = useId();
  const [allSkills, setAllSkills] = useState(false);
  const model = normalizeAssessmentForSkillMap(assessment);
  const initials = candidateName?.trim().split(/\s+/).slice(0, 2).map(part => Array.from(part)[0]).join('').toLocaleUpperCase('ru') || '—';
  return <section className="cp-skills-map cpds-surface cpds-map-section" aria-labelledby={headingId}>
    <div className="cpds-section-heading">
      <h2 id={headingId}>Навыки</h2>
      {model.state === 'ready' && <button type="button" className="cpds-text-button" aria-expanded={allSkills} onClick={() => setAllSkills(value => !value)}>{allSkills ? 'Карта' : `Все ${model.criteria.length}`} <span aria-hidden="true">⌄</span></button>}
    </div>
    {model.state !== 'ready' ? <p className="cp-skills-empty">{model.message}</p> : allSkills ? <div className="cp-skill-list">
      {model.criteria.map(criterion => {
        const value = levelOf(criterion);
        return <button type="button" key={criterion.id} className={`cpds-skill-row${selectedId === criterion.id ? ' active' : ''}`} aria-pressed={selectedId === criterion.id} onClick={() => onSelect?.(criterion.id)}>
          <span className="cpds-skill-label"><span>{criterion.title}</span><small>{criterion.selectedLevel?.label || 'Недостаточно данных'}</small></span>
          <span className="cpds-levels" aria-label={value === null ? 'Недостаточно данных' : `Уровень ${value} из 5`}><span className="cpds-level-dots" aria-hidden="true">{[1, 2, 3, 4, 5].map(n => <i key={n} className={value !== null && n <= value ? 'filled' : ''} />)}</span><b>{value === null ? '—' : `${value}/5`}</b></span><span aria-hidden="true">›</span>
        </button>;
      })}
    </div> : <div className="cpds-wheel">
      <svg viewBox="0 0 520 520" role="group" aria-label="Карта восьми навыков продаж. Длина сектора показывает сохранённый уровень от 1 до 5. Прочерк означает отсутствие оценки.">
        {[1, 2, 3, 4, 5].map(level => <circle key={level} cx="260" cy="260" r={74 + level * 23} fill="none" stroke="#e9e7ef" strokeDasharray="2 6" />)}
        {wheelSkills.map(([id, short], i) => {
          const criterion = model.criteria.find(item => item.id === id), value = levelOf(criterion);
          const start = i * 45 + 3, end = (i + 1) * 45 - 3, p = point(225, (start + end) / 2);
          const label = `${criterion?.title || short}: ${value === null ? 'нет данных' : `${value} из 5`}`;
          return <g key={id}>
            <path className="cpds-sector" d={wedge(74, 189, start, end)} fill="#f3f1f7" stroke={selectedId === id ? '#5b46bc' : 'transparent'} strokeWidth="3" role="button" tabIndex={0} aria-label={label} aria-pressed={selectedId === id} onClick={() => onSelect?.(id)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect?.(id); } }}><title>{label}</title></path>
            {value !== null && <path d={wedge(74, 74 + value * 23, start, end)} fill={colors[Math.floor(i / 2)]} pointerEvents="none" />}
            <text x={p.x} y={p.y - 3} textAnchor="middle" fill="#685e7a" fontSize="12" fontWeight="500" pointerEvents="none">{short}</text>
            <text x={p.x} y={p.y + 15} textAnchor="middle" fill="#342c43" fontSize="14" fontWeight="650" pointerEvents="none">{value ?? '—'}</text>
          </g>;
        })}
        <circle cx="260" cy="260" r="62" fill="#eee7f5" />
        <text x="260" y="267" textAnchor="middle" fontSize="33" fill="#74638b" fontWeight="500">{initials}</text>
        <text x="260" y="291" textAnchor="middle" fontSize="10" fill="#74638b">ПРОФИЛЬ НАВЫКОВ</text>
      </svg>
      <p><span className="cpds-legend-dot" />Уровень из 5<span>Нажми на сектор</span></p>
    </div>}
  </section>;
}
export default SkillsMap;
