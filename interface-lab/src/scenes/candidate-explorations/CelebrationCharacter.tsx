import { useCallback, useEffect, useRef } from 'react';
import { motion, useAnimate, useReducedMotion } from 'motion/react';
import type { CelebrationProps } from './types';
import './celebration-character.css';

type Stoppable = { stop: () => void };
const gentleEase = [0.22, 0.72, 0.2, 1] as const;

function PortfolioCharacter() {
  return <>
    <ellipse cx="180" cy="143" rx="131" ry="102" fill="#F4F7FC" />
    <ellipse className="cex-char-shadow" cx="181" cy="236" rx="66" ry="8" fill="#E4EBF5" />
    <g className="cex-char-bag" style={{ transformOrigin: '180px 224px' }}>
      <path d="M148 92V83C148 70 158 62 171 62H189C202 62 212 70 212 83V92" fill="none" stroke="#355C93" strokeWidth="10" strokeLinecap="round" />
      <path d="M160 89V84C160 79 164 76 170 76H190C196 76 200 79 200 84V89" fill="none" stroke="#DCE7F7" strokeWidth="5" strokeLinecap="round" />
      <g className="cex-char-left-arm" style={{ transformOrigin: '113px 155px' }}>
        <path d="M114 154C101 159 93 171 92 185" fill="none" stroke="#416AA1" strokeWidth="10" strokeLinecap="round" />
        <path d="M88 185L96 186" fill="none" stroke="#416AA1" strokeWidth="11" strokeLinecap="round" />
      </g>
      <g className="cex-char-wave" style={{ transformOrigin: '247px 153px' }}>
        <path d="M244 154C262 154 268 142 269 128" fill="none" stroke="#416AA1" strokeWidth="10" strokeLinecap="round" />
        <path d="M266 129L269 119L274 126" fill="none" stroke="#416AA1" strokeWidth="8" strokeLinecap="round" strokeLinejoin="round" />
      </g>
      <path d="M151 202V225M210 202V225" stroke="#355C93" strokeWidth="10" strokeLinecap="round" />
      <path d="M152 227H137M209 227H224" stroke="#2A4874" strokeWidth="12" strokeLinecap="round" />
      <path d="M111 115C111 98 121 88 139 88H221C239 88 249 98 249 115V183C249 201 239 211 221 211H139C121 211 111 201 111 183V115Z" fill="#6F99D0" />
      <path d="M111 116C111 98 121 88 139 88H221C239 88 249 98 249 116V130C206 145 156 145 111 130V116Z" fill="#B9D1EF" />
      <path d="M113 132C156 145 207 145 247 132" fill="none" stroke="#5682BB" strokeWidth="2" />
      <rect x="170" y="131" width="20" height="24" rx="7" fill="#E7EEF9" />
      <path d="M180 138V147" stroke="#5D84B5" strokeWidth="3" strokeLinecap="round" />
      <g className="cex-char-face" style={{ transformOrigin: '181px 173px' }}>
        <path d="M145 169V174M216 169V174" stroke="#243F65" strokeWidth="6" strokeLinecap="round" />
        <path d="M167 177C172 186 188 186 194 177" fill="none" stroke="#243F65" strokeWidth="4" strokeLinecap="round" />
        <ellipse cx="137" cy="181" rx="6" ry="3" fill="#9BBBE3" />
        <ellipse cx="224" cy="181" rx="6" ry="3" fill="#9BBBE3" />
      </g>
      <path d="M123 155V181C123 191 128 198 139 198" fill="none" stroke="#83AADD" strokeWidth="3" strokeLinecap="round" />
    </g>
    <g className="cex-char-spark" style={{ transformOrigin: '274px 83px' }}>
      <path d="M274 69C276 79 278 81 288 83C278 85 276 87 274 97C272 87 270 85 260 83C270 81 272 79 274 69Z" fill="#B5CBBF" />
    </g>
    <g className="cex-char-small-spark" style={{ transformOrigin: '83px 110px' }}>
      <path d="M83 104V116M77 110H89" stroke="#C4C1D7" strokeWidth="3" strokeLinecap="round" />
    </g>
  </>;
}

