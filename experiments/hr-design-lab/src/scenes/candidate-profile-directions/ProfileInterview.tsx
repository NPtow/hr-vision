import {useEffect,useMemo,useRef,useState} from 'react';
import {ArrowUpRight,Maximize2,Pause,Play,Video} from 'lucide-react';
import {CandidateInterviewPlayer} from './vendor/interview-review/CandidateInterviewPlayer';
import type {InterviewAssistantRequest} from './vendor/interview-review/interview-assistant-api';
import {formatTime,skills,type Candidate,type Skill} from './model';

const media='/media/candidate-interview-highlights/sample-interview.mp4';
export const profileClips=[
 {skillId:'A03',start:0,end:8,title:'Пилот — ещё не контракт',question:'Как вы понимаете, что сделку стоит вести дальше?'},
 {skillId:'A04',start:8,end:16,title:'Сначала — задача клиента',question:'Как вы выясняете, что нужно клиенту?'},
 {skillId:'A06.1',start:16,end:24,title:'«Дорого» — начало разговора',question:'Что делаете, когда клиент говорит: «Дорого»?'},
 {skillId:'A06.2',start:24,end:32,title:'Скидка в обмен на условие',question:'Как договариваетесь о цене?'}
];
export type ProfileClip=typeof profileClips[number];
export const clipForSkill=(id:string)=>profileClips.find(clip=>clip.skillId===id);

/** UI-only footage: a silent animatic borrowed from the highlights prototype. */
export function SkillVideo({skill,onExpand}:{skill:Skill;onExpand:(seconds:number)=>void}){
 const clip=clipForSkill(skill.parameter_id),video=useRef<HTMLVideoElement>(null);
 const [playing,setPlaying]=useState(false),[position,setPosition]=useState(clip?.start||0),[error,setError]=useState(false);
 useEffect(()=>{setPlaying(false);setError(false);setPosition(clip?.start||0);if(video.current){video.current.pause();if(video.current.readyState>=1)video.current.currentTime=clip?.start||0;}},[clip?.start,skill.parameter_id]);
 useEffect(()=>{
  const el=video.current;
  if(!el)return;
  const pauseOther=(event:Event)=>{if((event as CustomEvent).detail!==el)el.pause();};
  window.addEventListener('hr-vision-interview-play',pauseOther);
  return()=>{window.removeEventListener('hr-vision-interview-play',pauseOther);el.pause();};
 },[clip?.skillId]);
 function expand(){const el=video.current;const at=el&&el.readyState>=1?el.currentTime:position;el?.pause();onExpand(at);}
 function toggle(){const el=video.current;if(!el)return;if(!el.paused){el.pause();return;}if(clip&&el.currentTime>=clip.end-.1)el.currentTime=clip.start;void el.play().catch(()=>setError(true));}
 if(!clip||!skill.quote)return <div className="cpds-no-fragment"><Video size={24}/><span>{skill.value===null?'Пока нет материала для оценки':'Фрагмент пока не выбран'}</span></div>;
 return <div className="cpds-skill-video">
  <div className="cpds-clip-stage">
   <video ref={video} src={media} preload="metadata" muted playsInline aria-label={`Фрагмент: ${skill.short}`}
    onLoadedMetadata={e=>{e.currentTarget.currentTime=clip.start;}}
    onPlay={()=>{setPlaying(true);window.dispatchEvent(new CustomEvent('hr-vision-interview-play',{detail:video.current}));}}
    onPause={()=>setPlaying(false)} onError={()=>setError(true)}
    onTimeUpdate={e=>{setPosition(e.currentTarget.currentTime);if(e.currentTarget.currentTime>=clip.end){e.currentTarget.pause();if(e.currentTarget.currentTime>clip.end+.05)e.currentTarget.currentTime=clip.end-.02;}}}/>
   <button className="cpds-clip-play-area" onClick={toggle} aria-label={playing?'Пауза фрагмента':`Смотреть фрагмент: ${skill.short}`}>{!playing&&<span><Play size={21} fill="currentColor"/></span>}</button>
   <button className="cpds-clip-expand" onClick={expand} aria-label="Открыть полное интервью с этого момента"><Maximize2 size={17}/></button>
   {error&&<p className="cpds-clip-error">Не удалось загрузить видео.</p>}
  </div>
  <div className="cpds-clip-controls"><button aria-label={playing?'Пауза фрагмента':'Воспроизвести фрагмент'} onClick={toggle}>{playing?<Pause size={13}/>:<Play size={13}/>}</button><input type="range" min={clip.start} max={clip.end-.02} step=".05" value={Math.min(position,clip.end-.02)} aria-label="Позиция во фрагменте" onChange={e=>{if(video.current)video.current.currentTime=Number(e.target.value);setPosition(Number(e.target.value))}}/><span>{formatTime(Math.max(0,Math.min(position-clip.start,8)))} / 00:08</span></div>
  <button className="cpds-clip-open" onClick={expand}>{clip.title}<ArrowUpRight size={15}/></button>
 </div>;
}

