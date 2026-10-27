import React, { useEffect, useState } from 'react';
import NumberFlow, { NumberFlowGroup } from '@number-flow/react';
import { motion, useMotionValueEvent, useReducedMotion, useTransform, type MotionValue } from 'motion/react';
import { ctaSnapshot } from './cta-offer';
import './countdown-button.css';

export type CtaVariant = 'perimeter' | 'liquid' | 'ribbon';
const numberTiming = { duration: 420, easing: 'cubic-bezier(.2,.7,.2,1)' };
const twoDigits = { minimumIntegerDigits: 2, useGrouping: false };

export function CountdownButton({ variant = 'perimeter', progress, playing = true, onStart }: {
  variant?: CtaVariant; progress: MotionValue<number>; playing?: boolean; onStart: () => void;
}) {
  const [snapshot, setSnapshot] = useState(() => ctaSnapshot(progress.get()));
  const reduced = useReducedMotion();
  useEffect(() => { setSnapshot(ctaSnapshot(progress.get())); }, [progress]);
  useMotionValueEvent(progress, 'change', value => {
    const next = ctaSnapshot(value);
    setSnapshot(previous => previous.seconds === next.seconds && previous.discount === next.discount && previous.expired === next.expired ? previous : next);
  });
  // The first ten seconds hold the 50% offer. Only then does the visual clock start.
  const countdown = useTransform(progress, [.25, 1], [0, 1]);
  const backgroundColor = useTransform(countdown, [0, .38, .72, 1], ['#326bb2', '#72588d', '#ac4d65', '#bd3d46']);
  const remaining = useTransform(countdown, [0, 1], [1, 0]);
  const drainY = useTransform(countdown, [0, 1], ['0%', '115%']);
  const waveX = useTransform(countdown, [0, 1], ['-12%', '0%']);
  const waveScale = useTransform(countdown, [0, .15, .85, 1], [0, 1, 1, 0]);
  const ribbonClip = useTransform(countdown, [0, 1], ['inset(0 0% 0 0)', 'inset(0 100% 0 0)']);
  const ribbonX = useTransform(countdown, [0, 1], ['0%', '-100%']);
  const ringDash = useTransform(countdown, [0, 1], ['100 100', '0 100']);
  const animated = playing && !reduced;

  return (
    <motion.button type="button" className={`cta-countdown-button cta-${variant}`} data-discount={snapshot.discount} data-seconds={snapshot.seconds} data-expired={snapshot.expired} data-timer-visible={snapshot.timerVisible} style={{ backgroundColor }} onClick={onStart} aria-label={snapshot.expired ? 'Начать подбор' : `Начать подбор со скидкой ${snapshot.discount}%${snapshot.timerVisible ? `, осталось ${snapshot.seconds} секунд` : ''}`}>
          {variant === 'liquid' && <motion.span className="cta-liquid-fill" style={{ y: drainY }} aria-hidden="true">
            <motion.svg viewBox="0 0 800 35" preserveAspectRatio="none" style={{ x: reduced ? 0 : waveX, scaleY: reduced ? 0 : waveScale }}><path d="M0 18 C70 0 125 36 200 18 S325 0 400 18 S525 36 600 18 S725 0 800 18 V35 H0Z"/></motion.svg>
          </motion.span>}
          {variant === 'perimeter' && <svg className="cta-perimeter"  aria-hidden="true"><rect className="cta-perimeter-track" x="3" y="3" rx="16"/><motion.rect x="3" y="3" rx="16" pathLength="100" style={{ strokeDasharray: ringDash }}/></svg>}
          {variant === 'ribbon' && <span className="cta-ribbon-track" aria-hidden="true"><motion.span className="cta-ribbon-fill" style={{ clipPath: ribbonClip }}><span/><span/><span/><span/><span/><span/><span/><span/><span/><span/></motion.span><motion.i style={{ x: ribbonX }}><b/></motion.i></span>}
          {snapshot.timerVisible && <span className="cta-timer" aria-hidden="true"><NumberFlowGroup><NumberFlow value={0} format={twoDigits} animated={false}/><span>:</span><NumberFlow value={snapshot.seconds} format={twoDigits} digits={{ 1: { max: 5 } }} animated={animated} transformTiming={numberTiming}/></NumberFlowGroup></span>}
          <span className="cta-button-copy">Начать подбор</span>
          {!snapshot.expired && <span className="cta-discount" aria-hidden="true">−{snapshot.discount}%</span>}
          {variant === 'liquid' && <motion.span className="cta-underflow" style={{ scaleX: remaining }} aria-hidden="true"/>}
        </motion.button>
  );
}
