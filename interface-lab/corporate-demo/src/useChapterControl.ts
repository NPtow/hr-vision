import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { useAnimationFrame, useMotionValue, useMotionValueEvent, useReducedMotion, useScroll } from 'motion/react';
import { ChapterClock } from './chapter-clock';
import type { SceneControl } from './site';

export function useChapterControl(chapter: RefObject<HTMLElement | null>, duration: number, paused = false): SceneControl {
  const reduced = useReducedMotion();
  const { scrollYProgress } = useScroll({ target: chapter, offset: ['start start', 'end end'] });
  const progress = useMotionValue(0);
  const [clock] = useState(() => new ChapterClock());
  const [playing, setPlaying] = useState(false);
  const geometry = useRef({ start: 0, travel: 0 });
  const measure = useCallback(() => {
    const element = chapter.current;
    if (!element) return;
    geometry.current.start = window.scrollY + element.getBoundingClientRect().top;
    geometry.current.travel = Math.max(0, element.offsetHeight - window.innerHeight);
  }, [chapter]);
  const follow = useCallback((value: number) => {
    if (!reduced) window.scrollTo({ top: geometry.current.start + value * geometry.current.travel, behavior: 'instant' });
  }, [reduced]);
  const pause = useCallback(() => { clock.pause(); setPlaying(false); }, [clock]);
  const seek = useCallback((value: number) => {
    measure();
    progress.set(clock.seek(value));
    setPlaying(false);
    follow(clock.position);
  }, [clock, follow, measure, progress]);
  const play = useCallback(() => {
    if (reduced) { seek(1); return; }
    if (paused) return;
    measure();
    clock.play();
    setPlaying(clock.playing);
  }, [clock, measure, paused, reduced, seek]);
  useMotionValueEvent(scrollYProgress, 'change', value => {
    if (!reduced && !clock.playing) progress.set(clock.scroll(value));
  });
  useEffect(() => { if (!reduced) progress.set(clock.scroll(scrollYProgress.get())); }, [clock, progress, reduced, scrollYProgress]);
  useAnimationFrame((_, delta) => {
    if (!clock.playing) return;
    progress.set(clock.advance(delta, duration));
    follow(clock.position);
    if (!clock.playing) setPlaying(false);
  });
  useEffect(() => { if (paused) pause(); }, [paused, pause]);
  useEffect(() => {
    const keys = new Set(['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', 'Home', 'End', ' ']);
    const takeScrollControl = () => { clock.takeScrollControl(); setPlaying(false); };
    const onKey = (event: KeyboardEvent) => { if (keys.has(event.key)) takeScrollControl(); };
    const onVisibility = () => { if (document.hidden) pause(); };
    window.addEventListener('wheel', takeScrollControl, { passive: true });
    window.addEventListener('touchstart', takeScrollControl, { passive: true });
    window.addEventListener('pointerdown', takeScrollControl, { passive: true });
    window.addEventListener('hashchange', takeScrollControl);
    window.addEventListener('keydown', onKey);
    window.addEventListener('resize', measure);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.removeEventListener('wheel', takeScrollControl);
      window.removeEventListener('touchstart', takeScrollControl);
      window.removeEventListener('pointerdown', takeScrollControl);
      window.removeEventListener('hashchange', takeScrollControl);
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', measure);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [clock, measure, pause]);
  return useMemo(() => ({ progress, playing, seek, play, pause, toggle: () => clock.playing ? pause() : play() }), [clock, pause, play, playing, progress, seek]);
}
