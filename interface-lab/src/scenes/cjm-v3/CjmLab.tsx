import {useEffect,useRef,useState} from 'react';
import {ArrowDownRight,ArrowLeft,ArrowRight,ArrowUpRight,Check,ChevronDown,ChevronRight,GitBranch,Layers,Link2,PanelRightClose,RotateCcw,Users,X} from 'lucide-react';
import {actors,chapters,screens,stages,strategies,type ActorId,type CjmJob,type CjmScreen,type StrategyId} from './data';
import './cjm.css';

type Selection={chapter:number|null;actor:ActorId|null;job:string|null;screen:string|null;screens:boolean};
const clean:Selection={chapter:null,actor:null,job:null,screen:null,screens:false};
const actorName=(id:ActorId)=>actors.find(a=>a.id===id)?.name??id;
const chapterOf=(stage:number)=>chapters.find(c=>c.stages.includes(stage))!.id;
const storyUrl=(strategy:StrategyId)=>`/iframe.html?id=hr-vision-cjm-archive--${strategy}&viewMode=story`;
function readSelection(strategy:StrategyId):Selection{
 let value:Partial<Selection>={};
 try{value=JSON.parse(sessionStorage.getItem(`hr-cjm-v3-${strategy}`)??'{}')}catch{/* A blocked storage does not prevent reading the map. */}
 if(location.hash){const p=new URLSearchParams(location.hash.slice(1));value={chapter:p.has('chapter')?Number(p.get('chapter')):null,actor:p.get('actor') as ActorId|null,job:p.get('job'),screen:p.get('screen'),screens:p.get('screens')==='1'}}
 const jobs=strategies.find(s=>s.id===strategy)!.jobs;
 return {chapter:chapters.some(c=>c.id===value.chapter)?value.chapter!:null,actor:actors.some(a=>a.id===value.actor)?value.actor!:null,job:jobs.some(j=>j.id===value.job)?value.job!:null,screen:screens.some(s=>s.id===value.screen)&&jobs.some(j=>j.screenIds.includes(value.screen!))?value.screen!:null,screens:!!value.screens};
}
function selectionHash(value:Selection){const p=new URLSearchParams();if(value.chapter!==null)p.set('chapter',String(value.chapter));if(value.actor)p.set('actor',value.actor);if(value.job)p.set('job',value.job);if(value.screen)p.set('screen',value.screen);if(value.screens)p.set('screens','1');return p.size?`#${p}`:''}
const summaries:Record<ActorId,string[]>={
 manager:['Понять проблему бизнеса','Определить, кого нанять','Получить и проверить кандидатов','Сравнить и выбрать','Согласовать условия и старт','Проверить результат'],
 hr:['Уточнить потребность','Подготовить запрос','Организовать поток и проверки','Свести оценку и решение','Подготовить оффер и выход','Зафиксировать исход'],
 agency:['Найти потребность','Подготовить подбор','Найти людей и доказательства','Передать результат клиенту','Сопроводить договорённости','Обновить результат'],
 candidate:['Понять желаемые изменения','Рассказать о себе','Проверить интерес и показать опыт','Продолжать другие процессы','Сравнить условия и выйти','Проверить ожидания'],
};

