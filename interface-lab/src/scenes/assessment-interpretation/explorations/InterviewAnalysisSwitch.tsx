import { useRef, type KeyboardEvent } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import './interview-analysis-switch.css';

export type InterviewAnalysisSwitchProps = {
  mode: 'video' | 'map';
  onChange: (mode: 'video' | 'map') => void;
};

/** Both views share this permanently mounted, fixed-size mode control. */
export function InterviewAnalysisSwitch({ mode, onChange }: InterviewAnalysisSwitchProps) {
  const reduced = useReducedMotion();
  const buttons = useRef<Record<'video' | 'map', HTMLButtonElement | null>>({ video: null, map: null });
  function moveSelection(event: KeyboardEvent<HTMLButtonElement>, current: 'video' | 'map') {
    let next: 'video' | 'map';
    if (event.key === 'Home') next = 'video';
    else if (event.key === 'End') next = 'map';
    else if (['ArrowLeft', 'ArrowRight'].includes(event.key)) next = current === 'video' ? 'map' : 'video';
    else return;
    event.preventDefault();
    onChange(next);
    buttons.current[next]?.focus({ preventScroll: true });
  }
  return <div className="interview-analysis-switch" role="group" aria-label="Представление кандидата">
    <motion.span className="interview-analysis-switch__selection" initial={false} animate={{ x: mode === 'map' ? 104 : 0 }}
      transition={reduced ? { duration: 0 } : { type: 'spring', stiffness: 430, damping: 36, mass: 0.7 }} aria-hidden="true" />
    {([{ mode: 'video', label: 'Интервью' }, { mode: 'map', label: 'Анализ' }] as const).map(item => <button
      key={item.mode} type="button" ref={element => { buttons.current[item.mode] = element; }}
      aria-pressed={mode === item.mode} onClick={() => onChange(item.mode)} onKeyDown={event => moveSelection(event, item.mode)}>
      <span className="interview-analysis-switch__label">
        {item.mode === 'video' ? <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m5.5 3 7 5-7 5Z" /></svg> : <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 11V8m5 3V4m5 7V6" /></svg>}
        {item.label}
      </span>
    </button>)}
  </div>;
}
