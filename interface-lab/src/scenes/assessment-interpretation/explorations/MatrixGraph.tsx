import { useEffect, useId, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react';
import { LayoutGroup, motion, useReducedMotion } from 'motion/react';
import { ChevronLeft, ChevronRight, ChevronUp, Play } from 'lucide-react';
import { skillName } from '../radar/AssessmentRadar';
import { graphNumber, graphValue, type AssessmentGraphProps } from './graph-contract';
import './matrix-graph.css';

const vacancySpring = { type: 'spring' as const, stiffness: 380, damping: 38, mass: .8 };
const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;

/** One stable row per skill. The active row reveals its own explanation and sources. */
export function MatrixGraph({ criteria, selectedId, onSelect, onEvidence }: AssessmentGraphProps) {
  const visible = useMemo(() => criteria.filter(criterion => criterion.evidence.length > 0), [criteria]);
  const [expandedId, setExpandedId] = useState<string | null>(() => visible.find(criterion => criterion.id === selectedId)?.id ?? visible[0]?.id ?? null);
  const [sourceIds, setSourceIds] = useState<Record<string, string>>({});
  const previousSelection = useRef(selectedId);
  const controls = useRef(new Map<string, HTMLButtonElement>());
  const id = useId();
  const reduced = useReducedMotion();
  const active = visible.find(criterion => criterion.id === expandedId);

  useEffect(() => {
    if (previousSelection.current !== selectedId) {
      previousSelection.current = selectedId;
      setExpandedId(visible.find(criterion => criterion.id === selectedId)?.id ?? visible[0]?.id ?? null);
    } else if (expandedId && !visible.some(criterion => criterion.id === expandedId)) {
      setExpandedId(visible[0]?.id ?? null);
    }
  }, [selectedId, visible, expandedId]);

  function collapse() {
    const control = expandedId ? controls.current.get(expandedId) : undefined;
    setExpandedId(null);
    control?.focus({ preventScroll: true });
  }

  function moveFocus(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    let next = index;
    if (event.key === 'ArrowDown') next = Math.min(index + 1, visible.length - 1);
    else if (event.key === 'ArrowUp') next = Math.max(index - 1, 0);
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = visible.length - 1;
    else return;
    event.preventDefault();
    controls.current.get(visible[next].id)?.focus({ preventScroll: true });
  }

  if (!visible.length) return <p className="amx-empty">Пока нет материалов для оценки навыков.</p>;

  return <LayoutGroup id={id}><section className="amx-graph" aria-label="Оценка навыков по материалам интервью" data-expanded={!!active}
    onKeyDown={event => { if (event.key === 'Escape' && active && !event.defaultPrevented) { event.preventDefault(); collapse(); } }}>
    <div className="amx-board">
      {visible.map((criterion, index) => {
        const open = active?.id === criterion.id;
        const value = graphValue(criterion);
        const name = skillName(criterion);
        const sourceIndex = Math.max(0, criterion.evidence.findIndex(source => source.id === sourceIds[criterion.id]));
        const source = criterion.evidence[sourceIndex];
        const canPlay = source.sourceKind !== 'resume' && source.availability !== 'text_only' && source.availability !== 'unavailable' && source.seconds !== null && Number.isFinite(source.seconds) && source.seconds >= 0;
        const time = canPlay ? source.time || clock(source.seconds!) : '';
        const explanation = source.explanation?.trim() || criterion.summary?.trim() || 'Откройте фрагмент, чтобы посмотреть ответ кандидата в контексте.';
        const titleId = `${id}-${criterion.id}-title`;
        const bodyId = `${id}-${criterion.id}-body`;
        const changeSource = (sourceNumber: number) => setSourceIds(previous => ({ ...previous, [criterion.id]: criterion.evidence[sourceNumber].id }));

        return <motion.article key={criterion.id} className={`amx-card${open ? ' is-open' : ''}`} aria-labelledby={titleId}
          layout={!reduced} initial={false} transition={{ layout: reduced ? { duration: 0 } : vacancySpring }} style={{ borderRadius: 12, '--amx-tone': value === null ? 0 : value * .02 } as CSSProperties}>
          <div className="amx-card-face">
            <motion.button id={titleId} type="button" className="amx-card-trigger" layout={reduced ? false : 'position'} aria-expanded={open} aria-controls={bodyId}
              aria-label={`${name}. ${value === null ? 'Оценки пока нет' : `Оценка ${graphNumber(value)} из 5`}. ${open ? 'Свернуть' : 'Открыть объяснение'}.`}
              ref={node => { if (node) controls.current.set(criterion.id, node); else controls.current.delete(criterion.id); }}
              onClick={() => { if (open) collapse(); else { setExpandedId(criterion.id); onSelect(criterion.id); } }}
              onKeyDown={event => moveFocus(event, index)}>
              <span className="amx-card-name">{name}</span>
              <span className="amx-score" aria-hidden="true">{value === null ? '—' : <>{graphNumber(value)}<small>/5</small></>}</span>
              {open && <ChevronUp className="amx-collapse-mark" size={16} aria-hidden="true"/>}
            </motion.button>
            {open && <motion.div className="amx-card-body" id={bodyId} initial={{ opacity: reduced ? 1 : 0 }} animate={{ opacity: 1 }} transition={{ duration: reduced ? 0 : .18, delay: reduced ? 0 : .08 }}>
              <motion.p className="amx-explanation" key={source.id} initial={{ opacity: reduced ? 1 : 0 }} animate={{ opacity: 1 }} transition={{ duration: reduced ? 0 : .18 }} aria-live="polite" aria-atomic="true">{explanation}</motion.p>
              <div className="amx-card-actions">
                {criterion.evidence.length > 1 && <nav className="amx-sources" aria-label={`Источники по навыку «${name}»`}>
                  <button type="button" onClick={() => changeSource(sourceIndex - 1)} disabled={sourceIndex === 0} aria-label="Предыдущее объяснение"><ChevronLeft size={16} aria-hidden="true"/></button>
                  <span aria-live="polite" aria-atomic="true" aria-label={`Источник ${sourceIndex + 1} из ${criterion.evidence.length}`}>{sourceIndex + 1}<span aria-hidden="true"> / </span>{criterion.evidence.length}</span>
                  <button type="button" onClick={() => changeSource(sourceIndex + 1)} disabled={sourceIndex === criterion.evidence.length - 1} aria-label="Следующее объяснение"><ChevronRight size={16} aria-hidden="true"/></button>
                </nav>}
                {canPlay && onEvidence ? <button className="amx-source-link" type="button" onClick={() => onEvidence(source)} aria-label={`Смотреть фрагмент ${time} по навыку «${name}»`}><Play size={11} fill="currentColor" aria-hidden="true"/><span>{time}</span></button> : <span className="amx-source-unavailable">{canPlay ? time : source.availability === 'unavailable' ? 'Запись недоступна' : 'Только текст'}</span>}
              </div>
            </motion.div>}
          </div>
        </motion.article>;
      })}
    </div>
  </section></LayoutGroup>;
}
