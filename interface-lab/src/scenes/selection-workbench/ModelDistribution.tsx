import { createContext, useContext, useId, useState, type ReactNode } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import { ChevronDown } from 'lucide-react';
import { probabilityLabel, type DistributionData } from './distribution-data';
import './model-distribution.css';
export const DistributionMotionDuration = createContext(.3);

export function SkillDisclosure({ name, summary, children }: { name: string; summary: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return <details className="md-skill-disclosure" name={name} onToggle={e => setOpen(e.currentTarget.open)}><summary>{summary}<ChevronDown size={20}/></summary>{open && children}</details>;
}

/** Operational detail inside the existing workbench. Inter, neutral surfaces and
 * purple accent are inherited product tokens. No new shell, auto-tour or scoring.
 * One animated property (bar scale) helps compare consecutive levels/skills.
 */
export function ModelDistribution({ title, data, requiredLevel, headingLevel = 3 }: { title: string; data: DistributionData; requiredLevel?: string | null; headingLevel?: 3 | 4 }) {
  const [reading, setReading] = useState<string | null>(null);
  const id = useId(), Title = headingLevel === 4 ? 'h4' : 'h3';
  const reduced = useReducedMotion(), duration = useContext(DistributionMotionDuration);
  const selected = data.options.find(option => option.id === reading);
  const missing = data.options.every(option => option.probability === null);
  return <section className="md-distribution" aria-label={title}>
    <Title className="md-distribution-title">{title}</Title>
    {missing ? <p className="md-unavailable">Распределение не сохранено в этом результате.</p> : <div className="md-options">
      {data.options.map(option => <button type="button" key={option.id} className="md-option"
        data-answer={data.choice === option.id} data-unknown={option.unknown || undefined}
        aria-label={`${option.label}: ${probabilityLabel(option.probability)}${option.id === data.choice ? ', ответ модели' : ''}${option.id === requiredLevel ? ', требуется для вакансии' : ''}`}
        aria-expanded={reading === option.id} aria-controls={reading === option.id ? id : undefined}
        onClick={() => setReading(current => current === option.id ? null : option.id)}>
        <span className="md-option-label"><span>{option.label}{option.id === requiredLevel && <small className="md-required">Требуется</small>}{data.choice === option.id && <span className="md-dot" aria-hidden="true"/>}</span><strong>{probabilityLabel(option.probability)}</strong></span>
        {option.probability !== null && <span className="md-track" aria-hidden="true"><motion.span className="md-fill" initial={{ scaleX: reduced ? option.probability : 0 }} animate={{ scaleX: option.probability }} transition={{ duration: reduced ? 0 : duration, ease: [.2, 0, 0, 1] }}/></span>}
      </button>)}
    </div>}
    {selected && <div id={id} className="md-description"><strong>{selected.label}</strong><p>{selected.description || (selected.unknown ? 'Этот вариант означает, что в материалах недостаточно оснований для содержательного ответа.' : 'Для этого варианта отдельное описание не сохранено.')}</p></div>}
    {!missing && <footer className="md-distribution-foot"><span>{data.choice ? <><i/>Ответ модели</> : 'Исходные вероятности'}</span>{data.confidence !== null && <span>Уверенность: {probabilityLabel(data.confidence)}</span>}</footer>}
  </section>;
}