export function CjmLab({initialStrategy='agency'}:{initialStrategy?:StrategyId}){
 const [selection,setSelection]=useState<Selection>(()=>readSelection(initialStrategy));
 const [expanded,setExpanded]=useState<string|null>(null);
 const [copied,setCopied]=useState(false);
 const [linkFallback,setLinkFallback]=useState(false);
 const inspectorRef=useRef<HTMLElement>(null);
 const boardRef=useRef<HTMLDivElement>(null);
 const returnFocusRef=useRef<HTMLElement|null>(null);
 const strategy=strategies.find(s=>s.id===initialStrategy)!;
 const jobs=strategy.jobs;
 const selectedJob=jobs.find(j=>j.id===selection.job);
 const selectedScreen=screens.find(s=>s.id===selection.screen);
 const chapter=chapters.find(c=>c.id===selection.chapter);
 const visibleJobs=chapter?jobs.filter(j=>chapter.stages.includes(j.stage)):jobs;
 const availableScreens=screens.filter(s=>jobs.some(j=>j.screenIds.includes(s.id)));
 const related=new Set(selectedJob?[...selectedJob.relatedIds,...selectedJob.previousIds,...selectedJob.nextIds]:[]);
 const panelOpen=!!selectedJob||!!selectedScreen;
 useEffect(()=>{setSelection(readSelection(initialStrategy));setExpanded(null)},[initialStrategy]);
 useEffect(()=>{try{sessionStorage.setItem(`hr-cjm-v3-${initialStrategy}`,JSON.stringify(selection))}catch{};history.replaceState(null,'',`${location.pathname}${location.search}${selectionHash(selection)}`)},[selection,initialStrategy]);
 useEffect(()=>{const sync=()=>{setSelection(readSelection(initialStrategy));setExpanded(null)};window.addEventListener('hashchange',sync);return()=>window.removeEventListener('hashchange',sync)},[initialStrategy]);
 useEffect(()=>{inspectorRef.current?.scrollTo({top:0});if(panelOpen&&window.innerWidth<=800){inspectorRef.current?.scrollIntoView({block:'start',behavior:'instant'});inspectorRef.current?.focus({preventScroll:true})}},[selection.job,selection.screen]);
 useEffect(()=>{if(!panelOpen)return;const escape=(e:KeyboardEvent)=>{if(e.key==='Escape'){setSelection(s=>({...s,job:null,screen:null}));requestAnimationFrame(()=>returnFocusRef.current?.isConnected&&returnFocusRef.current.focus({preventScroll:true}))}};window.addEventListener('keydown',escape);return()=>window.removeEventListener('keydown',escape)},[panelOpen]);
 useEffect(()=>{if(!copied)return;const timer=setTimeout(()=>setCopied(false),2200);return()=>clearTimeout(timer)},[copied]);
 const set=(patch:Partial<Selection>)=>{setSelection(s=>({...s,...patch}));setExpanded(null);setLinkFallback(false)};
 const showJob=(job:CjmJob)=>{returnFocusRef.current=document.activeElement as HTMLElement;set({job:job.id,screen:null,chapter:chapterOf(job.stage)})};
 const showChapter=(id:number|null)=>{set({chapter:id,job:null,screen:null});if(window.scrollY>300)requestAnimationFrame(()=>boardRef.current?.closest('.cjm-workspace')?.scrollIntoView({block:'start',behavior:'instant'}))};
 const showScreen=(screen:CjmScreen)=>{returnFocusRef.current=document.activeElement as HTMLElement;set({screen:screen.id,job:null,screens:true})};
 const closePanel=()=>{set({job:null,screen:null});requestAnimationFrame(()=>returnFocusRef.current?.isConnected&&returnFocusRef.current.focus())};
 const reset=()=>{setSelection({...clean});setExpanded(null);setLinkFallback(false)};
 const dim=(job:CjmJob)=>{
  if(selectedScreen)return !job.screenIds.includes(selectedScreen.id);
  if(selectedJob)return job.id!==selectedJob.id&&!related.has(job.id);
  if(selection.actor)return job.actor!==selection.actor;
  return false;
 };
 async function copyLink(){try{await navigator.clipboard.writeText(location.href);setCopied(true)}catch{setLinkFallback(true)}}
 function jobLinks(ids:string[],empty:string){const items=jobs.filter(j=>ids.includes(j.id));return items.length?<div className="cjm-related">{items.map(j=><button key={j.id} onClick={()=>showJob(j)}><span>{actorName(j.actor)}</span><strong>{j.title}</strong><ArrowUpRight size={14}/></button>)}</div>:<p className="cjm-muted">{empty}</p>}
 function screenPreview(screen:CjmScreen){return <>
  <div className="cjm-preview"><img src={`/hr-cjm/screens/${screen.id.toLowerCase()}.webp`} alt={`Макет «${screen.name}», ${actorName(screen.actor)}`} loading="lazy"/><span>{initialStrategy==='agency'?'Живой макет':'Общий интерфейс · пример агентства'}</span></div>
  {initialStrategy!=='agency'&&<p className="cjm-preview-note">Показан агентский вариант общего интерфейса. Для этой стратегии нужны свои состояния и действия.</p>}
  {screen.future&&<p className="cjm-preview-note">Целевое развитие продукта. Макет показывает предложенное поведение.</p>}
  <ul className="cjm-functions">{screen.functions.map(f=><li key={f}><Check size={13}/>{f}</li>)}</ul>
  {screen.prototypeId&&<a className="cjm-primary" href={`/iframe.html?id=hr-vision-agency--${screen.prototypeId}&viewMode=story`} target="_blank" rel="noreferrer">Открыть макет<ArrowUpRight size={16}/></a>}
 </>}
 return <div className="cjm-app">
  <header className="cjm-topbar"><a className="cjm-brand" href="/iframe.html?id=hr-vision-agency--overview&viewMode=story"><span className="cjm-brandmark">h.</span>HR Vision<span className="cjm-slash">/</span><span className="cjm-brand-caption">Карта процесса</span></a><div className="cjm-top-actions"><a href="/iframe.html?id=hr-vision-agency--overview&viewMode=story">Экраны агентства<ArrowUpRight size={14}/></a><span className="cjm-version">CJM v3</span></div></header>
  <main className="cjm-main">
   <div className="cjm-intro"><div><div className="cjm-eyebrow">ТРИ СТРАТЕГИИ · ОДНА СИСТЕМА КООРДИНАТ</div><h1>Увидеть путь. Понять связи.</h1><p>От проблемы бизнеса до результата найма. Выберите этап, чтобы увидеть работу каждого.</p></div><button className="cjm-share" aria-label={copied?'Ссылка скопирована':'Ссылка на этот вид'} onClick={copyLink}>{copied?<Check size={15}/>:<Link2 size={15}/>}<span>{copied?'Ссылка скопирована':'Ссылка на этот вид'}</span></button></div>
   {linkFallback&&<div className="cjm-link-fallback"><label htmlFor="cjm-copy-url">Скопируйте ссылку</label><input id="cjm-copy-url" readOnly value={location.href} onFocus={e=>e.target.select()}/><button aria-label="Закрыть ссылку" onClick={()=>setLinkFallback(false)}><X size={15}/></button></div>}
   <div className="cjm-strategy-row"><nav className="cjm-strategies" aria-label="GTM стратегия">{strategies.map((s,i)=><a key={s.id} href={storyUrl(s.id)} aria-current={s.id===initialStrategy?'page':undefined}><span>0{i+1}</span>{s.title}</a>)}</nav><p>{strategy.promise}</p></div>
   <div className="cjm-workspace">
    <div className="cjm-toolbar"><div className="cjm-actor-filter" role="group" aria-label="Фокус на участнике"><button className={!selection.actor?'is-active':''} aria-pressed={!selection.actor} onClick={()=>set({actor:null,job:null,screen:null})}><Users size={15}/>Все участники</button>{actors.map(a=><button key={a.id} aria-pressed={selection.actor===a.id} className={selection.actor===a.id?'is-active':''} onClick={()=>set({actor:selection.actor===a.id?null:a.id,job:null,screen:null})}>{a.short}</button>)}</div><div className="cjm-toolbar-right"><button className={selection.screens?'is-active':''} aria-pressed={selection.screens} onClick={()=>set({screens:!selection.screens,screen:null})}><Layers size={15}/>Экраны</button><button aria-label="Сбросить вид карты" title="Сбросить вид карты" onClick={reset}><RotateCcw size={15}/></button></div></div>
    <div className="cjm-chapter-nav"><button className={!chapter?'is-active':''} onClick={()=>showChapter(null)} aria-current={!chapter?'step':undefined}>Весь путь</button>{chapters.map((c,i)=><button key={c.id} className={selection.chapter===c.id?'is-active':''} aria-current={selection.chapter===c.id?'step':undefined} onClick={()=>showChapter(c.id)}><span>0{i+1}</span>{c.title}<ChevronRight size={12}/></button>)}</div>
    {selection.screens&&<div className="cjm-screen-tray"><div><strong>Интерфейсы участников</strong><span>Выберите экран, чтобы увидеть его место в процессе</span></div><div className="cjm-screen-list">{availableScreens.filter(s=>!selection.actor||s.actor===selection.actor).map(s=><button key={s.id} className={selection.screen===s.id?'is-active':''} onClick={()=>showScreen(s)}><span>{s.id}</span>{s.name}</button>)}</div></div>}
    <div className={`cjm-reading-area ${panelOpen?'has-inspector':''}`}>
     <div className="cjm-board" ref={boardRef}>
      <div className={`cjm-board-heading ${!chapter?'is-overview':''}`}><div><span className="cjm-eyebrow">{chapter?`ГЛАВА 0${chapter.id+1} / 06`:'ОБЗОР / 06 ГЛАВ'}</span><h2>{chapter?chapter.title:'Четыре участника. Весь путь.'}</h2><p>{chapter?chapter.description:strategy.description}</p></div>{chapter&&<button onClick={()=>showChapter(null)}><ArrowLeft size={14}/>К обзору</button>}</div>
      {!chapter?<div className="cjm-overview" aria-label="Обзор пути всех участников">
       <div className="cjm-overview-head"><span>Участники</span>{chapters.map(c=><button key={c.id} onClick={()=>showChapter(c.id)}><span>0{c.id+1}</span>{c.title}<ArrowUpRight size={13}/></button>)}</div>
       {actors.map(actor=><div className={`cjm-overview-row ${selection.actor===actor.id?'is-focused':''}`} key={actor.id}><div className="cjm-actor-label"><span className="cjm-avatar">{actor.id==='manager'?'НМ':actor.id==='hr'?'HR':actor.id==='agency'?'А':'К'}</span><strong>{actor.name}</strong></div>{chapters.map(c=>{
        const members=jobs.filter(j=>j.actor===actor.id&&c.stages.includes(j.stage));
        const fade=members.length>0&&members.every(dim);const screenMatch=selectedScreen&&members.some(j=>j.screenIds.includes(selectedScreen.id));
        return <div key={c.id} className={`cjm-overview-cell ${fade?'is-dimmed':''} ${screenMatch?'is-covered':''}`}><span className="cjm-mobile-chapter">0{c.id+1} / {c.title}</span>{members.length?<button onClick={()=>showChapter(c.id)} aria-label={`${actor.name}: ${c.title}`}><strong>{members.length===1?members[0].title:summaries[actor.id][c.id]}</strong><span>{members.length} {members.length===1?'работа':members.length<5?'работы':'работ'}<ArrowUpRight size={13}/></span></button>:<div className="cjm-no-job">{initialStrategy==='contacts'&&actor.id==='agency'?'Найм продолжает компания':'Без отдельного шага'}</div>}{selection.screens&&members.length>0&&<div className="cjm-cell-screens">{Array.from(new Set(members.flatMap(j=>j.screenIds))).map(id=><button key={id} aria-label={`Открыть экран ${id}`} onClick={()=>showScreen(screens.find(s=>s.id===id)!)}>{id}</button>)}</div>}</div>
       })}</div>)}
       <div className="cjm-overview-note"><GitBranch size={16}/><p><strong>У каждого свой темп.</strong> Один кандидат уже обсуждает оффер, пока других ещё ищут. Откройте главу, чтобы увидеть подробности и развилки.</p></div>
      </div>:<div className="cjm-detail" aria-label={`Работы главы ${chapter.title}`}>
       {actors.map(actor=>{const list=visibleJobs.filter(j=>j.actor===actor.id);return <section key={actor.id} className={`cjm-detail-lane ${selection.actor===actor.id?'is-focused':''}`}><div className="cjm-detail-actor"><span className="cjm-avatar">{actor.id==='manager'?'НМ':actor.id==='hr'?'HR':actor.id==='agency'?'А':'К'}</span><div><h3>{actor.name}</h3><span>{actor.description}</span></div></div><div className="cjm-job-list">{list.map(job=><div key={job.id} className={`cjm-job-group ${dim(job)?'is-dimmed':''} ${selectedScreen&&job.screenIds.includes(selectedScreen.id)?'is-covered':''}`}>
        <button className={`cjm-job ${selection.job===job.id?'is-selected':''}`} onClick={()=>showJob(job)} aria-label={`${actor.name}: ${job.title}`}><span className="cjm-stage-label">{stages[job.stage]}</span><strong>{job.title}</strong><span className="cjm-job-description">{job.description}</span><span className="cjm-job-foot">{job.external?'За пределами продукта':'Подробнее'}<ArrowUpRight size={15}/></span></button>
        {job.branches.length>0&&<button className="cjm-branch-toggle" aria-expanded={expanded===job.id} onClick={()=>setExpanded(expanded===job.id?null:job.id)}><GitBranch size={13}/>{job.branches.length} {job.branches.length===1?'вариант продолжения':job.branches.length<5?'варианта продолжения':'вариантов продолжения'}<ChevronDown size={13}/></button>}
        {expanded===job.id&&<div className="cjm-branches">{job.branches.map(branch=><div key={branch.id}><strong>{branch.label}</strong><p>{branch.description}</p>{branch.targetStrategy&&<a className="cjm-branch-link" href={`${storyUrl(branch.targetStrategy)}#chapter=1&job=a1`}>Открыть CJM агентства<ArrowUpRight size={13}/></a>}{branch.targetIds.map(id=>{const target=jobs.find(j=>j.id===id);return target?<button key={id} onClick={()=>showJob(target)}><ArrowDownRight size={13}/>{target.title}</button>:null})}</div>)}</div>}
        {selection.screens&&job.screenIds.length>0&&<div className="cjm-job-screens">{job.screenIds.map(id=><button key={id} onClick={()=>showScreen(screens.find(s=>s.id===id)!)}><Layers size={12}/>{screens.find(s=>s.id===id)?.name}</button>)}</div>}
       </div>)}{!list.length&&<p className="cjm-no-job">{initialStrategy==='contacts'?'Компания продолжает найм самостоятельно. Команда сервиса уже передала контакт.':'В этой главе у участника нет отдельной работы в выбранной стратегии.'}</p>}</div></section>})}
       <div className="cjm-detail-bottom"><button disabled={chapter.id===0} onClick={()=>showChapter(chapter.id-1)}><ArrowLeft size={14}/>Предыдущая глава</button><span>0{chapter.id+1} / 06</span><button disabled={chapter.id===5} onClick={()=>showChapter(chapter.id+1)}>Следующая глава<ArrowRight size={14}/></button></div>
      </div>}
     </div>
     {panelOpen&&<aside className="cjm-inspector" ref={inspectorRef} tabIndex={-1} aria-label={selectedScreen?'Подробности экрана':'Подробности работы'}><div className="cjm-inspector-head"><span>{selectedScreen?'ЭКРАН И ЕГО РОЛЬ':'РАБОТА В КОНТЕКСТЕ'}</span><button onClick={closePanel} aria-label="Закрыть подробности"><PanelRightClose size={17}/></button></div>
      {selectedScreen?<><div className="cjm-inspector-title"><span>{actorName(selectedScreen.actor)} · {selectedScreen.id}</span><h2>{selectedScreen.name}</h2><p>{selectedScreen.description}</p></div>{screenPreview(selectedScreen)}<section><h3>Какие работы поддерживает</h3>{jobLinks(jobs.filter(j=>j.screenIds.includes(selectedScreen.id)).map(j=>j.id),'')}</section></>:selectedJob?<><div className="cjm-inspector-title"><span>{actorName(selectedJob.actor)} · {stages[selectedJob.stage]}</span><h2>{selectedJob.title}</h2><p>{selectedJob.description}</p></div><section><h3>Что должно быть готово</h3><p>{selectedJob.prerequisite}</p>{jobLinks(selectedJob.previousIds,'Начало пути этого участника.')}</section><section><h3>В это время у других</h3>{jobLinks(selectedJob.relatedIds,'Прямые передачи другим участникам для этой работы не выделены.')}</section><section><h3>Результат и следующий шаг</h3><p>{selectedJob.outcome}</p>{jobLinks(selectedJob.nextIds,'Завершение этого участка пути.')}</section>{selectedJob.branches.length>0&&<section><h3>Возможные продолжения</h3><div className="cjm-inspector-branches">{selectedJob.branches.map(b=><div key={b.id}><GitBranch size={14}/><div><strong>{b.label}</strong><p>{b.description}</p>{b.targetStrategy?<a className="cjm-branch-link" href={`${storyUrl(b.targetStrategy)}#chapter=1&job=a1`}>Открыть CJM агентства<ArrowUpRight size={13}/></a>:jobLinks(b.targetIds,'Исход фиксируется в процессе.')}</div></div>)}</div></section>}<section><h3>Где живёт эта работа</h3>{selectedJob.screenIds.length?selectedJob.screenIds.map(id=>{const s=screens.find(s=>s.id===id)!;return <button key={id} className="cjm-screen-card" onClick={()=>showScreen(s)}><img loading="lazy" src={`/hr-cjm/screens/${id.toLowerCase()}.webp`} alt=""/><span><small>{id}</small><strong>{s.name}</strong><span>Миниатюра и функции</span></span><ArrowUpRight size={16}/></button>}):<p className="cjm-muted">В этой стратегии работа происходит вне интерфейса HR Vision.</p>}</section><details className="cjm-basis"><summary>Основание и границы модели</summary><p>{selectedJob.source}</p><p>Предпосылки и результаты сформулированы по смыслу работ. Соседние шаги показывают маршрут, а не обязательную последовательность всего найма.</p></details></>:null}
     </aside>}
    </div>
   </div>
   <footer className="cjm-footer"><span>HR Vision · CJM v3 · 3 самостоятельных маршрута</span><span>Карта целевого процесса. Макеты иллюстрируют функции.</span></footer>
  </main>
 </div>
}
