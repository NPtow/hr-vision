import { useId, useState } from 'react';
import { motion, MotionConfig, useReducedMotion } from 'motion/react';
import type { CelebrationProps } from './types';
import './celebration-path.css';

const settle = { type: 'spring' as const, bounce: 0, duration: 0.58 };

function ReplayIcon() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 11a8 8 0 1 1 2.5 6M4 5v6h6" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

function Briefcase({ color = '#fff' }: { color?: string }) {
  return <g fill="none" stroke={color} strokeWidth="3.1" strokeLinecap="round" strokeLinejoin="round"><path d="M-12-16v-5a5 5 0 0 1 5-5H7a5 5 0 0 1 5 5v5" /><rect x="-28" y="-16" width="56" height="40" rx="9" /><path d="M-28-3a66 66 0 0 0 56 0M0-5v8" /></g>;
}

/** A single, finite route. The stations are an illustration, not a hiring funnel. */
function RouteArtwork({ reduced }: { reduced: boolean }) {
  const id = useId().replace(/:/g, '');
  const route = 'M80 242H212C255 242 273 217 273 189C273 160 253 145 224 145H169C141 145 122 127 122 104C122 78 144 63 178 63H354';
  return <svg className="hrcp-artwork hrcp-artwork--route" viewBox="0 0 520 310" fill="none" aria-hidden="true">
    <defs>
      <linearGradient id={`${id}-route`} x1="80" y1="240" x2="350" y2="60" gradientUnits="userSpaceOnUse"><stop stopColor="#76a3e7" /><stop offset=".58" stopColor="#3167c8" /><stop offset="1" stopColor="#187d78" /></linearGradient>
      <linearGradient id={`${id}-bag`} x1="300" y1="44" x2="411" y2="135" gradientUnits="userSpaceOnUse"><stop stopColor="#3e79dc" /><stop offset="1" stopColor="#2659a8" /></linearGradient>
    </defs>
    <ellipse cx="355" cy="179" rx="79" ry="10" fill="#eef1f5" />
    <path d={route} stroke="#f0f4f8" strokeWidth="38" strokeLinecap="round" />
    <path d={route} stroke="#e0e9f5" strokeWidth="2" strokeLinecap="round" />
    <motion.path d={route} stroke={`url(#${id}-route)`} strokeWidth="12" strokeLinecap="round" initial={reduced ? false : { pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 1.08, ease: [0.25, 0.1, 0.3, 1] }} />
    {[{ x: 80, y: 242, at: 0.04 }, { x: 253, y: 225, at: 0.3 }, { x: 126, y: 124, at: 0.62 }].map(({ x, y, at }) => <motion.g key={x} initial={reduced ? false : { opacity: 0, scale: 0.7 }} animate={{ opacity: 1, scale: 1 }} transition={{ ...settle, delay: reduced ? 0 : at }} style={{ transformOrigin: `${x}px ${y}px` }}>
      <circle cx={x} cy={y} r="10" fill="#fff" /><circle cx={x} cy={y} r="4" fill="#427ac9" />
    </motion.g>)}
    <motion.g initial={reduced ? false : { opacity: 0, y: 22, scale: 0.89 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={{ ...settle, delay: reduced ? 0 : 0.75 }} style={{ transformOrigin: '354px 109px' }}>
      <path d="M350 78v77" stroke="#214980" strokeWidth="5" strokeLinecap="round" />
      <path d="M352 29c19-12 42 13 60 1v34c-18 12-41-13-60-1Z" fill="#bfe880" />
      <rect x="298" y="84" width="113" height="83" rx="25" fill={`url(#${id}-bag)`} />
      <path d="M335 84V76a10 10 0 0 1 10-10h19a10 10 0 0 1 10 10v8" stroke="#2659a8" strokeWidth="6" />
      <path d="M299 117c31 14 79 14 111 0" stroke="#7facf0" strokeWidth="2" />
      <rect x="347" y="116" width="15" height="15" rx="5" fill="#d1e1fa" />
      <path d="M305 99a15 15 0 0 1 13-9" stroke="#84acec" strokeWidth="2" strokeLinecap="round" opacity=".8" />
    </motion.g>
    <motion.g initial={reduced ? false : { opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }} transition={{ ...settle, delay: reduced ? 0 : 1.1 }} style={{ transformOrigin: '406px 163px' }}>
      <circle cx="406" cy="163" r="23" fill="#d0eea8" stroke="#fff" strokeWidth="6" /><path d="m398 163 5 5 10-11" stroke="#2a6549" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    </motion.g>
    <motion.g initial={reduced ? false : { opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, delay: reduced ? 0 : 1.3 }}>
      <circle cx="422" cy="92" r="3" fill="#a6c2e8" /><path d="M442 119v10m-5-5h10" stroke="#7bada7" strokeWidth="2.5" strokeLinecap="round" />
    </motion.g>
  </svg>;
}

function CardGlyph({ kind }: { kind: number }) {
  if (kind === 0) return <g stroke="#456c97" strokeWidth="2.1" fill="none" strokeLinecap="round"><circle cx="0" cy="-6" r="8" /><path d="M-15 15c1-16 29-16 30 0" /></g>;
  if (kind === 1) return <g stroke="#407c76" strokeWidth="2.1" fill="none" strokeLinecap="round" strokeLinejoin="round"><rect x="-16" y="-13" width="32" height="29" rx="5" /><path d="M-9-17v8M9-17v8M-16-3h32M-7 6l5 5 10-10" /></g>;
  return <g stroke="#557643" strokeWidth="2.1" fill="none" strokeLinecap="round" strokeLinejoin="round"><path d="M-13-15H5l8 8v24h-26ZM5-15v8h8M-6 1h12M-6 7h8" /></g>;
}

/** A paper stack unfolds into one composed result; no looping celebration. */
function StackArtwork({ reduced }: { reduced: boolean }) {
  const cards = [
    { x: -125, y: 27, rotate: -16, fill: '#edf3fb', line: '#d3e0f1' },
    { x: 0, y: -2, rotate: 0, fill: '#e9f4f0', line: '#cce4db' },
    { x: 125, y: 27, rotate: 16, fill: '#f0f5e8', line: '#dce8cc' },
  ];
  return <svg className="hrcp-artwork hrcp-artwork--stack" viewBox="0 0 520 310" fill="none" aria-hidden="true">
    <ellipse cx="260" cy="270" rx="145" ry="11" fill="#f0f3f7" />
    <g transform="translate(260 83)">
      {cards.map((card, index) => <motion.g key={index} initial={reduced ? false : { x: 0, y: 52 + index * 5, rotate: (index - 1) * 4, opacity: 0 }} animate={{ x: card.x, y: card.y, rotate: card.rotate, opacity: 1 }} transition={{ ...settle, duration: 0.8, delay: reduced ? 0 : index * 0.09 }} style={{ transformOrigin: '0px 112px' }}>
        <rect x="-66" y="0" width="132" height="151" rx="22" fill={card.fill} stroke="#fff" strokeWidth="3" />
        <g transform="translate(0 43)"><CardGlyph kind={index} /></g>
        <rect x="-34" y="77" width="68" height="5" rx="2.5" fill={card.line} />
        <rect x="-24" y="90" width="48" height="5" rx="2.5" fill={card.line} />
        <circle cx="0" cy="122" r="11" fill="#fff" fillOpacity=".75" /><path d="m-4 122 3 3 5-6" stroke={index === 0 ? '#718fb1' : '#5b9080'} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </motion.g>)}
    </g>
    <motion.g initial={reduced ? false : { opacity: 0, y: 28, scale: 0.84 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={{ ...settle, duration: 0.72, delay: reduced ? 0 : 0.62 }} style={{ transformOrigin: '260px 206px' }}>
      <rect x="192" y="136" width="136" height="124" rx="40" fill="#2e67bd" stroke="#fff" strokeWidth="7" />
      <path d="M205 164c3-9 11-15 22-16" stroke="#6391d5" strokeWidth="2" strokeLinecap="round" />
      <g transform="translate(260 199)"><Briefcase color="#f7faff" /></g>
    </motion.g>
    <motion.g initial={reduced ? false : { opacity: 0, y: 12, scale: 0.72 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={{ ...settle, delay: reduced ? 0 : 1.04 }} style={{ transformOrigin: '323px 244px' }}>
      <circle cx="323" cy="244" r="25" fill="#cbea91" stroke="#fff" strokeWidth="6" /><path d="m314 244 6 6 12-13" stroke="#3b6343" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    </motion.g>
  </svg>;
}

export function CelebrationPath({ variant, runId, onResume, onProcesses }: CelebrationProps) {
  const reduced = Boolean(useReducedMotion());
  const [replay, setReplay] = useState(0);
  const isStack = variant % 2 === 1;
  const animationKey = `${runId}-${variant}-${replay}-${reduced ? 'still' : 'motion'}`;
  return <MotionConfig reducedMotion="user">
    <section className={`hrcp-celebration ${isStack ? 'hrcp-celebration--stack' : 'hrcp-celebration--route'}`} aria-label="Поиск на паузе">
      <div className="hrcp-illustration">
        <button className="hrcp-replay" type="button" onClick={() => setReplay(value => value + 1)} aria-label="Повторить анимацию"><ReplayIcon /><span>Ещё раз</span></button>
        <div key={animationKey}>{isStack ? <StackArtwork reduced={reduced} /> : <RouteArtwork reduced={reduced} />}</div>
      </div>
      <motion.div key={`copy-${animationKey}`} className="hrcp-copy" initial={reduced ? false : { opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ ...settle, duration: 0.5, delay: reduced ? 0 : 0.44 }}>
        <div className="hrcp-pause"><span aria-hidden="true"><i /><i /></span>Поиск на паузе</div>
        <h2>Я хорошо зарабатываю в классной компании</h2>
        <p>Поиск на паузе. Все процессы сохранены в истории. Вернуться можно в любой момент.</p>
      </motion.div>
      <div className="hrcp-actions">
        <motion.button type="button" className="hrcp-resume" onClick={onResume} whileTap={reduced ? undefined : { scale: 0.98 }} transition={{ type: 'spring', bounce: 0, duration: 0.22 }}>Снова искать работу<svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 12h16m-6-6 6 6-6 6" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg></motion.button>
        <button type="button" className="hrcp-processes" onClick={onProcesses}>История поиска</button>
      </div>
    </section>
  </MotionConfig>;
}
