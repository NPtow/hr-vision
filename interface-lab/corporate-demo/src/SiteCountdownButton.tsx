import React, { useEffect, useRef } from 'react';
import { useMotionValue } from 'motion/react';
import { CountdownButton } from './CountdownButton';
import { ctaProgressAt } from './cta-offer';

export function SiteCountdownButton({ onStart, startAtSection }: { onStart: () => void; startAtSection: string }) {
  const progress = useMotionValue(0);
  const startedAt = useRef<number | null>(null);
  useEffect(() => {
    let frame = 0;
    const tick = () => {
      const next = ctaProgressAt(performance.now() - startedAt.current!);
      progress.set(next);
      if (next < 1) frame = requestAnimationFrame(tick);
    };
    const stopWatching = () => {
      window.removeEventListener('scroll', checkDepth);
      window.removeEventListener('resize', checkDepth);
    };
    const checkDepth = () => {
      if (startedAt.current !== null) return;
      const section = document.getElementById(startAtSection);
      // The chapter has reached the middle of the screen. A fast scroll or a
      // restored position below it also counts as reaching this depth.
      if (!section || section.getBoundingClientRect().top > window.innerHeight / 2) return;
      startedAt.current ??= performance.now();
      stopWatching();
      tick();
    };
    if (startedAt.current !== null) tick();
    else {
      window.addEventListener('scroll', checkDepth, { passive: true });
      window.addEventListener('resize', checkDepth);
      frame = requestAnimationFrame(checkDepth);
    }
    return () => {
      stopWatching();
      cancelAnimationFrame(frame);
    };
  }, [progress, startAtSection]);

  return <div className="cta-button-wrap"><CountdownButton progress={progress} onStart={onStart}/></div>;
}