function HighFiveCharacters() {
  return <>
    <path d="M48 187C23 113 88 40 169 43C246 19 324 83 319 156C314 220 252 243 180 231C116 249 67 234 48 187Z" fill="#F5F8F7" />
    <ellipse cx="125" cy="233" rx="47" ry="7" fill="#E2EDE8" />
    <ellipse cx="241" cy="233" rx="43" ry="7" fill="#E9E6EF" />
    <g className="cex-char-friend-left" style={{ transformOrigin: '125px 222px' }}>
      <path d="M109 210V228H99M141 210V228H151" fill="none" stroke="#477368" strokeWidth="9" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M86 168C76 173 72 183 74 194" fill="none" stroke="#57867A" strokeWidth="9" strokeLinecap="round" />
      <path d="M84 138C84 121 96 111 114 111H137C155 111 167 124 167 142V184C167 204 154 215 136 215H114C95 215 84 203 84 185V138Z" fill="#8EB8A9" />
      <path d="M92 135C92 124 100 119 111 119H137" fill="none" stroke="#AFD0C2" strokeWidth="3" strokeLinecap="round" />
      <path d="M113 154V158M145 154V158" stroke="#2F544B" strokeWidth="5" strokeLinecap="round" />
      <path d="M121 174C127 180 138 178 141 171" fill="none" stroke="#2F544B" strokeWidth="3.5" strokeLinecap="round" />
      <ellipse cx="106" cy="171" rx="5" ry="2.5" fill="#BED8CB" />
      <g className="cex-char-five-left" style={{ transformOrigin: '159px 178px' }}>
        <path d="M159 177C170 166 172 146 174 129" fill="none" stroke="#57867A" strokeWidth="9" strokeLinecap="round" />
        <path d="M174 130L168 121M174 129L174 116M175 129L181 120" fill="none" stroke="#57867A" strokeWidth="6" strokeLinecap="round" />
      </g>
    </g>
    <g className="cex-char-friend-right" style={{ transformOrigin: '240px 222px' }}>
      <path d="M226 210V227H216M254 210V227H264" fill="none" stroke="#756780" strokeWidth="9" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M276 166C287 171 292 184 288 194" fill="none" stroke="#8A7B96" strokeWidth="9" strokeLinecap="round" />
      <path d="M203 145C203 119 217 104 240 104C263 104 279 120 279 146V186C279 206 266 216 246 216H235C216 216 203 206 203 186V145Z" fill="#B7A9C5" />
      <path d="M212 144C212 124 222 113 239 112" fill="none" stroke="#D1C6DC" strokeWidth="3" strokeLinecap="round" />
      <path d="M221 155V159M251 155V159" stroke="#51445F" strokeWidth="5" strokeLinecap="round" />
      <path d="M223 173C228 180 237 180 242 175" fill="none" stroke="#51445F" strokeWidth="3.5" strokeLinecap="round" />
      <ellipse cx="257" cy="172" rx="5" ry="2.5" fill="#D8CADF" />
      <g className="cex-char-five-right" style={{ transformOrigin: '209px 178px' }}>
        <path d="M210 177C196 164 189 146 187 129" fill="none" stroke="#8A7B96" strokeWidth="9" strokeLinecap="round" />
        <path d="M187 130L192 120M187 129L186 116M187 129L181 120" fill="none" stroke="#8A7B96" strokeWidth="6" strokeLinecap="round" />
      </g>
    </g>
    <g className="cex-char-five-spark" style={{ transformOrigin: '181px 97px' }}>
      <path d="M180 83V90M161 92L166 97M197 92L192 97" fill="none" stroke="#B49D69" strokeWidth="3" strokeLinecap="round" />
    </g>
    <g className="cex-char-five-halo" style={{ transformOrigin: '181px 119px' }}>
      <circle cx="181" cy="119" r="17" fill="none" stroke="#B9D1C5" strokeWidth="2" opacity=".7" />
    </g>
  </>;
}

