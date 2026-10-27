import { useId, useState } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import { ArrowRight, RotateCcw } from 'lucide-react';
import type { CelebrationProps } from './types';
import './celebration-object.css';

const settle = [0.22, 1, 0.36, 1] as const;

function Desk({ reduced }: { reduced: boolean }) {
  const id = useId().replace(/:/g, '');
  return <svg className="cex-object-desk" viewBox="0 0 520 340" fill="none" aria-hidden="true">
    <defs>
      <linearGradient id={`${id}-screen`} x1="199" y1="128" x2="340" y2="239" gradientUnits="userSpaceOnUse"><stop stopColor="#F4F8FF" /><stop offset="1" stopColor="#E3EDFF" /></linearGradient>
      <linearGradient id={`${id}-pot`} x1="83" y1="203" x2="119" y2="262" gradientUnits="userSpaceOnUse"><stop stopColor="#FFE3CB" /><stop offset="1" stopColor="#F6C8A6" /></linearGradient>
    </defs>
    <ellipse cx="265" cy="277" rx="225" ry="19" fill="#E9EFF9" />
    <path d="M34 263H487" stroke="#C9D7EA" strokeWidth="3" strokeLinecap="round" />
    <motion.g initial={reduced ? false : { opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: reduced ? 0.12 : 0.5, ease: settle }}>
      <rect x="143" y="99" width="259" height="157" rx="16" fill="#214D8E" />
      <rect x="151" y="107" width="243" height="141" rx="10" fill={`url(#${id}-screen)`} />
      <circle cx="272" cy="103" r="1.5" fill="#82A4CF" />
      <path d="M134 255H410L422 267C425 271 421 276 415 276H128C122 276 118 271 121 267L134 255Z" fill="#B3C9E7" />
      <path d="M135 255H410L415 261H129L135 255Z" fill="#D5E2F4" />
      <path d="M243 255H300L296 260H247L243 255Z" fill="#90ADCF" />
      <rect x="164" y="119" width="43" height="5" rx="2.5" fill="#D7E2F3" />
      <circle cx="378" cy="122" r="3" fill="#D7E2F3" />
      <rect x="228" y="222" width="89" height="4" rx="2" fill="#CEDDF2" />
      <motion.circle cx="273" cy="173" r="35" fill="#3465AD" initial={reduced ? false : { scale: 0.72, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} style={{ transformOrigin: '273px 173px' }} transition={{ delay: reduced ? 0 : 0.28, duration: reduced ? 0.12 : 0.45, ease: settle }} />
      <motion.path d="M255 173L267 185L290 160" stroke="white" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" initial={reduced ? false : { pathLength: 0, opacity: 0 }} animate={{ pathLength: 1, opacity: 1 }} transition={{ delay: reduced ? 0 : 0.57, duration: reduced ? 0.12 : 0.38, ease: 'easeOut' }} />
      <motion.g initial={reduced ? false : { opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: reduced ? 0 : 0.65, duration: 0.2 }}>
        <path d="M217 167L213 164M217 182L211 184M329 165L334 162M330 181L336 183" stroke="#94B2DD" strokeWidth="2.5" strokeLinecap="round" />
      </motion.g>
    </motion.g>
    <motion.g initial={reduced ? false : { y: 10, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={{ delay: reduced ? 0 : 0.1, duration: reduced ? 0.12 : 0.5, ease: settle }}>
      <motion.g initial={reduced ? false : { rotate: -8 }} animate={reduced ? { rotate: 0 } : { rotate: [-8, 5, -2, 0] }} transition={{ delay: 0.15, duration: 0.8, ease: 'easeOut' }} style={{ transformOrigin: '101px 219px' }}>
        <path d="M101 221V160" stroke="#718FB8" strokeWidth="3.5" strokeLinecap="round" />
        <path d="M100 194C76 196 66 179 74 163C93 163 107 176 100 194Z" fill="#A0BEE0" />
        <path d="M102 179C126 177 136 159 127 145C109 148 98 161 102 179Z" fill="#658FC2" />
        <path d="M102 159C88 150 87 133 102 123C116 134 116 151 102 159Z" fill="#B6CDE8" />
      </motion.g>
      <path d="M74 213H129L122 254C121 259 117 263 111 263H92C86 263 82 259 81 254L74 213Z" fill={`url(#${id}-pot)`} />
      <rect x="70" y="209" width="63" height="11" rx="5.5" fill="#FFE4CE" />
      <path d="M89 243C96 248 108 248 115 243" stroke="#D49871" strokeWidth="2" strokeLinecap="round" />
    </motion.g>
    <motion.g initial={reduced ? false : { y: -7, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={{ delay: reduced ? 0 : 0.22, duration: reduced ? 0.12 : 0.45, ease: settle }}>
      <path d="M468 224H475C488 224 489 246 475 247H468" stroke="#91AED4" strokeWidth="7" />
      <path d="M430 218H472V253C472 259 467 263 461 263H441C435 263 430 259 430 253V218Z" fill="#C4D8F2" />
      <ellipse cx="451" cy="218" rx="21" ry="5" fill="#A6C1E3" />
      <ellipse cx="451" cy="218" rx="16" ry="2.5" fill="#7595BF" />
      <motion.path d="M447 199C434 185 458 181 447 165" stroke="#C7D6EB" strokeWidth="3" strokeLinecap="round" initial={reduced ? false : { pathLength: 0, opacity: 0 }} animate={{ pathLength: 1, opacity: 1 }} transition={{ delay: reduced ? 0 : 0.65, duration: reduced ? 0.12 : 0.8, ease: 'easeOut' }} />
    </motion.g>
    <motion.g initial={reduced ? false : { opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }} style={{ transformOrigin: '410px 79px' }} transition={{ delay: reduced ? 0 : 0.88, duration: reduced ? 0.12 : 0.4, ease: settle }}>
      <path d="M408 58L411 68L421 71L411 74L408 84L405 74L395 71L405 68L408 58Z" fill="#ECCAA8" />
      <circle cx="430" cy="92" r="3.5" fill="#B9D0ED" />
    </motion.g>
  </svg>;
}

function Pass({ reduced }: { reduced: boolean }) {
  return <div className="cex-object-pass-stage" aria-hidden="true">
    <motion.div className="cex-object-pass-shadow" initial={reduced ? false : { scaleX: 0.7, opacity: 0 }} animate={{ scaleX: 1, opacity: 1 }} transition={{ duration: reduced ? 0.12 : 0.9 }} />
    <motion.div className="cex-object-pass" initial={reduced ? false : { rotateY: 180, rotateZ: -5, y: 12 }} animate={{ rotateY: 0, rotateZ: -3, y: 0 }} transition={{ delay: reduced ? 0 : 0.3, duration: reduced ? 0 : 0.95, ease: [0.65, 0, 0.2, 1] }}>
      <div className="cex-object-pass-face cex-object-pass-back">
        <div className="cex-object-pass-top"><span>HR VISION</span><span>МОЙ ПОИСК</span></div>
        <div className="cex-object-pass-search"><svg viewBox="0 0 64 64" fill="none"><circle cx="28" cy="28" r="17" stroke="currentColor" strokeWidth="3" /><path d="M41 41L54 54" stroke="currentColor" strokeWidth="3" strokeLinecap="round" /><path d="M20 28H36M28 20V36" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg><strong>Ищу своё место.</strong></div>
        <div className="cex-object-pass-footer"><span>Следующая глава</span><span>Уже близко</span></div>
      </div>
      <div className="cex-object-pass-face cex-object-pass-front">
        <div className="cex-object-pass-top"><span>HR VISION</span><span>НОВЫЙ ЭТАП</span></div>
        <div className="cex-object-pass-result"><span className="cex-object-pass-mark"><svg viewBox="0 0 60 60" fill="none"><path d="M30 4L36 10L44 9L48 17L56 21L54 30L56 39L48 43L44 51L36 50L30 56L24 50L16 51L12 43L4 39L6 30L4 21L12 17L16 9L24 10L30 4Z" fill="#DDE9FB" /><path d="M19 30L27 38L42 22" stroke="#3465AD" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" /></svg></span><div><span>Хорошая работа.</span><strong>Моё место.</strong></div></div>
        <div className="cex-object-pass-perforation"><i /><span /><i /></div>
        <div className="cex-object-pass-footer"><span className="cex-object-pass-pause"><i />Поиск на паузе</span><svg className="cex-object-pass-code" viewBox="0 0 74 23" fill="currentColor"><path d="M0 0H2V23H0ZM5 0H6V23H5ZM9 0H13V23H9ZM16 0H18V23H16ZM22 0H23V23H22ZM27 0H32V23H27ZM35 0H37V23H35ZM40 0H42V23H40ZM46 0H47V23H46ZM51 0H55V23H51ZM58 0H60V23H58ZM64 0H67V23H64ZM70 0H74V23H70Z" /></svg></div>
      </div>
    </motion.div>
    <motion.svg className="cex-object-pass-spark" viewBox="0 0 48 48" fill="none" initial={reduced ? false : { opacity: 0, scale: 0.5, rotate: -35 }} animate={{ opacity: 1, scale: 1, rotate: 0 }} transition={{ delay: reduced ? 0 : 1.1, duration: reduced ? 0.12 : 0.35, ease: settle }}><path d="M24 2L29 17L44 22L29 27L24 42L19 27L4 22L19 17L24 2Z" fill="#E7C5A3" /><circle cx="42" cy="40" r="3" fill="#B4CCE9" /></motion.svg>
  </div>;
}

/** Two original, local-only pause states. The parent owns the actual search state. */
export function CelebrationObject({ variant, runId, onResume, onProcesses }: CelebrationProps) {
  const reduced = useReducedMotion() ?? false;
  const [replay, setReplay] = useState(0);
  const isPass = variant === 1;
  const titleId = useId();
  return <section className={`cex-object-celebration ${isPass ? 'cex-object-ticket-layout' : 'cex-object-desk-layout'}`} aria-labelledby={titleId}>
    <div className="cex-object-layout">
    <div className="cex-object-illustration">
      <button type="button" className="cex-object-replay" onClick={() => setReplay(value => value + 1)} aria-label={isPass ? 'Повторить анимацию пропуска в новую работу' : 'Повторить анимацию рабочего места'}>
        <div key={`${variant}-${runId}-${replay}`} className="cex-object-art">{isPass ? <Pass reduced={reduced} /> : <Desk reduced={reduced} />}</div>
        <span className="cex-object-replay-caption"><RotateCcw size={13} strokeWidth={1.7} />Повторить</span>
      </button>
    </div>
    <div className="cex-object-copy">
      <span className="cex-object-eyebrow"><span />Поиск на паузе</span>
      <h2 id={titleId}>Я хорошо зарабатываю <span>в классной компании.</span></h2>
      <p>{isPass ? 'Вот это поворот. Пусть новая глава будет хорошей — а мы побудем рядом.' : 'Резюме может выдохнуть. А вы — спокойно освоиться на новом месте.'}</p>
      <div className="cex-object-actions">
        <motion.button type="button" className="cex-object-resume" whileTap={reduced ? undefined : { scale: 0.98 }} onClick={onResume}>Снова искать работу<ArrowRight size={17} strokeWidth={1.8} /></motion.button>
        <button type="button" className="cex-object-processes" onClick={onProcesses}>История поиска</button>
      </div>
      <span className="cex-object-note">Прежние отклики и встречи сохранены в истории.</span>
    </div>
    </div>
  </section>;
}
