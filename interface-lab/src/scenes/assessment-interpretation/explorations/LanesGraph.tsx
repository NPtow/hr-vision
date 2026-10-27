import { useId, useRef, type CSSProperties, type KeyboardEvent } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { ArrowUpRight, Play } from 'lucide-react';
import { skillName } from '../radar/AssessmentRadar';
import { graphConfidence, graphNumber, graphValue, type AssessmentGraphProps } from './graph-contract';
import './lanes-graph.css';

const laneColors = ['#7460c4', '#8870bd', '#6b82b7', '#5d969b', '#9b8a61', '#ab7b90', '#8271a1'];
const confidenceNumber = (value: number) => new Intl.NumberFormat('ru-RU', { style: 'percent', maximumFractionDigits: 0 }).format(value);

/** A score is the length of a lane; confidence has its own explicit text label. */
export function LanesGraph({ criteria, selectedId, onSelect, onEvidence }: AssessmentGraphProps) {
  const visible = criteria.filter(criterion => criterion.evidence.length > 0);
  const activeId = visible.some(criterion => criterion.id === selectedId) ? selectedId : visible[0]?.id;
  const active = visible.find(criterion => criterion.id === activeId);
  const activeConfidence = active ? graphConfidence(active) : null;
  const reduced = useReducedMotion();
  const instance = useId();
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const spring = reduced ? { duration: 0 } : { type: 'spring' as const, bounce: 0, duration: 0.42 };

  function navigate(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    let next: number;
    switch (event.key) {
      case 'ArrowUp': case 'ArrowLeft': next = (index - 1 + visible.length) % visible.length; break;
      case 'ArrowDown': case 'ArrowRight': next = (index + 1) % visible.length; break;
      case 'Home': next = 0; break;
      case 'End': next = visible.length - 1; break;
      default: return;
    }
    event.preventDefault();
    const criterion = visible[next];
    if (!criterion) return;
    onSelect(criterion.id);
    buttons.current.get(criterion.id)?.focus({ preventScroll: true });
    buttons.current.get(criterion.id)?.scrollIntoView({ block: 'nearest', behavior: reduced ? 'instant' : 'smooth' });
  }

  if (!visible.length) return <div className="lanes-graph lanes-graph--empty">Нет фрагментов для отображения навыков.</div>;

  return <section className="lanes-graph" aria-label="Оценки навыков по материалам интервью">
    <header className="lanes-graph__header">
      <span>Навыки</span>
      <span>Оценка <span className="lanes-graph__scale-label">1–5</span></span>
    </header>
    <ol className="lanes-graph__list" aria-label="Выберите навык. Стрелки переключают навыки.">
      <AnimatePresence initial={false}>
        {visible.map((criterion, index) => {
          const selected = criterion.id === activeId;
          const value = graphValue(criterion);
          const confidence = graphConfidence(criterion);
          const directEstimate = criterion.modelEstimate?.value;
          const isModelEstimate = typeof directEstimate === 'number' && Number.isFinite(directEstimate) && directEstimate >= 1 && directEstimate <= 5;
          const label = skillName(criterion);
          const colorIndex = Array.from(criterion.id).reduce((sum, char) => sum + char.charCodeAt(0), 0) % laneColors.length;
          const style = { '--lane-color': laneColors[colorIndex] } as CSSProperties;
          return <motion.li className="lanes-graph__item" key={criterion.id} style={style}
            layout={!reduced}
            initial={{ opacity: 0, y: reduced ? 0 : 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 0 }}
            transition={spring}>
            <button type="button" className={`lanes-graph__lane${selected ? ' is-selected' : ''}`}
              ref={node => { if (node) buttons.current.set(criterion.id, node); else buttons.current.delete(criterion.id); }}
              aria-pressed={selected} tabIndex={selected ? 0 : -1}
              aria-label={`${label}. ${value === null ? 'Оценка отсутствует' : `${isModelEstimate ? 'Оценка JEV' : 'Уровень'} ${graphNumber(value)} из 5`}.${confidence === null ? '' : ` Уверенность JEV ${confidenceNumber(confidence)}.`} Открыть основания.`}
              onKeyDown={event => navigate(event, index)} onClick={() => onSelect(criterion.id)}>
              {selected && <motion.span className="lanes-graph__selection" layoutId={`lane-selection-${instance}`}
                transition={spring} aria-hidden="true"/>}
              <span className="lanes-graph__row">
                <span className="lanes-graph__name">{label}</span>
                <span className="lanes-graph__value">{value === null ? '—' : graphNumber(value)}<span>/ 5</span></span>
              </span>
              <span className="lanes-graph__track" aria-hidden="true">
                <span className="lanes-graph__grid"/>
                {value !== null && <motion.span className="lanes-graph__fill" initial={false}
                  animate={{ scaleX: value / 5 }} transition={spring}/>}
              </span>
            </button>
          </motion.li>;
        })}
      </AnimatePresence>
    </ol>
    {active && <div className="lanes-graph__meta">
      <span>{active.evidence.length} {active.evidence.length === 1 ? 'фрагмент' : active.evidence.length < 5 ? 'фрагмента' : 'фрагментов'} по выбранному навыку</span>
      <span title="Уверенность в выборе оценки моделью — отдельный показатель, не уровень навыка и не проверенная точность.">{activeConfidence === null ? 'Уверенность не указана' : `Уверенность JEV ${confidenceNumber(activeConfidence)}`}</span>
    </div>}
    {onEvidence && active && <div className="lanes-graph__sources" aria-label={`Источники: ${skillName(active)}`}>
      <span className="lanes-graph__sources-label">Проверить по записи</span>
      <div className="lanes-graph__source-list">
        {active.evidence.map((evidence, index) => {
          const hasVideo = evidence.sourceKind !== 'resume' && evidence.seconds !== null && Number.isFinite(evidence.seconds) && evidence.seconds >= 0 && evidence.availability !== 'text_only' && evidence.availability !== 'unavailable';
          return <button type="button" key={`${active.id}:${evidence.id}`} disabled={evidence.availability === 'unavailable'}
            onClick={() => onEvidence(evidence)}
            aria-label={`Открыть ${hasVideo ? 'фрагмент записи' : 'реплику'} ${evidence.time || index + 1}`}>
            {hasVideo ? <Play size={11} fill="currentColor" aria-hidden="true"/> : <ArrowUpRight size={12} aria-hidden="true"/>}
            <span>{hasVideo ? evidence.time || 'К фрагменту' : `Реплика ${index + 1}`}</span>
          </button>;
        })}
      </div>
    </div>}
  </section>;
}
