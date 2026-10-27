import { useId, useState } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import type { CelebrationProps } from './types';
import './celebration-orbit.css';

const arrive = { type: 'spring' as const, duration: 0.62, bounce: 0.15 };
const settle = [0.22, 1, 0.36, 1] as const;

function Check({ x = 0, y = 0, color = 'currentColor' }: { x?: number; y?: number; color?: string }) {
  return <path d={`M${x - 8} ${y} l5 5 11 -11`} fill="none" stroke={color} strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" />;
}

function OrbitArtwork({ reduced, id }: { reduced: boolean; id: string }) {
  const satellites = [
    { x: 100, y: 144, turn: -14, fill: '#DCEAFF', accent: '#3D75C8', symbol: 'spark' },
    { x: 190, y: 67, turn: 11, fill: '#FFF0BD', accent: '#BC881E', symbol: 'lines' },
    { x: 432, y: 78, turn: -8, fill: '#D4EFE5', accent: '#369878', symbol: 'check' },
    { x: 510, y: 211, turn: 14, fill: '#E4DEFF', accent: '#8266D7', symbol: 'lines' },
    { x: 389, y: 281, turn: -12, fill: '#FFE6D8', accent: '#CA885F', symbol: 'spark' },
    { x: 119, y: 260, turn: 9, fill: '#DDEBFF', accent: '#3D75C8', symbol: 'check' },
  ];
  return <svg viewBox="0 0 600 350" fill="none" aria-hidden="true">
    <defs>
      <linearGradient id={`${id}-card`} x1="165" y1="102" x2="448" y2="288" gradientUnits="userSpaceOnUse">
        <stop stopColor="#538CDF" /><stop offset="1" stopColor="#2361B4" />
      </linearGradient>
      <radialGradient id={`${id}-halo`}><stop stopColor="#E3EDFC" stopOpacity=".72" /><stop offset="1" stopColor="#F8FAFF" stopOpacity="0" /></radialGradient>
      <filter id={`${id}-shadow`} x="-30%" y="-35%" width="160%" height="180%"><feDropShadow dx="0" dy="15" stdDeviation="14" floodColor="#2560AF" floodOpacity=".14" /></filter>
    </defs>
    <ellipse cx="300" cy="180" rx="270" ry="166" fill={`url(#${id}-halo)`} />
    <motion.g initial={reduced ? false : { opacity: 0, scale: 0.9 }} animate={reduced ? { opacity: 0.65 } : { opacity: [0, 1, 0.65], scale: [0.9, 1, 1] }} transition={{ duration: 1.5, times: [0, 0.22, 1], ease: settle }} style={{ transformOrigin: '300px 180px' }}>
      <ellipse cx="300" cy="177" rx="231" ry="104" transform="rotate(-17 300 177)" stroke="#DCE6F5" strokeWidth="1.2" />
      <ellipse cx="300" cy="177" rx="220" ry="114" transform="rotate(27 300 177)" stroke="#E7EDF7" strokeWidth="1.2" />
      <circle cx="78" cy="210" r="3" fill="#B6CEF0" /><circle cx="486" cy="118" r="3" fill="#C6D9F1" />
      <circle cx="356" cy="294" r="2.5" fill="#C3D6F2" />
    </motion.g>
    {!reduced && satellites.map((satellite, i) => <g key={i} transform={`translate(${satellite.x} ${satellite.y})`}>
      <motion.g
        initial={{ opacity: 0, scale: 0.7, rotate: satellite.turn }}
        animate={{ x: [0, (300 - satellite.x) * 0.12, 300 - satellite.x], y: [0, -20, 175 - satellite.y], scale: [0.7, 1, 0.18], rotate: [satellite.turn, satellite.turn * 0.5, 0], opacity: [0, 1, 1, 0] }}
        transition={{ duration: 1.12, delay: i * 0.045, ease: settle, times: [0, 0.26, 1], opacity: { times: [0, 0.1, 0.72, 1], duration: 1.12, delay: i * 0.045 } }}
      >
        <rect x="-25" y="-27" width="50" height="54" rx="15" fill={satellite.fill} stroke="white" strokeWidth="2" />
        {satellite.symbol === 'check' ? <Check color={satellite.accent} /> : satellite.symbol === 'spark'
          ? <path d="M0 -12 C2 -3 3 -2 12 0 C3 2 2 3 0 12 C-2 3 -3 2 -12 0 C-3 -2 -2 -3 0 -12Z" fill={satellite.accent} />
          : <g stroke={satellite.accent} strokeWidth="3" strokeLinecap="round"><path d="M-9 -6 H9" /><path d="M-9 1 H5" /><path d="M-9 8 H0" /></g>}
      </motion.g>
    </g>)}
    <motion.g initial={reduced ? false : { opacity: 0, scale: 0.7, y: 12 }} animate={{ opacity: 1, scale: 1, y: 0 }} transition={reduced ? { duration: 0 } : { ...arrive, delay: 0.87 }} style={{ transformOrigin: '300px 185px' }}>
      <g filter={`url(#${id}-shadow)`}>
        <rect x="164" y="103" width="272" height="162" rx="27" fill={`url(#${id}-card)`} />
        <rect x="164.6" y="103.6" width="270.8" height="160.8" rx="26.4" stroke="white" strokeOpacity=".24" strokeWidth="1.2" />
        <path d="M337 104 C317 147 348 163 342 197 C339 223 365 252 400 265 H409 C373 229 388 200 374 174 C358 143 378 125 380 104Z" fill="white" opacity=".065" />
        <rect x="186" y="126" width="34" height="34" rx="10" fill="white" fillOpacity=".18" />
        <path d="M198 136 C203 135 207 137 206 141 C205 145 201 145 196 146 C192 147 191 145 193 142 C194 139 195 137 198 136Z M207 143 C211 142 214 144 212 148 C211 152 207 154 202 153 C198 152 202 149 204 147Z" fill="white" />
        <text x="230" y="141" fill="white" fontSize="10.5" fontWeight="550" opacity=".8">НОВЫЙ ЭТАП</text>
        <text x="187" y="199" fill="white" fontSize="26" fontWeight="650" letterSpacing="-.6">На своём месте</text>
        <text x="187" y="229" fill="#DFEBFF" fontSize="12.5">Кажется, всё сложилось.</text>
      </g>
      <motion.g initial={reduced ? false : { scale: 0, rotate: -35 }} animate={{ scale: 1, rotate: 0 }} transition={reduced ? { duration: 0 } : { ...arrive, delay: 1.16 }} style={{ transformOrigin: '425px 110px' }}>
        <circle cx="425" cy="110" r="26" fill="white" /><circle cx="425" cy="110" r="21" fill="#DFF3E9" />
        <Check x={425} y={111} color="#2A8E67" />
      </motion.g>
    </motion.g>
    <motion.g initial={reduced ? false : { opacity: 0, scale: 0.5 }} animate={{ opacity: 1, scale: 1 }} transition={reduced ? { duration: 0 } : { ...arrive, delay: 1.25 }} style={{ transformOrigin: '473px 253px' }}>
      <path d="M473 239 C475 250 477 251 488 253 C477 255 475 257 473 268 C471 257 469 255 458 253 C469 251 471 249 473 239Z" fill="#EFCA68" />
      <circle cx="134" cy="100" r="4" fill="#B2D8C9" />
    </motion.g>
  </svg>;
}

