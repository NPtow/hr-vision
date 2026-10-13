import React, { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { motion, useMotionValue, useReducedMotion, useTransform, type MotionValue } from 'motion/react';
import { ArrowRight, Check, CheckCheck, CirclePlay, FileText, ListChecks, MessageSquare, Play, Users, CalendarDays, ClipboardList, Minus } from 'lucide-react';
import { LOGO_URL } from '../assets';
import { interviewChapters } from '../interview-data';
import type { SceneControl } from '../site';
import type { InterviewVariant } from './interview-variants';
import type { ReviewMatch } from './review-data';
import { candidates } from '../candidate-data';
import { useFrameProgressChange } from './useFrameProgressChange';
import './interview.css';

type Rect = { x: number; y: number; w: number; h: number };
type Geometry = { width: number; height: number; mobile: boolean; rects: Rect[] };
const blank = { x: 0, y: 0, w: 0, h: 0 };
// A single clock drives focus, camera, emphasis and the lab player. No detached timers.
const beats = [0, .065, .095, .285, .315, .495, .525, .705, .735, .895, .935, 1];
const targets = [-1, -1, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4];
const stops = [.14, .4, .61, .85];
const activeAt = (p: number) => p < .08 ? -1 : p < .3 ? 0 : p < .51 ? 1 : p < .72 ? 2 : p < .915 ? 3 : 4;
const smooth = (v: number) => v * v * (3 - 2 * v);
const clamp = (v: number) => Math.max(0, Math.min(1, v));
function interpolate<T extends Record<string, number>>(p: number, values: T[]): T {
  let i = 0;
  while (i < beats.length - 2 && p > beats[i + 1]) i++;
  const t = smooth(clamp((p - beats[i]) / (beats[i + 1] - beats[i])));
  return Object.fromEntries(Object.keys(values[i]).map(key => [key, values[i][key] + (values[i + 1][key] - values[i][key]) * t])) as T;
}
function localRect(node: HTMLElement, root: HTMLElement): Rect {
  let x = 0, y = 0, current: HTMLElement | null = node;
  while (current && current !== root) { x += current.offsetLeft; y += current.offsetTop; current = current.offsetParent as HTMLElement | null; }
  return { x: x - 5, y: y - 5, w: node.offsetWidth + 10, h: node.offsetHeight + 10 };
}

type Continuation = { progress: MotionValue<number>; active: boolean; content: ReactNode; title: ReactNode; match?: ReviewMatch };

export function InterviewTour({ control, variant, onFeedback, match, continuation }: { control: Pick<SceneControl, 'progress' | 'seek'>; variant: Exclude<InterviewVariant, 'original'>; onFeedback?: () => void; match?: ReviewMatch; continuation?: Continuation }) {
  const person = match?.person ?? candidates[0];
  const contextPerson = continuation?.match?.person ?? person;
  const strengths = match?.strengths ?? [
    { title: 'Возвращать клиентов', detail: 'Разбирает причины ухода' },
    { title: 'Развивать B2B-базу', detail: '3 года релевантного опыта' },
  ];
  const viewport = useRef<HTMLDivElement>(null);
  const board = useRef<HTMLDivElement>(null);
  const panels = useRef<(HTMLElement | null)[]>([]);
  const geometry = useMotionValue<Geometry>({ width: 1120, height: 544, mobile: false, rects: [blank, blank, blank, blank, blank] });
  const still = useMotionValue(0);
  const keyboardPanel = useMotionValue(-1);
  const handoff = continuation?.progress ?? still;
  const reduced = useReducedMotion();
  const [active, setActive] = useState(-1);
  const uid = useId();
  useEffect(() => setActive(activeAt(control.progress.get())), [control.progress]);
  useFrameProgressChange(control.progress, p => { keyboardPanel.set(-1); setActive(activeAt(p)); });

  useEffect(() => {
    const view = viewport.current, product = board.current;
    if (!view || !product) return;
    let frame = 0;
    const measure = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => geometry.set({ width: view.clientWidth, height: view.clientHeight, mobile: view.clientWidth < 600, rects: panels.current.map(panel => panel ? localRect(panel, product) : blank) }));
    };
    const observer = new ResizeObserver(measure);
    observer.observe(view); observer.observe(product);
    panels.current.forEach(panel => panel && observer.observe(panel));
    measure();
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [geometry, match]);

  const camera = useTransform(() => {
    const g = geometry.get(), p = keyboardPanel.get() < 0 ? control.progress.get() : stops[keyboardPanel.get()];
    const values = targets.map(target => {
      const index = match && target === 4 ? 3 : target;
      if (index < 0) return { x: 0, y: 0, scale: 1 };
      const r = g.rects[index];
      const desired = variant === 'closeup' ? [1.38, 1.55, 1.48, 1.32, 1.4][index] : 1;
      const scale = g.mobile ? 1 : Math.min(desired, (g.width - 56) / Math.max(1, r.w), (g.height - 44) / Math.max(1, r.h));
      const x = g.mobile || variant === 'focus' ? 0 : g.width / 2 - (r.x + r.w / 2) * scale;
      const y = g.mobile ? Math.min(0, g.height / 2 - r.y - r.h / 2) : variant === 'focus' ? 0 : g.height / 2 - (r.y + r.h / 2) * scale;
      return { x, y, scale };
    });
    if (reduced) {
      const i = p < .08 ? 0 : p < .3 ? 2 : p < .51 ? 4 : p < .72 ? 6 : p < .915 ? 8 : 10;
      return { ...values[i], scale: 1, x: 0, y: g.mobile ? values[i].y : 0 };
    }
    return interpolate(p, values);
  });
  const cameraX = useTransform(() => camera.get().x * (1 - handoff.get()));
  const cameraY = useTransform(() => camera.get().y * (1 - handoff.get()));
  const cameraScale = useTransform(() => 1 + (camera.get().scale - 1) * (1 - handoff.get()));
  const focus = useTransform(() => {
    const g = geometry.get(), p = keyboardPanel.get() < 0 ? control.progress.get() : stops[keyboardPanel.get()];
    const values = targets.map(target => ({ ...g.rects[target < 0 ? 0 : match && target === 4 ? 3 : target], opacity: target < 0 ? 0 : 1 }));
    if (reduced) return values[p < .08 ? 0 : p < .3 ? 2 : p < .51 ? 4 : p < .72 ? 6 : p < .915 ? 8 : 10];
    return interpolate(p, values);
  });
  const focusX = useTransform(() => focus.get().x);
  const focusY = useTransform(() => focus.get().y);
  const focusW = useTransform(() => focus.get().w);
  const focusH = useTransform(() => focus.get().h);
  const focusOpacity = useTransform(() => focus.get().opacity * (1 - handoff.get()));
  const tourOpacity = useTransform(handoff, [0, 1], [1, 0]);
  const continuationOpacity = useTransform(handoff, [0, .5, 1], [0, .85, 1]);
  const evidence = useTransform(control.progress, [.31, .38, .93, .97], [0, 1, 1, 1]);
  const evidenceWidth = useTransform(() => `${evidence.get() * 100}%`);
  const evaluation = useTransform(control.progress, [.53, .59, .93, .97], [0, 1, 1, 1]);
  const evaluationWidth = useTransform(() => `${evaluation.get() * 100}%`);
  const connection = useTransform(control.progress, [.735, .84], [0, 1]);
  const connectionWidth = useTransform(() => `${connection.get() * 100}%`);
  const select = (index: number) => control.seek(stops[index]);
  const buttonProps = (index: number) => ({
    ref: (node: HTMLButtonElement | null) => { panels.current[index] = node; },
    type: 'button' as const,
    onClick: () => select(index),
    onFocus: (event: React.FocusEvent<HTMLButtonElement>) => {
      if (event.currentTarget.matches(':focus-visible')) { keyboardPanel.set(index); setActive(index); }
    },
    onBlur: () => { keyboardPanel.set(-1); setActive(activeAt(control.progress.get())); },
    'aria-pressed': active === index,
  });
  const titles = [
    <>Интервью уже у вас.<br/><em>Смотрите в удобное время.</em></>,
    <>Главное — в одном ответе.<br/><em>С таймкодом в записи.</em></>,
    <>Что уже умеет.<br/><em>Что предстоит освоить.</em></>,
    <>Под вашу задачу.<br/><em>И вот почему.</em></>,
    match ? <>Нужный опыт подтверждён.<br/><em>В его собственных ответах.</em></> : <>Битрикс24 нужно освоить.<br/><em>Это важно для вас?</em></>,
  ];
  const title = continuation?.active ? continuation.title : active < 0 ? <>Все интервью проведены.<br/><em>Все кандидаты оценены.</em></> : titles[active];

  return <section className={`interview-tour it-${variant}`} aria-labelledby={`${uid}-title`} data-candidate={contextPerson.name} data-focus={active} data-continuation={continuation?.active || undefined}>
    <h2 id={`${uid}-title`}>{title}</h2>
    <div className="it-viewport" ref={viewport}>
      <motion.div className="it-camera" style={{ x: cameraX, y: cameraY, scale: cameraScale }}>
        <div className="it-product" ref={board}>
          <div className="it-toolbar"><img src={LOGO_URL} alt="HR Vision"/><span>Менеджер по работе с клиентами</span>{onFeedback && <button className="it-feedback-link" onClick={onFeedback}>Уточнить подбор</button>}</div>
          <div className="it-workspace">
            <aside className="it-sidebar" aria-hidden="true"><span className="it-nav-active"><ClipboardList/>Задачи</span><span><Users/>Кандидаты</span><span><CalendarDays/>Встречи</span><span><FileText/>Офферы</span></aside>
            <div className="it-content">
              <div className={`it-candidate ${continuation?.match || match ? 'it-candidate-updated' : ''}`}><span className="it-avatar">{contextPerson.initials}</span><div><h3>{contextPerson.name}</h3><span>{contextPerson.role} · {contextPerson.exp}</span></div><span className="it-done"><CheckCheck size={15}/>{continuation?.match || match ? 'С учётом вашего ответа' : 'Интервью разобрано'}</span></div>
              <div className="it-sequence">
              <motion.div className="it-tour-panels" style={{ opacity: tourOpacity }} aria-hidden={continuation?.active} inert={continuation?.active}>
              <div className="it-grid">
                <div className="it-left">
                  <button {...buttonProps(0)} className="it-record it-panel" aria-label="Рассмотреть запись интервью">
                    <span className="it-panel-top"><span><CirclePlay size={16}/>Запись интервью</span><span className="it-record-ready"><Check size={13}/>Проведено</span></span>
                    <span className="it-video"><span className="it-portrait" aria-hidden="true"><span/><i/></span><span className="it-play"><Play size={18} fill="currentColor"/></span><span className="it-video-name">{person.name}</span><span className="it-video-time">28:40</span></span>
                    <span className="it-timeline" aria-hidden="true"><Play size={12}/><span>{match?.time ?? '04:20'}</span><i><b/><b/><b/><b/></i><span>28:40</span></span>
                  </button>
                  <button {...buttonProps(1)} className="it-answers it-panel" aria-label="Рассмотреть ключевой ответ">
                    <span className="it-panel-top"><span><MessageSquare size={15}/>{match?.criterion ?? 'Возвращение клиентов'}</span><span className="it-time">{match?.time ?? interviewChapters[1].time}</span></span>
                    <span className="it-quote">{match ? <>«{match.quote.split(match.marked)[0]}<span className="it-mark"><motion.i style={{ width: evidenceWidth }}/><span>{match.marked}</span></span>{match.quote.split(match.marked)[1]}»</> : <>«Сначала выясняла, почему клиент перестал заказывать. <span className="it-mark"><motion.i style={{ width: evidenceWidth }}/><span>Делила базу по причинам</span></span> и готовила своё предложение».</>}</span>
                  </button>
                </div>
                <button {...buttonProps(2)} className="it-assessment it-panel" aria-label="Рассмотреть оценку кандидата">
                  <span className="it-panel-top"><span><ListChecks size={17}/>Оценка рекрутера</span><CheckCheck size={15}/></span>
                  <span className="it-assessment-title">Что уже умеет.<br/>Что предстоит освоить.</span>
                  {strengths.map(strength => <span className="it-criterion" key={strength.title}><span className="it-criterion-check"><Check size={14}/></span><span><strong>{strength.title}</strong><span>{strength.detail}</span></span></span>)}
                  <span ref={node => { panels.current[4] = node; }} className={`it-criterion ${match ? '' : 'it-caution'}`}><span className="it-criterion-check">{match ? <Check size={14}/> : <Minus size={14}/>}</span><span><strong>{match?.skill ?? 'Битрикс24'}</strong><span className="it-mark"><motion.i style={{ width: evaluationWidth }}/><span>{match?.proof ?? 'Понадобится освоение'}</span></span></span></span>
                </button>
              </div>
              <button {...buttonProps(3)} className="it-fit it-panel" aria-label={`Рассмотреть почему предлагаем ${match?.accusative ?? 'Анну'}`}>
                <span className="it-fit-task"><span>Ваша задача</span><strong>Вернуть ушедших клиентов</strong></span>
                <span className="it-fit-bridge" aria-hidden="true"><span/><motion.i style={{ width: connectionWidth }}/><ArrowRight size={18}/></span>
                <span className="it-fit-reason"><span>Почему предлагаем {match?.accusative ?? 'Анну'}</span><strong>{match?.reason ?? 'Она уже решала такую задачу.'}</strong><span>{match?.reasonDetail ?? 'И хочет развивать вашу базу.'}</span></span>
              </button>
              </motion.div>
              {continuation && <motion.div className="it-continuation" style={{ opacity: continuationOpacity }} aria-hidden={!continuation.active} inert={!continuation.active}>{continuation.content}</motion.div>}
              </div>
            </div>
          </div>
          <motion.div className="it-spotlight" aria-hidden="true" style={{ left: focusX, top: focusY, width: focusW, height: focusH, opacity: focusOpacity }}/>
        </div>
      </motion.div>
    </div>
  </section>;
}
