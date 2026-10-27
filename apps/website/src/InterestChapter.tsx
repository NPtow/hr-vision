import React, { useEffect, useRef, useState } from 'react';
import { useMotionValue, useMotionValueEvent, useReducedMotion, useScroll } from 'motion/react';
import { InterestConcept } from './lab/InterestConcepts';
import './interest-chapter.css';

// Reuse the accepted lab animation with the website's scroll as its clock.
export function InterestChapter() {
  const chapter = useRef<HTMLElement>(null);
  const space = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();
  const { scrollYProgress } = useScroll({ target: chapter, offset: ['start start', 'end end'] });
  const progress = useMotionValue(0);
  useMotionValueEvent(scrollYProgress, 'change', value => progress.set(reduced ? 1 : value));
  useEffect(() => { progress.set(reduced ? 1 : scrollYProgress.get()); }, [reduced, progress, scrollYProgress]);
  const [layout, setLayout] = useState({ width: 1120, height: 630, scale: 1 });

  useEffect(() => {
    const element = space.current;
    if (!element) return;
    const measure = () => {
      const width = element.clientWidth, height = element.clientHeight;
      if (!width || !height) return;
      const compact = window.matchMedia('(max-width: 600px)').matches;
      const frameWidth = compact ? 390 : 1120;
      const frameHeight = compact ? 810 : 630;
      setLayout({ width: frameWidth, height: frameHeight, scale: Math.min(1, width / frameWidth, height / frameHeight) });
    };
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    measure();
    return () => observer.disconnect();
  }, []);

  return <section ref={chapter} id="interest" className="site-interest" aria-label="Кандидаты, которым вы интересны">
    <div className="site-interest-sticky">
      <div ref={space} className="site-interest-space">
        <div className="site-interest-frame" style={{ width: layout.width, height: layout.height, transform: `translate(-50%, -50%) scale(${layout.scale})` }}>
          <InterestConcept variant="signal" progress={progress}/>
        </div>
      </div>
    </div>
  </section>;
}