export function InterviewHighlights({onOpen,onSkill}:{onOpen:(skillId:string,seconds:number)=>void;onSkill:(id:string)=>void}){
 return <section className="cpds-highlights cpds-surface" aria-label="Фрагменты интервью">
  <div className="cpds-section-heading"><h3>Из интервью</h3><button className="cpds-text-button" onClick={()=>onOpen(profileClips[0].skillId,0)}>Весь разговор<ArrowUpRight size={14}/></button></div>
  <div className="cpds-highlight-grid">{profileClips.map((clip,index)=><article key={clip.skillId}>
   <button className={'cpds-highlight-watch cpds-clip-tone-'+index} onClick={()=>onOpen(clip.skillId,clip.start)} aria-label={`Смотреть: ${clip.title}`}><span><Play size={15} fill="currentColor"/></span><small>00:08</small><strong>{clip.title}</strong></button>
   <button className="cpds-highlight-skill" onClick={()=>onSkill(clip.skillId)}>{skills.find(s=>s.parameter_id===clip.skillId)?.short}<ArrowUpRight size={12}/></button>
  </article>)}</div>
 </section>;
}

export function FullInterview({person,initialSeconds,onSkill}:{person:Candidate;initialSeconds:number;onSkill:(id:string)=>void}){
 const [request,setRequest]=useState(()=>({recordingId:'profile-demo',seconds:initialSeconds,requestId:crypto.randomUUID()}));
 const previousEntry=useRef(initialSeconds);
 useEffect(()=>{
  if(previousEntry.current===initialSeconds)return;
  previousEntry.current=initialSeconds;
  setRequest({recordingId:'profile-demo',seconds:initialSeconds,requestId:crypto.randomUUID()});
 },[initialSeconds]);
 useEffect(()=>setRequest({recordingId:'profile-demo',seconds:initialSeconds,requestId:crypto.randomUUID()}),[initialSeconds]);
 const rows=useMemo(()=>profileClips.map(clip=>({...clip,skill:skills.find(s=>s.parameter_id===clip.skillId)!})),[]);
 const transcript=useMemo(()=>'WEBVTT\n\n'+rows.map((row,index)=>`${index+1}\n00:${formatTime(row.start)}.000 --> 00:${formatTime(row.end)}.000\n<v ${person.name}>${row.skill.quote}\n`).join('\n'),[rows,person.name]);
 const chapters=useMemo(()=>rows.map(row=>({title:row.title,detail:row.question,fragment:row.skill.quote,source:formatTime(row.start),seconds:row.start,endSeconds:row.end,speaker:person.name,isQuote:true})),[rows,person.name]);
 const assistant=useMemo<InterviewAssistantRequest>(()=>async(action,query,_requestId,signal)=>{
  if(signal.aborted)throw new DOMException('Aborted','AbortError');
  const words=query.toLowerCase().split(/[^а-яёa-z0-9]+/).filter(w=>w.length>3);
  const chosen=rows.filter(row=>words.some(word=>(row.title+' '+row.skill.title+' '+row.skill.quote).toLowerCase().includes(word.slice(0,5))));
  if(action==='search')return {mode:'search',matches:chosen.map(row=>({id:row.skillId,text:row.skill.quote,startMs:row.start*1000,endMs:row.end*1000,speakerName:person.name})),totalMatches:chosen.length,coverage:'demo',model:'local-demo',version:'1'};
  if(!chosen.length)return {mode:'chat',text:'Это локальный пример чата. Попробуйте спросить про возражения, потребности или цену — покажу подготовленный ответ с источником.',supported:false,evidence:[],coverage:{selected:0,omitted:0},model:'local-demo',version:'1'};
  const row=chosen[0],text=row.skill.quote;
  return {mode:'chat',text,parts:[{text,sourceIds:[row.skillId]}],supported:true,evidence:[{id:row.skillId,quote:text,startMs:row.start*1000,endMs:row.end*1000,speakerName:person.name}],coverage:{selected:1,omitted:0},model:'local-demo',version:'1'};
 },[rows,person.name]);
 return <div className="cpds-full-interview">
  <CandidateInterviewPlayer sourceId="profile-demo" recordingId="profile-demo" kind="screening" name={person.name} media={{src:media,localPreview:true}} chapters={chapters} fullChapters={chapters} transcriptText={transcript} transcriptSeekable assistant={assistant} evidenceRequest={request} hideFeedbackQuestions/>
  <div className="cpds-interview-moments">{rows.map(row=><button key={row.skillId} aria-label={`Перейти к фрагменту: ${row.title}`} onClick={()=>{setRequest({recordingId:'profile-demo',seconds:row.start,requestId:crypto.randomUUID()});onSkill(row.skillId)}}><Play size={12}/><span>{row.title}</span><time>{formatTime(row.start)}</time></button>)}</div>
  <p className="cpds-player-demo-note">Макет: видео без звука, цитаты вымышлены. Чат отвечает локально по подготовленным фрагментам.</p>
 </div>;
}
