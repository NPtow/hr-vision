import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { MotionConfig, useAnimationFrame, useMotionValue, useMotionValueEvent, useReducedMotion } from 'motion/react';
import { SceneRuntime } from './SceneRuntime';
import { CHANNEL, sceneById, type SceneId, type Command } from './scenes';
import { interestVariantById, type InterestVariant } from './interest-variants';
import { interviewVariantById, type InterviewVariant } from './interview-variants';
import { feedbackVariantById, type FeedbackVariant } from './feedback-variants';
import { ctaVariantById, type CtaVariant } from './cta-variants';
import './stage.css';

export type SceneHostProps = { scene: SceneId; playing?: boolean; runId?: number; compact?: boolean; interestVariant?: InterestVariant; interviewVariant?: InterviewVariant; feedbackVariant?: FeedbackVariant; ctaVariant?: CtaVariant };
export function SceneHost({ scene: initialScene, playing: initialPlaying = false, runId: externalRun = 0, compact = false, interestVariant = 'original', interviewVariant = 'original', feedbackVariant = 'original', ctaVariant = 'perimeter' }: SceneHostProps) {
  const [scene, setScene] = useState(initialScene);
  const [playing, setPlaying] = useState(initialPlaying);
  const [runId, setRunId] = useState(0);
  const [speed, setSpeed] = useState(1);
  const [display, setDisplay] = useState(0);
  const progress = useMotionValue(0);
  const lastNotice = useRef(0);
  const reduced = useReducedMotion();
  const embedded = new URLSearchParams(window.location.search).get('lab') === '1';
  const definition = sceneById(scene);
  const duration = scene === 'cta' ? ctaVariantById(ctaVariant).duration : scene === 'interest' ? interestVariantById(interestVariant).duration : scene === 'interview' ? interviewVariantById(interviewVariant).duration : scene === 'feedback' ? feedbackVariantById(feedbackVariant).duration : definition.duration;
  const notify = useCallback((kind: string, payload: Record<string, unknown> = {}) => {
    if(embedded && window.parent !== window) window.parent.postMessage({ channel: CHANNEL, kind, scene, ...payload }, window.location.origin);
  }, [embedded, scene]);
  const pause = useCallback(() => setPlaying(false), []);
  const play = useCallback(() => { if(progress.get() >= 1) { progress.set(0); setRunId(v=>v+1); } setPlaying(true); }, [progress]);
  const toggle = useCallback(() => { if(playing) pause(); else play(); }, [playing, pause, play]);
  const seek = useCallback((value: number) => { setPlaying(false); progress.set(Math.max(0,Math.min(1,value))); }, [progress]);
  const reset = useCallback((start = false) => { setPlaying(start); progress.set(0); setRunId(v=>v+1); window.scrollTo(0,0); }, [progress]);
  const control = useMemo(() => ({ progress, playing, play, pause, toggle, seek }), [progress,playing,play,pause,toggle,seek]);
  useAnimationFrame((_time,delta) => {
    if(!playing) return;
    const next = Math.min(1, progress.get() + Math.min(delta,64) * speed / (duration * 1000));
    progress.set(next);
    if(next >= 1) setPlaying(false);
  });
  useMotionValueEvent(progress, 'change', value => {
    const time = performance.now();
    if(time-lastNotice.current > 70 || value===0 || value===1 || !playing) {
      lastNotice.current=time; setDisplay(value);
      notify('state', { progress:value, playing, speed, runId });
    }
  });
  useEffect(() => { notify('state', { progress:progress.get(), playing, speed, runId }); },[notify,playing,speed,runId,progress]);
  useEffect(() => { setScene(initialScene); reset(false); },[initialScene,externalRun,interestVariant,interviewVariant,feedbackVariant,ctaVariant,reset]);
  useEffect(() => { setPlaying(initialPlaying && !reduced); },[initialPlaying,reduced]);
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if(event.origin !== window.location.origin || event.source !== window.parent || event.data?.channel !== CHANNEL || event.data?.scene !== scene) return;
      const command = event.data.command as Command | undefined;
      if(!command || typeof command !== 'object') return;
      if(command.kind === 'play') play();
      if(command.kind === 'pause') pause();
      if(command.kind === 'reset') reset();
      if(command.kind === 'replay') reset(true);
      if(command.kind === 'seek' && Number.isFinite(command.value)) seek(command.value);
      if(command.kind === 'speed' && [.5,1,1.5,2].includes(command.value)) setSpeed(command.value);
    };
    window.addEventListener('message', receive);
    notify('ready');
    return () => window.removeEventListener('message', receive);
  },[scene,notify,play,pause,seek,reset]);
  useEffect(()=>{
    const escape=(event:KeyboardEvent)=>{if(event.key==='Escape')notify('escape');};
    window.addEventListener('keydown',escape);
    return()=>window.removeEventListener('keydown',escape);
  },[notify]);
  const navigate = useCallback((id: SceneId) => { if(embedded) notify('navigate',{target:id}); else {setScene(id); reset(false);} },[embedded,notify,reset]);
  return <MotionConfig reducedMotion="user"><div className={`scene-host ${embedded ? 'embedded' : 'standalone'}`}>
    {!embedded && <div className="standalone-heading"><strong>{definition.title}</strong></div>}
    <SceneRuntime key={`${scene}-${interestVariant}-${interviewVariant}-${feedbackVariant}-${ctaVariant}-${runId}`} id={scene} control={control} compact={compact} onNavigate={navigate} interestVariant={interestVariant} interviewVariant={interviewVariant} feedbackVariant={feedbackVariant} ctaVariant={ctaVariant}/>
    {!embedded && <div className="standalone-controls"><button onClick={()=>reset(true)}>Повторить</button><button onClick={toggle}>{playing?'Пауза':'Воспроизвести'}</button><input aria-label="Прогресс анимации" type="range" min="0" max="1000" value={Math.round(display*1000)} onChange={e=>seek(Number(e.target.value)/1000)}/><span>{Math.round(display*100)}%</span></div>}
  </div></MotionConfig>;
}