/** A one-shot, interruptible illustration. Business actions are owned by the parent. */
export function CelebrationCharacter({ variant, runId, onResume, onProcesses }: CelebrationProps) {
  const [scope, animate] = useAnimate<SVGSVGElement>();
  const reduceMotion = useReducedMotion();
  const animations = useRef<Stoppable[]>([]);
  const isPair = variant % 2 === 1;
  const stop = useCallback(() => {
    animations.current.forEach(control => control.stop());
    animations.current = [];
  }, []);

  const celebrate = useCallback(() => {
    stop();
    if (!scope.current) return;
    if (reduceMotion) {
      // Keep the meaning, remove positional movement. No looping motion in either mode.
      animations.current = [animate(isPair ? '.cex-char-five-spark' : '.cex-char-spark', { opacity: [null, 0.4, 1] }, { duration: 0.24 })];
      return;
    }
    if (isPair) {
      animations.current = [
        animate('.cex-char-friend-left', { x: [null, -5, 3, -1, 0], y: [null, 3, -7, 1, 0], rotate: [null, -3, 4, -1, 0] }, { duration: 1.35, times: [0, .2, .48, .72, 1], ease: gentleEase }),
        animate('.cex-char-friend-right', { x: [null, 5, -3, 1, 0], y: [null, 3, -5, 1, 0], rotate: [null, 3, -4, 1, 0] }, { duration: 1.35, times: [0, .2, .48, .72, 1], ease: gentleEase }),
        animate('.cex-char-five-left', { rotate: [null, -32, 5, 0] }, { duration: 1.28, times: [0, .22, .52, 1], ease: gentleEase }),
        animate('.cex-char-five-right', { rotate: [null, 32, -5, 0] }, { duration: 1.28, times: [0, .22, .52, 1], ease: gentleEase }),
        animate('.cex-char-five-spark', { opacity: [null, 0, 0, 1, .8], scale: [null, .8, .8, 1.1, 1] }, { duration: 1.35, times: [0, .15, .4, .57, 1], ease: gentleEase }),
        animate('.cex-char-five-halo', { opacity: [null, 0, 0, .7, 0], scale: [null, .8, .8, 1.4, 1.65] }, { duration: 1.35, times: [0, .1, .42, .55, 1], ease: gentleEase }),
      ];
    } else {
      animations.current = [
        animate('.cex-char-bag', { y: [null, 5, -19, 1, 0], rotate: [null, -4, 3, -1, 0], scaleY: [null, .97, 1.025, .99, 1] }, { duration: 1.48, times: [0, .2, .46, .73, 1], ease: gentleEase }),
        animate('.cex-char-shadow', { scaleX: [null, 1.04, .8, 1.03, 1], opacity: [null, 1, .6, 1, 1] }, { duration: 1.48, times: [0, .2, .46, .73, 1], ease: gentleEase }),
        animate('.cex-char-wave', { rotate: [null, -17, 14, -9, 0] }, { duration: 1.45, times: [0, .2, .5, .7, 1], ease: gentleEase }),
        animate('.cex-char-left-arm', { rotate: [null, 7, -9, 0] }, { duration: 1.35, times: [0, .2, .45, 1], ease: gentleEase }),
        animate('.cex-char-spark', { rotate: [null, -12, 13, 0], scale: [null, .7, 1.1, 1] }, { duration: 1.28, ease: gentleEase }),
        animate('.cex-char-small-spark', { opacity: [null, .3, 1], rotate: [null, 25, 0] }, { duration: 1.2, ease: gentleEase }),
      ];
    }
  }, [animate, isPair, reduceMotion, scope, stop]);

  useEffect(() => {
    // Reset includes manual replay, a new gallery run, and a reduced-motion change.
    const nodes = scope.current?.querySelectorAll<SVGElement>('[class^="cex-char-"]');
    stop();
    nodes?.forEach(node => { node.style.removeProperty('transform'); node.style.removeProperty('opacity'); });
    celebrate();
    return stop;
  }, [runId, celebrate, scope, stop]);

  return <section className={`cex-celebration-character ${isPair ? 'cex-celebration-character-pair' : ''}`} data-celebration-character={isPair ? 'high-five' : 'portfolio'}>
    <motion.button className="cex-character-stage" type="button" aria-label={isPair ? 'Дать пять — повторить анимацию' : 'Поздравить портфель — повторить анимацию'} onClick={celebrate} whileTap={reduceMotion ? undefined : { scale: .985 }} transition={{ duration: .14 }}>
      <svg ref={scope} viewBox="0 0 360 270" fill="none" aria-hidden="true" focusable="false">
        {isPair ? <HighFiveCharacters /> : <PortfolioCharacter />}
      </svg>
    </motion.button>
    <div className="cex-character-copy">
      <p className="cex-character-eyebrow"><span aria-hidden="true" />Поиск на паузе</p>
      <h2>Я хорошо зарабатываю<br className="cex-character-desktop-break" /> в классной компании</h2>
      <p className="cex-character-description">Пусть работа радует.<br />Прежние процессы сохранены в истории.</p>
    </div>
    <div className="cex-character-actions">
      <motion.button type="button" className="cex-character-resume" onClick={onResume} whileTap={reduceMotion ? undefined : { scale: .98 }} transition={{ duration: .12 }}>Снова искать работу<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M4 10H16M11 5L16 10L11 15" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg></motion.button>
      <button type="button" className="cex-character-processes" onClick={onProcesses}>История поиска</button>
    </div>
  </section>;
}