function PortfolioArtwork({ reduced, id }: { reduced: boolean; id: string }) {
  const sheets = [
    { x: -120, y: -47, rotate: -18, fill: '#F5DE93', text: 'Всё получилось', line: '#BE9741' },
    { x: 7, y: -76, rotate: 5, fill: '#C8E6D8', text: 'Мои возможности', line: '#72AF96' },
    { x: 130, y: -27, rotate: 19, fill: '#D7E3FF', text: 'Новая работа', line: '#83A5DF' },
  ];
  return <svg viewBox="0 0 600 350" fill="none" aria-hidden="true">
    <defs>
      <linearGradient id={`${id}-case`} x1="159" y1="189" x2="435" y2="298" gradientUnits="userSpaceOnUse"><stop stopColor="#4E87D7" /><stop offset="1" stopColor="#2861B2" /></linearGradient>
      <linearGradient id={`${id}-flap`} x1="300" y1="172" x2="300" y2="246" gradientUnits="userSpaceOnUse"><stop stopColor="#6699DF" /><stop offset="1" stopColor="#3D79CC" /></linearGradient>
      <filter id={`${id}-bag-shadow`} x="-35%" y="-35%" width="170%" height="190%"><feDropShadow dx="0" dy="13" stdDeviation="14" floodColor="#315E98" floodOpacity=".13" /></filter>
    </defs>
    <ellipse cx="300" cy="296" rx="126" ry="13" fill="#E9EEF6" />
    <motion.g initial={reduced ? false : { y: 35, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={reduced ? { duration: 0 } : { ...arrive, delay: 0.08 }}>
      <rect x="258" y="129" width="84" height="46" rx="17" fill="none" stroke="#356ABD" strokeWidth="12" />
      <rect x="164" y="158" width="272" height="122" rx="23" fill="#295FA9" />
    </motion.g>
    {sheets.map((sheet, i) => <g key={i} transform="translate(300 182)">
      <motion.g
        initial={reduced ? false : { x: sheet.x, y: sheet.y, rotate: sheet.rotate, opacity: 0 }}
        animate={reduced ? { x: 0, y: 12, rotate: (i - 1) * 4, opacity: 1 } : { x: [sheet.x, sheet.x * 0.45, 0], y: [sheet.y, sheet.y - 15, 12], rotate: [sheet.rotate, sheet.rotate * 0.5, (i - 1) * 4], opacity: [0, 1, 1] }}
        transition={{ duration: reduced ? 0 : 1.1, delay: reduced ? 0 : 0.06 + i * 0.08, times: [0, 0.36, 1], ease: settle }}
      >
        <rect x="-60" y="-86" width="120" height="142" rx="14" fill={sheet.fill} stroke="white" strokeWidth="2.5" />
        <rect x="-42" y="-66" width="25" height="25" rx="8" fill="white" fillOpacity=".56" />
        <Check x={-29} y={-53} color={sheet.line} />
        <text x="-43" y="-19" fontSize="9.5" fontWeight="600" fill="#4C6381">{sheet.text}</text>
        <path d="M-42 -4 H35 M-42 6 H16 M-42 16 H30" stroke={sheet.line} strokeWidth="4" strokeLinecap="round" opacity=".44" />
      </motion.g>
    </g>)}
    <motion.g initial={reduced ? false : { y: 35, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={reduced ? { duration: 0 } : { ...arrive, delay: 0.08 }}>
      <g filter={`url(#${id}-bag-shadow)`}>
        <rect x="160" y="181" width="280" height="113" rx="25" fill={`url(#${id}-case)`} />
        <rect x="160.6" y="181.6" width="278.8" height="111.8" rx="24.4" stroke="white" strokeOpacity=".22" strokeWidth="1.2" />
        <path d="M178 271 H422" stroke="white" strokeWidth="1.3" strokeDasharray="3 4" opacity=".2" />
      </g>
      <motion.g initial={reduced ? false : { scaleY: 0 }} animate={{ scaleY: 1 }} transition={reduced ? { duration: 0 } : { type: 'spring', duration: 0.6, bounce: 0.16, delay: 0.96 }} style={{ transformOrigin: '300px 175px' }}>
        <path d="M160 176 C160 170 166 165 173 165 H427 C434 165 440 170 440 176 V198 C440 212 431 218 418 221 L319 242 C306 245 294 245 281 242 L182 221 C169 218 160 211 160 198Z" fill={`url(#${id}-flap)`} />
        <path d="M168 198 C168 207 175 211 185 214 L282 235 C295 238 305 238 318 235 L415 214 C425 211 432 207 432 198" stroke="white" strokeWidth="1.2" strokeOpacity=".28" />
      </motion.g>
      <motion.g initial={reduced ? false : { scale: 0.1, rotate: -25, opacity: 0 }} animate={{ scale: 1, rotate: 0, opacity: 1 }} transition={reduced ? { duration: 0 } : { ...arrive, delay: 1.23 }} style={{ transformOrigin: '300px 239px' }}>
        <circle cx="300" cy="239" r="24" fill="#F9DEA0" />
        <circle cx="300" cy="239" r="18" stroke="#DAAF57" strokeWidth="1" />
        <Check x={300} y={240} color="#A67425" />
      </motion.g>
    </motion.g>
    <motion.g initial={reduced ? false : { opacity: 0, scale: 0.5 }} animate={{ opacity: 1, scale: 1 }} transition={reduced ? { duration: 0 } : { ...arrive, delay: 1.2 }} style={{ transformOrigin: '469px 147px' }}>
      <path d="M469 134 C471 144 472 145 482 147 C472 149 471 150 469 160 C467 150 466 149 456 147 C466 145 467 144 469 134Z" fill="#E8BF61" />
      <path d="M114 205 C112 191 121 182 132 184 C135 196 128 205 114 205Z" fill="#99CDB7" />
      <circle cx="454" cy="279" r="3.5" fill="#A9C4E8" />
    </motion.g>
  </svg>;
}

/** Two finite, local-only celebrations. Clicking the illustration replays it. */
export function CelebrationOrbit({ variant, runId, onResume, onProcesses }: CelebrationProps) {
  const reduced = Boolean(useReducedMotion());
  const [replay, setReplay] = useState(0);
  const id = useId().replace(/:/g, '');
  const portfolio = variant === 1;
  return <section className={`co-orbit ${portfolio ? 'co-orbit--portfolio' : ''}`} aria-labelledby={`${id}-title`}>
    <button className="co-orbit-art" type="button" onClick={() => setReplay(value => value + 1)} aria-label="Повторить анимацию успеха">
      <div key={`${variant}-${runId}-${replay}`} className="co-orbit-picture">
        {portfolio ? <PortfolioArtwork reduced={reduced} id={id} /> : <OrbitArtwork reduced={reduced} id={id} />}
      </div>
      <span className="co-orbit-replay" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><path d="M5 8a8 8 0 1 1-1 8M5 3v5h5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg></span>
    </button>
    <div className="co-orbit-copy">
      <span className="co-orbit-eyebrow"><span />Можно выдохнуть</span>
      <h2 id={`${id}-title`}>Я хорошо зарабатываю<br /><span>в классной компании</span></h2>
      <p>Все процессы сохранены в истории. Возвращайтесь, когда захотите перемен.</p>
      <div className="co-orbit-actions">
        <motion.button type="button" className="co-orbit-resume" onClick={onResume} whileTap={reduced ? undefined : { scale: 0.98 }} transition={{ duration: 0.12 }}>Снова искать работу <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M5 12h14m-6-6 6 6-6 6" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg></motion.button>
        <button type="button" className="co-orbit-processes" onClick={onProcesses}>История поиска</button>
      </div>
    </div>
  </section>;
}
