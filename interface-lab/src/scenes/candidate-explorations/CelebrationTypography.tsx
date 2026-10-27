import { useId, type ReactNode } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import type { CelebrationProps } from './types';
import './celebration-typography.css';

const phrase = 'Я хорошо зарабатываю в классной компании';
const ease = [0.22, 1, 0.36, 1] as const;

function RevealLine({ children, delay, reduced }: { children: ReactNode; delay: number; reduced: boolean }) {
  return (
    <span className="ce-type-line" aria-hidden="true">
      <motion.span
        className="ce-type-line-inner"
        initial={reduced ? { opacity: 0 } : { opacity: 0, y: '0.84em' }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: reduced ? 0.15 : 0.65, delay: reduced ? 0 : delay, ease }}
      >
        {children}
      </motion.span>
    </span>
  );
}

function Check({ reduced, delay = 0 }: { reduced: boolean; delay?: number }) {
  return (
    <svg viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <motion.path
        d="M8 16.5 13.2 21.5 24 10.5"
        stroke="currentColor"
        strokeWidth="2.25"
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={reduced ? false : { pathLength: 0, opacity: 0 }}
        animate={{ pathLength: 1, opacity: 1 }}
        transition={{ duration: reduced ? 0 : 0.38, delay: reduced ? 0 : delay, ease }}
      />
    </svg>
  );
}

function Underline({ children, delay, reduced }: { children: ReactNode; delay: number; reduced: boolean }) {
  return (
    <span className="ce-type-ink-word">
      {children}
      <svg viewBox="0 0 220 19" preserveAspectRatio="none" fill="none" aria-hidden="true">
        <motion.path
          d="M4 12 C57 5 124 5 214 11"
          stroke="currentColor"
          strokeWidth="3.5"
          strokeLinecap="round"
          initial={reduced ? false : { pathLength: 0, opacity: 0 }}
          animate={{ pathLength: 1, opacity: 1 }}
          transition={{ duration: reduced ? 0 : 0.5, delay: reduced ? 0 : delay, ease }}
        />
      </svg>
    </span>
  );
}

function Actions({ onResume, onProcesses, reduced }: Pick<CelebrationProps, 'onResume' | 'onProcesses'> & { reduced: boolean }) {
  return (
    <div className="ce-type-actions">
      <motion.button
        className="ce-type-button ce-type-button-primary"
        type="button"
        onClick={onResume}
        whileTap={reduced ? undefined : { scale: 0.98 }}
        transition={{ duration: 0.1 }}
      >
        Снова искать работу
        <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
          <path d="M4 10h12m-5-5 5 5-5 5" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </motion.button>
      <button className="ce-type-button ce-type-button-secondary" type="button" onClick={onProcesses}>
        История поиска
      </button>
    </div>
  );
}

/** Two local pause-state explorations. Parent owns every domain action and replay. */
export function CelebrationTypography({ variant, runId, onResume, onProcesses }: CelebrationProps) {
  const reduced = !!useReducedMotion();
  const titleId = useId();
  const receipt = variant % 2 === 0;

  return (
    <section className={`ce-type ${receipt ? 'ce-type-receipt' : 'ce-type-editorial'}`} aria-labelledby={titleId}>
      <div className="ce-type-stage" key={`${variant}-${runId}`}>
        {receipt ? (
          <>
            <motion.article
              className="ce-type-ticket"
              initial={reduced ? { opacity: 0 } : { opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: reduced ? 0.15 : 0.6, ease }}
            >
              <div className="ce-type-ticket-header">
                <span className="ce-type-ticket-brand">HR Vision</span>
                <span className="ce-type-ticket-caption">Мой новый этап</span>
              </div>
              <div className="ce-type-ticket-main">
                <motion.span
                  className="ce-type-chapter"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ duration: 0.2, delay: reduced ? 0 : 0.15 }}
                >
                  Можно выдохнуть
                </motion.span>
                <h2 className="ce-type-title" id={titleId} aria-label={phrase}>
                  <RevealLine delay={0.22} reduced={reduced}>Я хорошо</RevealLine>
                  <RevealLine delay={0.31} reduced={reduced}>зарабатываю</RevealLine>
                  <RevealLine delay={0.4} reduced={reduced}>в классной</RevealLine>
                  <RevealLine delay={0.49} reduced={reduced}><span className="ce-type-blue">компании.</span></RevealLine>
                </h2>
                <motion.div
                  className="ce-type-ticket-stamp"
                  aria-hidden="true"
                  initial={reduced ? { opacity: 0 } : { opacity: 0, scale: 1.08, rotate: -13 }}
                  animate={{ opacity: 1, scale: 1, rotate: -8 }}
                  transition={{ duration: reduced ? 0.15 : 0.35, delay: reduced ? 0 : 0.82, ease }}
                >
                  <Check reduced={reduced} delay={1} />
                  <span>Мне хорошо</span>
                </motion.div>
              </div>
              <div className="ce-type-perforation" aria-hidden="true" />
              <div className="ce-type-ticket-footer">
                <span className="ce-type-pause-dot" aria-hidden="true"><i /><i /></span>
                <div>
                  <strong>Поиск на паузе</strong>
                  <p>Предыдущие процессы сохранены в истории.</p>
                </div>
              </div>
            </motion.article>
            <Actions onResume={onResume} onProcesses={onProcesses} reduced={reduced} />
          </>
        ) : (
          <div className="ce-type-editorial-content">
            <motion.div
              className="ce-type-state"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.2 }}
            >
              <span className="ce-type-state-icon"><Check reduced={reduced} delay={0.12} /></span>
              Поиск на паузе
            </motion.div>
            <h2 className="ce-type-title" id={titleId} aria-label={phrase}>
              <RevealLine delay={0.08} reduced={reduced}>Я <Underline delay={0.8} reduced={reduced}>хорошо</Underline></RevealLine>
              <RevealLine delay={0.18} reduced={reduced}>зарабатываю</RevealLine>
              <RevealLine delay={0.28} reduced={reduced}>в <Underline delay={1.06} reduced={reduced}>классной</Underline></RevealLine>
              <RevealLine delay={0.38} reduced={reduced}>компании.</RevealLine>
            </h2>
            <motion.p
              className="ce-type-editorial-copy"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: reduced ? 0.15 : 0.35, delay: reduced ? 0 : 0.85 }}
            >
              Звучит как хороший план.<br />
              Предыдущие процессы сохранены в истории.
            </motion.p>
            <Actions onResume={onResume} onProcesses={onProcesses} reduced={reduced} />
          </div>
        )}
      </div>
    </section>
  );
}
