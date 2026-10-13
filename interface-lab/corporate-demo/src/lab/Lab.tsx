import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUpRight, ChevronLeft, ChevronRight, Monitor, Pause, Play, RotateCcw, Smartphone, StepForward, X } from 'lucide-react';
import { LOGO_URL } from '../assets';
import { CHANNEL, scenes, sceneById, type SceneId, type LabState, type Command } from './scenes';
import { interestVariants, interestVariantById, interestSteps } from './interest-variants';
import { interviewVariants, interviewVariantById, interviewSteps } from './interview-variants';
import { feedbackVariants, feedbackVariantById, feedbackSteps, feedbackGraphSteps } from './feedback-variants';
import { ctaVariants, ctaVariantById, ctaSteps } from './cta-variants';
import './lab.css';

const initialState: LabState = { progress:0, playing:false, speed:1, runId:0 };
const currentId = () => sceneById(location.hash.slice(1).split('/')[0]).id;
const currentVariant = () => currentId() === 'cta' ? ctaVariantById(location.hash.slice(1).split('/')[1]) : currentId() === 'review-feedback' ? feedbackVariantById('reshuffle') : currentId() === 'feedback' ? feedbackVariantById(location.hash.slice(1).split('/')[1]) : currentId() === 'interview' ? interviewVariantById(location.hash.slice(1).split('/')[1]) : interestVariantById(location.hash.slice(1).split('/')[1]);
export function CompanyMotionLab() {
  const [scene,setScene] = useState<SceneId>(currentId);
  const [variant,setVariant] = useState(currentVariant);
  const [manifest,setManifest] = useState<Record<string,string> | null>(null);
  const [error,setError] = useState('');
  const [ready,setReady] = useState(false);
  const [state,setState] = useState<LabState>(initialState);
  const [device,setDevice] = useState<'desktop'|'mobile'>(()=>innerWidth<640?'mobile':'desktop');
  const [fullscreen,setFullscreen] = useState(false);
  const [scale,setScale] = useState(1);
  const [connectionRun,setConnectionRun] = useState(0);
  const iframe = useRef<HTMLIFrameElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const autoplay = useRef(!matchMedia('(prefers-reduced-motion: reduce)').matches);
  const active = sceneById(scene);
  const variants = scene === 'cta' ? ctaVariants : scene === 'interest' ? interestVariants : scene === 'interview' ? interviewVariants : scene === 'feedback' ? feedbackVariants : null;
  const duration = variants ? variant.duration : active.duration;
  const steps = scene === 'cta' ? ctaSteps : scene === 'feedback' && variant.id === 'graph' ? feedbackGraphSteps : variant.id === 'original' ? active.steps : scene === 'interest' ? interestSteps : scene === 'interview' ? interviewSteps : scene === 'feedback' ? feedbackSteps : active.steps;
  const width=device==='desktop'?1280:390;
  const height=device==='desktop'?720:844;
  const send = useCallback((command: Command) => {
    iframe.current?.contentWindow?.postMessage({channel:CHANNEL,scene,command},location.origin);
  },[scene]);
  const navigate = useCallback((id: SceneId) => { location.hash=id; },[]);
  useEffect(()=>{
    const abort = new AbortController();
    fetch('./lab-manifest.json',{signal:abort.signal,cache:'no-cache'}).then(r=>{if(!r.ok)throw new Error('Не удалось загрузить сцены');return r.json();}).then(data=>{
      if(!scenes.every(s=>typeof data[s.id]==='string') || ![...interestVariants,...interviewVariants,...feedbackVariants,...ctaVariants].every(v=>typeof data[v.story]==='string')) throw new Error('Список сцен неполный');
      setManifest(data);
    }).catch(e=>{if(e.name!=='AbortError')setError('Не удалось открыть сцены. Обновите страницу.');});
    return()=>abort.abort();
  },[]);
  useEffect(()=>{
    const change=()=>{const nextScene=currentId();const nextVariant=currentVariant();if(nextScene!==scene || nextVariant.id!==variant.id){setScene(nextScene);setVariant(nextVariant);setReady(false);setState(initialState);}window.scrollTo(0,0);};
    window.addEventListener('hashchange',change);return()=>window.removeEventListener('hashchange',change);
  },[scene,variant.id]);
  useEffect(()=>{
    document.title=`${active.label} · HR Vision · Анимации`;
    document.querySelector<HTMLAnchorElement>(`[data-scene-link="${scene}"]`)?.scrollIntoView({block:'nearest',inline:'nearest'});
  },[scene,active.label]);
  useEffect(()=>{
    const receive=(event:MessageEvent)=>{
      if(event.origin!==location.origin||event.source!==iframe.current?.contentWindow||event.data?.channel!==CHANNEL||event.data?.scene!==scene)return;
      const data=event.data;
      if(data.kind==='ready'){setReady(true);setError('');if(autoplay.current)send({kind:'play'});}
      if(data.kind==='state' && Number.isFinite(data.progress)) setState({progress:Math.max(0,Math.min(1,data.progress)),playing:!!data.playing,speed:data.speed,runId:data.runId});
      if(data.kind==='navigate' && scenes.some(s=>s.id===data.target))navigate(data.target);
      if(data.kind==='escape')setFullscreen(false);
    };
    window.addEventListener('message',receive);return()=>window.removeEventListener('message',receive);
  },[scene,send,navigate]);
  useEffect(()=>{
    if(ready || !manifest)return;
    const timer=window.setTimeout(()=>setError('Сцена не загрузилась. Попробуйте ещё раз.'),15000);
    return()=>window.clearTimeout(timer);
  },[ready,manifest,scene,device,connectionRun,variant.id]);
  useEffect(()=>{
    if(!stage.current)return;
    const observer=new ResizeObserver(entries=>{const {width:w,height:h}=entries[0].contentRect;setScale(Math.min(w/width,h/height,1));});
    observer.observe(stage.current);return()=>observer.disconnect();
  },[width,height,fullscreen]);
  useEffect(()=>{
    const hide=()=>{if(document.hidden)send({kind:'pause'});};
    const escape=(event:KeyboardEvent)=>{if(event.key==='Escape')setFullscreen(false);};
    document.addEventListener('visibilitychange',hide);window.addEventListener('keydown',escape);
    return()=>{document.removeEventListener('visibilitychange',hide);window.removeEventListener('keydown',escape);};
  },[send]);
  const selectedStep = steps.reduce((result,step,i)=>state.progress>=step[0]-.01?i:result,0);
  const step = (direction: number) => {
    const target = direction>0 ? steps.find(s=>s[0]>state.progress+.015)?.[0] ?? 1 : [...steps].reverse().find(s=>s[0]<state.progress-.015)?.[0] ?? 0;
    send({kind:'seek',value:target});
  };
  const selectDevice=(next:'desktop'|'mobile')=>{if(device===next)return;send({kind:'pause'});autoplay.current=false;setDevice(next);setReady(false);setState(initialState);};
  const source=manifest?.[variants ? variant.story : scene];
  return <div className={`motion-lab ${fullscreen?'is-fullscreen':''}`}>
    <header className="motion-lab-header"><a className="motion-lab-brand" href="#intro"><img src={LOGO_URL} width="143" height="23" alt="HR Vision"/><span>Анимации</span></a><nav aria-label="Сцены сайта">{scenes.map(s=><a key={s.id} data-scene-link={s.id} href={`#${s.id}`} aria-current={scene===s.id?'page':undefined}>{s.label}</a>)}</nav></header>
    <main className="motion-lab-main"><div className="motion-lab-heading"><div><h1>{active.title}</h1></div><a href="./#top" target="_blank" rel="noreferrer">Весь сайт <ArrowUpRight size={15}/></a></div>
      {variants && <nav className="motion-lab-variants" aria-label={scene === 'cta' ? 'Варианты анимации кнопки' : scene === 'interest' ? 'Варианты анимации отправки' : scene === 'feedback' ? 'Варианты анимации фидбека' : 'Варианты разбора интервью'}>{variants.map(v=><a key={v.id} href={`#${scene}/${v.id}`} aria-current={variant.id===v.id?'page':undefined}>{v.label}</a>)}</nav>}
      {scene === 'cta' && <div className="motion-lab-cta-note"><span>Демо · 10 с со скидкой 50% → 30 с со скидкой 30%</span><a href="https://github.com/vydimitrov/react-countdown-circle-timer" target="_blank" rel="noreferrer">Countdown Circle Timer</a><a href="https://number-flow.barvian.me/" target="_blank" rel="noreferrer">NumberFlow</a></div>}
      {scene === 'connections' && <a className="motion-lab-interest-link" href="#interest/letters">Отправка и отклики · 3 варианта <ArrowUpRight size={14}/></a>}
      <div className="motion-lab-view"><div className="motion-lab-stage" ref={stage} data-device={device}>
        {source&&<iframe key={`${scene}-${variant.id}-${device}-${connectionRun}`} ref={iframe} className="motion-lab-frame" title={`${active.title} — интерактивная сцена`} src={`./storybook/iframe.html?id=${encodeURIComponent(source)}&viewMode=story&lab=1`} style={{width,height,transform:`translate(-50%,-50%) scale(${scale})`}}/>}
        {!ready&&<div className="motion-lab-loading" role="status">{error||'Открываем сцену…'}{error&&<button onClick={()=>{if(!manifest){location.reload();return;}setError('');setConnectionRun(v=>v+1);}}>Повторить</button>}</div>}

        {fullscreen&&<button className="motion-lab-close" onClick={()=>setFullscreen(false)} aria-label="Закрыть полный экран"><X size={19}/></button>}
      </div>
      <div className="motion-lab-player" aria-label="Управление анимацией"><div className="motion-lab-play-buttons">
        <button onClick={()=>send({kind:'replay'})} disabled={!ready}><RotateCcw size={15}/>Повторить</button>
        <button className="play-control" onClick={()=>send({kind:state.playing?'pause':'play'})} disabled={!ready}>{state.playing?<Pause size={15}/>:<Play size={15}/>}<span>{state.playing?'Пауза':'Смотреть'}</span></button>
        <button onClick={()=>step(1)} disabled={!ready||state.progress>=1}><StepForward size={15}/><span>Следующий шаг</span></button>
      </div><div className="motion-lab-scrub"><input type="range" min="0" max="1000" step="1" value={Math.round(state.progress*1000)} aria-label="Позиция анимации" disabled={!ready} onChange={e=>send({kind:'seek',value:Number(e.target.value)/1000})}/><span>{(state.progress*duration).toFixed(1)} <i>/ {duration} с</i></span></div>
      <label className="motion-lab-speed"><span className="visually-hidden">Скорость анимации</span><select aria-label="Скорость анимации" value={state.speed} onChange={e=>send({kind:'speed',value:Number(e.target.value)})}>{[.5,1,1.5,2].map(rate=><option key={rate} value={rate}>{rate}×</option>)}</select></label>
      <div className="motion-lab-devices" role="group" aria-label="Размер экрана"><button aria-label="Компьютер" aria-pressed={device==='desktop'} onClick={()=>selectDevice('desktop')}><Monitor size={16}/></button><button aria-label="Телефон" aria-pressed={device==='mobile'} onClick={()=>selectDevice('mobile')}><Smartphone size={16}/></button></div>
      <button className="motion-lab-expand" onClick={()=>setFullscreen(v=>!v)}>{fullscreen?'Свернуть':'Развернуть'}<ArrowUpRight size={14}/></button>
      </div><div className="motion-lab-steps" aria-label="Этапы сцены">{steps.map(([position,label],i)=><button key={label} disabled={!ready} onClick={()=>send({kind:'seek',value:position})} aria-pressed={i===selectedStep}><span>{String(i+1).padStart(2,'0')}</span>{label}</button>)}</div></div>
      <div className="motion-lab-footer"><span>{String(scenes.findIndex(s=>s.id===scene)+1).padStart(2,'0')} / {scenes.length}</span><div>{source&&<a href={`./storybook/?path=/story/${source}`} target="_blank" rel="noreferrer">Storybook <ArrowUpRight size={13}/></a>}<button aria-label="Предыдущая сцена" disabled={scene===scenes[0].id} onClick={()=>navigate(scenes[Math.max(0,scenes.findIndex(s=>s.id===scene)-1)].id)}><ChevronLeft size={16}/></button><button aria-label="Следующая сцена" disabled={scene===scenes[scenes.length-1].id} onClick={()=>navigate(scenes[Math.min(scenes.length-1,scenes.findIndex(s=>s.id===scene)+1)].id)}><ChevronRight size={16}/></button></div></div>
    </main>
  </div>;
}
