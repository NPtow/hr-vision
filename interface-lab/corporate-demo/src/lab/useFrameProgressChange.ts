import { useEffect, useRef } from 'react';
import { useMotionValueEvent, type MotionValue } from 'motion/react';

// A derived MotionValue can emit while its parent component is rendering.
// React state must follow after that render, with at most one update per frame.
export function useFrameProgressChange(progress: MotionValue<number>, onChange: (value: number) => void) {
  const callback = useRef(onChange);
  const latest = useRef(progress.get());
  const frame = useRef<number | null>(null);
  useEffect(() => { callback.current = onChange; }, [onChange]);
  useMotionValueEvent(progress, 'change', value => {
    latest.current = value;
    if (frame.current !== null) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = null;
      callback.current(latest.current);
    });
  });
  useEffect(() => () => {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
  }, [progress]);
}
