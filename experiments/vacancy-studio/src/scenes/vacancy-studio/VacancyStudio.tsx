import {useEffect,useRef,useState} from 'react';
import {AnimatePresence,MotionConfig,motion} from 'motion/react';
import {ArrowUp,ArrowUpRight,Check,ChevronDown,ChevronLeft,ChevronRight,ChevronsLeft,ChevronsRight,FileText,Link2,LoaderCircle,MapPin,Mic,Paperclip,Plus,RotateCcw,ShieldCheck,SlidersHorizontal,Sparkles,Square,UserRound,X,Quote,Download,CheckCheck} from 'lucide-react';
import {catalog,parameterById,rubricVersion} from '../vacancy-research/model';
import {ApiError,errorText,getSession,newSession,request,transcribe,uploadFile,type Answer,type Attachment,type Health,type Proposal,type StudioState} from './api';
import {detectMentions} from './checklist';
import {VoiceInput,type VoiceState} from './voice';
import './vacancy-studio.css';
import {TaskSkillCard} from './TaskSkillCard';
import {taskChoices} from './task-options';

const factLabels:Record<string,string>={role:'Должность',location:'Локация',experience:'Опыт',industry:'Отрасль',industry_experience:'Опыт в отрасли',employment:'Занятость',work_mode:'Формат работы',salary:'Вознаграждение'};
const stages:Record<string,string>={queued:'Начинаем разбор',compose:'Собираем профиль и следующий вопрос',validate:'Проверяем формат и основания',repair:'Уточняем ответ модели',extract:'Разбираем ваши задачи',interview:'Готовим следующий вопрос',judge:'Проверяем предложения'};
const examplesForStart=[
  {title:'SDR',caption:'От контакта до встречи',text:'Ищем SDR в B2B-продажи. Нужно находить клиентов, выяснять их задачу и назначать подходящие встречи менеджеру.'},
  {title:'Аккаунт-менеджер',caption:'Развитие клиентов',text:'Нужен аккаунт-менеджер для действующих B2B-клиентов. Важно понимать их задачи, находить возможности развития и согласовывать следующие шаги.'},
  {title:'Руководитель продаж',caption:'Команда и результат',text:'Ищем руководителя отдела продаж. Нужно развивать менеджеров, разбирать воронку и отвечать за план команды.'},
];
const spring={type:'spring' as const,stiffness:360,damping:36};
function title(p:Proposal){return p.kind==='skill'?parameterById(p.parameter_id!).title:factLabels[p.key!]??p.key;}
function Mark(){return <span className="vs-brand"><svg width="27" height="25" viewBox="0 0 27 25" aria-hidden="true"><path d="M1 13C3 5 11 2 24 3C21 8 14 11 5 12L10 17C5 19 1 18 1 13Z" fill="currentColor"/><path d="M26 12C24 20 16 23 3 22C6 17 13 14 22 13L17 8C22 6 26 7 26 12Z" fill="currentColor"/></svg><b>HR Vision</b></span>;}

function ProposalDetail({p,busy,onSave,onClose,onDismiss}:{p:Proposal;busy:boolean;onSave:(values:Record<string,unknown>)=>Promise<unknown>;onClose:()=>void;onDismiss:()=>void}){
  const [value,setValue]=useState(p.value??''),[hard,setHard]=useState(p.hard_filter??false);
  const dirty=value!==p.value||hard!==p.hard_filter;
  return <div className="vs-detail">
    <button className="vs-back" onClick={onClose}><ChevronLeft size={16}/>К профилю</button><h2>{title(p)}</h2>
    <label className="vs-field">Значение<input value={value} maxLength={250} onChange={e=>setValue(e.target.value)}/></label>
    {!['role','industry'].includes(p.key!)&&<label className="vs-toggle"><input type="checkbox" checked={hard} onChange={e=>setHard(e.target.checked)}/><span><b>Обязательное условие</b><small>Несоответствие исключает кандидата из подбора.</small></span></label>}
    <div className="vs-detail-actions"><button className="vs-primary" disabled={busy||!dirty||!value.trim()} onClick={()=>void onSave({value,hard_filter:hard})}>Сохранить<Check size={17}/></button><button className="vs-text-button" disabled={busy} onClick={onDismiss}>Убрать</button></div>
  </div>;
}

export function VacancyStudio(){
  const [state,setState]=useState<StudioState|null>(null),[health,setHealth]=useState<Health|null>(null);
  const [draft,setDraft]=useState(''),[attachments,setAttachments]=useState<Attachment[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[uploading,setUploading]=useState(false);
  const [wide,setWide]=useState(false),[detail,setDetail]=useState<string|null>(null),[inlineDetail,setInlineDetail]=useState<string|null>(null);
  const [palette,setPalette]=useState('lilac'),[settings,setSettings]=useState(false),[linkOpen,setLinkOpen]=useState(false),[link,setLink]=useState(''),[toast,setToast]=useState('');
  const [voiceState,setVoiceState]=useState<VoiceState>('idle'),[seconds,setSeconds]=useState(0),[resetOpen,setResetOpen]=useState(false);
  const input=useRef<HTMLTextAreaElement>(null),linkInput=useRef<HTMLInputElement>(null),file=useRef<HTMLInputElement>(null),chatEnd=useRef<HTMLDivElement>(null),voice=useRef<VoiceInput|null>(null),live=useRef(true),lastFocus=useRef<HTMLElement|null>(null),resetDialog=useRef<HTMLDialogElement>(null);
  const [sending,setSending]=useState<'source'|'sending'|null>(null),[workStarted,setWorkStarted]=useState<number|null>(null),[now,setNow]=useState(Date.now());
  const submission=useRef<{fingerprint:string;id:string}|null>(null);
  const running=state?.pending?.status==='running';const locked=busy||!!running||uploading||voiceState!=='idle';
  const processing=!!sending||!!running;
  const pendingTiming=state?.pending as {started_at?:number;attempt_started_at?:number}|null;
  const pendingStart=running?(pendingTiming?.attempt_started_at??pendingTiming?.started_at):undefined;
  const elapsed=Math.max(0,Math.floor((now-(pendingStart?pendingStart:workStarted??now))/1000));
  useEffect(()=>{if(!processing)return;setNow(Date.now());const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[processing]);
  const started=!!state?.messages.length;
  const acceptedSkills=state?.proposals.filter(p=>p.status==='accepted'&&p.kind==='skill')??[];
  const contextFacts=state?.vacancy_context?Object.entries(state.vacancy_context).flatMap(([key,fact])=>{const proposal=state.proposals.find(p=>p.id===fact.proposal_id);return proposal?[{...proposal,key,value:fact.value,hard_filter:fact.hard_filter}]:[];}):state?.proposals.filter(p=>p.status==='accepted'&&p.kind==='fact')??[];
  const accepted=[...contextFacts,...acceptedSkills];
  const pending=state?.proposals.filter(p=>p.status==='pending')??[];
  const active=state?.proposals.find(p=>p.id===detail);
  const mentions=detectMentions(draft);
  const completed=mentions.filter(m=>m.mentioned).length;

  async function init(){
    setError('');
    try{
      const [s,h]=await Promise.all([getSession(),request<Health>('/healthz')]);
      if(!live.current)return;setState(s);setHealth(h);
      try{const d=JSON.parse(localStorage.getItem('vs-draft-'+s.id)??'{}');setDraft(d.text??'');setAttachments(d.attachments??[]);}catch{/* A malformed local draft never replaces the server profile. */}
    }catch(e){if(live.current)setError((e as Error).message);}
  }
  useEffect(()=>{live.current=true;void init();return()=>{live.current=false;voice.current?.cancel();};},[]);
  useEffect(()=>{if(state)localStorage.setItem('vs-draft-'+state.id,JSON.stringify({text:draft,attachments}));},[draft,attachments,state?.id]);
  useEffect(()=>{if(!state||!running)return;let alive=true;let poll:ReturnType<typeof setTimeout>;
    const tick=async()=>{try{const s=await request<StudioState>('/api/state',state.id);if(alive){setState(s);setError('');}}catch(e){if(alive)setError((e as Error).message);}finally{if(alive)poll=setTimeout(tick,1500);}};
    poll=setTimeout(tick,1000);return()=>{alive=false;clearTimeout(poll);};
  },[state?.id,running]);
  useEffect(()=>{const last=document.querySelector('.vs-message:last-of-type');if(state?.messages.at(-1)?.role==='assistant')last?.scrollIntoView({block:'start',behavior:'instant'});else chatEnd.current?.scrollIntoView({block:'end',behavior:'instant'});},[state?.messages.length]);
  useEffect(()=>{if(resetOpen)resetDialog.current?.showModal();else resetDialog.current?.close();},[resetOpen]);
  useEffect(()=>{if(linkOpen)linkInput.current?.focus();},[linkOpen]);
  useEffect(()=>{if(!toast)return;const t=setTimeout(()=>setToast(''),4200);return()=>clearTimeout(t);},[toast]);
  useEffect(()=>{if(!state)return;const recorder=new VoiceInput({upload:(b,s)=>transcribe(state.id,b,s),onState:(s,n)=>{setVoiceState(s);setSeconds(n);},onResult:t=>{setDraft(d=>d+(d?'\n':'')+t);setToast('Речь распознана. Проверьте текст перед отправкой.');input.current?.focus();},onError:setError});voice.current=recorder;return()=>recorder.cancel();},[state?.id]);
  useEffect(()=>{if(!detail&&!inlineDetail&&!settings&&!resetOpen)return;const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){setDetail(null);setInlineDetail(null);setSettings(false);setResetOpen(false);lastFocus.current?.focus();}};window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key);},[detail,inlineDetail,settings,resetOpen]);

  async function refreshOnConflict(e:unknown){
    setError((e as Error).message);
    if(e instanceof ApiError&&['REVISION_CONFLICT','STALE_QUESTION'].includes(e.code)&&state){setState(await request('/api/state',state.id));}
  }
  async function send(text=draft,answer?:Answer,replyId?:string){
    if(!state||locked||(!text.trim()&&!attachments.length))return;setBusy(true);setError('');setWorkStarted(Date.now());setSending('sending');
    try{
      const materials=answer?[]:[...attachments];
      const urls=[...new Set(text.match(/https:\/\/[^\s<>]+/g)??[])];
      for(const url of urls){if(!materials.some(a=>a.name===url)){if(materials.length>=4)throw new Error('До четырёх материалов в одном ответе.');setSending('source');materials.push(await request<Attachment>('/api/sources/link',state.id,{url}));}}
      const message=text.trim()||'Разберите описание вакансии в прикреплённых материалах.';
      const reply_to=replyId??state.active_question_id??undefined;
      const sourceIds=materials.map(a=>a.id),fingerprint=JSON.stringify([state.id,message,sourceIds,reply_to,answer]);
      if(submission.current?.fingerprint!==fingerprint)submission.current={fingerprint,id:crypto.randomUUID()};
      setSending('sending');
      const s=await request<StudioState>('/api/turn',state.id,{message,message_id:submission.current.id,revision:state.revision,sourceIds,reply_to,answer});
      setState(s);if(!answer){setDraft('');setAttachments([]);setLinkOpen(false);}setInlineDetail(null);
      submission.current=null;
    }catch(e){await refreshOnConflict(e);}finally{setBusy(false);setSending(null);}
  }
  async function retry(){
    if(!state?.pending||locked)return;const msg=state.messages.find(m=>m.id===state.pending!.id);if(!msg)return;
    setBusy(true);setError('');setWorkStarted(Date.now());setSending('sending');
    try{setState(await request('/api/turn',state.id,{message:msg.content,message_id:msg.id,revision:state.revision,sourceIds:msg.sourceIds??[],reply_to:msg.in_reply_to?.id,answer:msg.answer,retry:true}));}
    catch(e){await refreshOnConflict(e);}finally{setBusy(false);setSending(null);}
  }
  async function change(action:string,p?:Proposal,values?:Record<string,unknown>,current=state):Promise<StudioState|null>{
    if(!current||locked)return null;setBusy(true);setError('');
    try{const updated=await request<StudioState>('/api/proposals',current.id,{revision:current.revision,action,id:p?.id,...values});setState(updated);
      if(action==='accept'){setToast(`${title(p!)} — добавлено в профиль`);setInlineDetail(null);}
      if(action==='dismiss'){setDetail(null);setInlineDetail(null);setToast('Убрано из профиля.');}
      if(action==='edit')setToast('Изменения сохранены');
      if(action==='confirm'){setWide(true);setDetail(null);setToast('Профиль подтверждён и сохранён в этом макете');}
      return updated;
    }catch(e){await refreshOnConflict(e);return null;}finally{setBusy(false);}
  }
  async function commitSkill(p:Proposal,values:{level:number;importance:number}){
    const edited=p.level===values.level&&p.importance===values.importance?state:await change('edit',p,values);
    if(!edited)return false;
    // Use the acknowledged revision for acceptance, never the stale render closure.
    return p.status==='pending'?!!await change('accept',p,undefined,edited):true;
  }
  function skillCard(p:Proposal,inProfile=false){return <TaskSkillCard key={p.id} p={p} busy={locked} expanded={inlineDetail===p.id}
    onOpen={()=>{lastFocus.current=document.activeElement as HTMLElement;setInlineDetail(inlineDetail===p.id?null:p.id);if(inProfile){setWide(true);setDetail(null);}}}
    onClose={()=>setInlineDetail(null)} onCommit={values=>commitSkill(p,values)} onDismiss={()=>act('dismiss',p)}/>;}
  function act(action:string,p?:Proposal,values?:Record<string,unknown>){void change(action,p,values).catch(()=>{});}
  async function attach(files:FileList|null){if(!files?.length||!state)return;setUploading(true);setError('');try{
    if(attachments.length+files.length>4)throw new Error('Можно прикрепить до четырёх документов к одному ответу.');
    for(const f of Array.from(files)){const a=await uploadFile(state.id,f);setAttachments(list=>list.some(x=>x.id===a.id)?list:[...list,a]);}
  }catch(e){setError((e as Error).message);}finally{setUploading(false);if(file.current)file.current.value='';}}
  async function attachLink(){if(!state||!link.trim())return;setUploading(true);setError('');try{
    if(attachments.length>=4)throw new Error('Можно прикрепить до четырёх документов.');
    const a=await request<Attachment>('/api/sources/link',state.id,{url:link.trim()});setAttachments(list=>list.some(x=>x.id===a.id)?list:[...list,a]);setLink('');setLinkOpen(false);
  }catch(e){setError((e as Error).message);}finally{setUploading(false);}}
  async function reset(){voice.current?.cancel();setBusy(true);try{const s=await newSession();setState(s);setDraft('');setAttachments([]);setDetail(null);setInlineDetail(null);setWide(false);setError('');setResetOpen(false);}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  function openDetail(p:Proposal){lastFocus.current=document.activeElement as HTMLElement;setDetail(p.id);setWide(true);}
  function exportProfile(){if(!state)return;
    const facts=accepted.filter(p=>p.kind==='fact').map(p=>`- ${title(p)}: ${p.value}${p.hard_filter?' (обязательно)':''}`);
    const skills=accepted.filter(p=>p.kind==='skill').map(p=>`### ${title(p)}\n${taskChoices(p).find(t=>t.level===p.level)?.text??'Задача требует уточнения'}\n\nВажность: ${['','Полезно','Важно','Ключевое'][p.importance!]}`);
    const text=`# ${state.title}\n\n${state.summary}\n\n${facts.join('\n')}\n\n${skills.join('\n\n')}\n\nКаталог: ${rubricVersion}. ${state.confirmed?'Подтверждено работодателем.':'Черновик.'} Составлено в экспериментальном макете; не опубликовано в Верстаке.`;
    const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{type:'text/markdown;charset=utf-8'}));a.download='Профиль вакансии.md';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);
  }
  const processingStatus=processing&&<div role="status" aria-live="polite" className="vs-thinking"><span className="vs-thinking-orb"><LoaderCircle size={20} className="vs-spin"/></span><span><b>{sending==='source'?'Читаем материал по ссылке':sending?'Отправляем описание':stages[state?.pending?.phase??'']??'Обрабатываем ответ'}</b><small>{error?'Нет связи с сервисом. Проверяем состояние повторно.':elapsed>=45?'Обработка продолжается. Ждём ответ модели.':'Можно оставаться на этой странице — результат появится здесь.'}</small></span><time>{Math.floor(elapsed/60)}:{String(elapsed%60).padStart(2,'0')}</time></div>;
  const composer=(initial:boolean)=><div className={`vs-composer ${initial?'vs-composer-large':''}`}>
    {!!attachments.length&&<div className="vs-attachments">{attachments.map(a=><span key={a.id}><FileText size={15}/><span title={a.name}>{a.name}</span><small>{Math.round(a.characters/1000)||'<1'} тыс. знаков</small><button aria-label={`Убрать файл ${a.name}`} onClick={()=>setAttachments(list=>list.filter(x=>x.id!==a.id))}><X size={14}/></button></span>)}</div>}
    <label className="vs-sr-only" htmlFor="vs-message">{initial?'Описание вакансии':'Ваш ответ'}</label>
    <textarea ref={input} id="vs-message" value={draft} maxLength={6000} placeholder={initial?'Например, ищем SDR в Москве. Будет находить B2B-клиентов и договариваться о встречах…':'Ответьте своими словами…'} onChange={e=>setDraft(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&(e.metaKey||e.ctrlKey)){e.preventDefault();void send();}}} disabled={!state}/>
    <div className="vs-composer-tools"><div><button className="vs-icon-button" title="Прикрепить PDF, DOCX или текст" aria-label="Прикрепить файл" disabled={!state||locked} onClick={()=>file.current?.click()}><Paperclip size={20}/></button><button className="vs-link-action" title="Вакансия на hh.ru, SuperJob или другом сайте" aria-label="Добавить ссылку" aria-expanded={linkOpen} aria-controls="vs-url" onClick={()=>setLinkOpen(!linkOpen)} disabled={!state||locked}><Link2 size={20}/><span>Добавить ссылку</span></button>{(uploading||!initial)&&<span className="vs-composer-caption">{uploading?'Читаем документ…':'⌘ Enter — отправить'}</span>}</div><div>
      {voiceState!=='idle'&&<span className="vs-recording-label">{voiceState==='recording'?`${seconds} сек.`:voiceState==='requesting'?'Микрофон…':'Распознаём…'}</span>}
      {voiceState!=='idle'&&<button className="vs-icon-button" onClick={()=>voice.current?.cancel()} aria-label="Отменить запись"><X size={17}/></button>}
      <button className={`vs-icon-button ${voiceState==='recording'?'vs-is-recording':''}`} title={health?.voice?'Надиктовать ответ':'Распознавание голоса недоступно'} aria-label={voiceState==='recording'?'Закончить запись':'Надиктовать ответ'} disabled={!state||!health?.voice||busy||!!running||uploading||voiceState==='transcribing'||voiceState==='requesting'} onClick={()=>voiceState==='recording'?voice.current?.stop():void voice.current?.start()}>{voiceState==='recording'?<Square size={17}/>:voiceState==='transcribing'?<LoaderCircle className="vs-spin" size={20}/>:<Mic size={20}/>}</button>
      <button className="vs-send" aria-label={initial?'Начать создавать профиль':'Отправить ответ'} disabled={!state||locked||(!draft.trim()&&!attachments.length)} onClick={()=>void send()}>{busy?<LoaderCircle className="vs-spin" size={20}/>:<ArrowUp size={22}/>}</button></div></div>
    <AnimatePresence>{linkOpen&&<motion.div className="vs-link-input" initial={{height:0,opacity:0}} animate={{height:'auto',opacity:1}} exit={{height:0,opacity:0}}><label htmlFor="vs-url" className="vs-sr-only">Ссылка на вакансию</label><input ref={linkInput} id="vs-url" type="url" placeholder="https://hh.ru/vacancy/…" value={link} onChange={e=>setLink(e.target.value)} onKeyDown={e=>{if(e.key==='Enter')void attachLink();}}/><button disabled={!link.trim()||locked} onClick={()=>void attachLink()}>{uploading?'Читаем…':'Прочитать'}</button></motion.div>}</AnimatePresence>
    {initial&&<div className="vs-mention-area"><div className="vs-mention-label"><span>Хорошее начало — пять деталей</span><span>{completed} / 5</span></div><div className="vs-mentions">{mentions.map(m=><span key={m.id} className={m.mentioned?'is-mentioned':''} aria-label={`${m.label}: ${m.mentioned?'упомянуто':'пока не упомянуто'}`}><span className="vs-mention-check">{m.mentioned?<Check size={12}/>:<i/>}</span>{m.label}</span>)}</div><div className="vs-mention-progress"><motion.div animate={{width:`${completed/5*100}%`}} transition={spring}/></div></div>}
  </div>;

  return <MotionConfig reducedMotion="user"><div className={`vs-app vs-palette-${palette}`}>
    <header className="vs-header"><a href="#" aria-label="HR Vision — новая вакансия" onClick={e=>{e.preventDefault();if(started)setResetOpen(true);}}><Mark/></a><div className="vs-breadcrumb"><span>Новая вакансия</span>{started&&<><ChevronRight size={14}/><b>{state?.title}</b></>}</div><div className="vs-header-actions"><span className="vs-connection"><i className={state?'is-online':''}/>{state?(state.confirmed?'Профиль подтверждён':'Черновик сохраняется'):'Подключаемся'}</span><button className="vs-icon-button" aria-label="Настройки макета" title="Оформление макета" aria-expanded={settings} onClick={()=>setSettings(!settings)}><SlidersHorizontal size={18}/></button></div>
      {settings&&<div className="vs-settings"><span className="vs-eyebrow">Оформление макета</span>{[{id:'meaning',name:'Цвета по смыслу'},{id:'lilac',name:'Лавандовые карточки'},{id:'quiet',name:'Нейтральные карточки'}].map(c=><button key={c.id} aria-pressed={palette===c.id} onClick={()=>setPalette(c.id)}><span className={`vs-swatch vs-swatch-${c.id}`}/>{c.name}{palette===c.id&&<Check size={16}/>}</button>)}<p>Живой эксперимент · {health?.model??'модель подключается'}<br/>Каталог ядра: {catalog.length} навыков. Черновик хранится отдельно от рабочих вакансий.</p></div>}
    </header>
    <input ref={file} type="file" accept=".pdf,.docx,.txt,.md,.csv" multiple hidden onChange={e=>void attach(e.target.files)}/>
    {error&&<div role="alert" className="vs-error"><span>{error}</span>{!state?<button onClick={()=>void init()}>Подключиться</button>:<button aria-label="Скрыть ошибку" onClick={()=>setError('')}><X size={17}/></button>}</div>}
    {!started?<main className="vs-intro"><span className="vs-eyebrow">Начнём с вашей задачи</span><h1>Кого вы ищете<br/>в команду продаж?</h1><p className="vs-intro-description">Расскажите о работе. Вместе соберём понятный профиль человека.</p>{composer(true)}{processingStatus}<div className="vs-starters">{examplesForStart.map(e=><button key={e.title} disabled={locked} onClick={()=>{setDraft(e.text);input.current?.focus();}}><span><b>{e.title}</b><small>{e.caption}</small></span><ArrowUpRight size={17}/></button>)}</div><p className="vs-intro-foot">Система предложит требования. Вы решите, что добавить в профиль.</p></main>:<main className={`vs-workspace ${wide?'vs-profile-wide':''}`}>
      <aside className="vs-profile" aria-label="Профиль будущего сотрудника"><div className="vs-profile-toolbar"><span><UserRound size={17}/>Портрет сотрудника</span><button className="vs-icon-button" aria-label={wide?'Свернуть профиль':'Развернуть профиль'} aria-expanded={wide} onClick={()=>{setWide(!wide);setDetail(null);}}>{wide?<ChevronsLeft size={20}/>:<ChevronsRight size={20}/>}</button></div>
        <div className="vs-profile-scroll">{active?<ProposalDetail key={active.id} p={active} busy={locked} onSave={v=>change('edit',active,v)} onClose={()=>{setDetail(null);lastFocus.current?.focus();}} onDismiss={()=>act('dismiss',active)}/>:<>
          <section className="vs-vacancy-passport" aria-label="Описание вакансии">
            <h2 className="vs-vacancy-title">{contextFacts.find(p=>p.key==='role')?<button disabled={locked} aria-label="Редактировать должность" onClick={()=>openDetail(contextFacts.find(p=>p.key==='role')!)}>{contextFacts.find(p=>p.key==='role')!.value}<ChevronRight size={17}/></button>:state?.title}</h2>
            <div className="vs-passport-body">{state?.summary&&<p className="vs-vacancy-summary">{state.summary}</p>}
              {!!contextFacts.some(p=>p.key!=='role')&&<ul className="vs-vacancy-facts">{contextFacts.filter(p=>p.key!=='role').map(p=><li key={p.id}><button disabled={locked} onClick={()=>openDetail(p)} aria-label={`Изменить ${title(p)}`}><span className="vs-passport-label">{title(p)}</span><span className="vs-passport-value">{p.value}{p.hard_filter&&<small>Обязательно</small>}</span><ChevronRight size={14}/></button></li>)}</ul>}
            </div>
          </section>
          <section className="vs-profile-section" aria-label="Навыки"><div className="vs-profile-cards"><AnimatePresence>{accepted.filter(p=>p.kind==='skill').map(p=>skillCard(p,true))}</AnimatePresence></div>{!accepted.some(p=>p.kind==='skill')&&<div className="vs-empty-skills"><span><Plus size={20}/></span><p>Добавляйте подходящие<br/>предложения из разговора</p></div>}</section>
          {!!pending.length&&<button className="vs-pending-link" onClick={()=>{setWide(false);chatEnd.current?.scrollIntoView({block:'end'});}}>В разговоре ещё {pending.length} предложений<ChevronRight size={15}/></button>}
        </>}</div>
        <div className="vs-profile-footer"><button className={state?.confirmed?'vs-confirmed':'vs-primary'} disabled={locked||!accepted.length} onClick={()=>state?.confirmed?exportProfile():act('confirm')}>{state?.confirmed?<><Download size={16}/>Скачать профиль</>:<>Подтвердить профиль<Check size={17}/></>}</button>{state?.confirmed&&<small>Сохранён в макете. В Верстак не отправлен.</small>}</div>
      </aside>
      <section className="vs-conversation" aria-label="Разговор о вакансии"><div className="vs-chat-heading"><span><span className="vs-assistant-icon"><Sparkles size={17}/></span><b>Собираем профиль вместе</b></span><button className="vs-mobile-profile" onClick={()=>setWide(true)}>Профиль · {acceptedSkills.length}<ChevronRight size={15}/></button><button className="vs-icon-button" title="Новая вакансия" aria-label="Новая вакансия" disabled={locked} onClick={()=>setResetOpen(true)}><Plus size={19}/></button></div>
        <div className="vs-chat-scroll">{state?.messages.map((m,index)=><div key={m.id} className={`vs-message vs-message-${m.role}`}>
          {m.role==='user'?<><p>{m.content}</p>{!!m.sourceIds?.length&&<small><Paperclip size={13}/>{m.sourceIds.length} материалов</small>}</>:<>
            <p>{m.content}</p>
            {!!m.proposalIds?.some(id=>state.proposals.some(p=>p.id===id&&p.kind==='skill'&&p.status==='pending'))&&<div className="vs-suggestions"><AnimatePresence initial={false}>{m.proposalIds.map(id=>{const p=state.proposals.find(p=>p.id===id&&p.kind==='skill'&&p.status==='pending');return p?skillCard(p):null;})}</AnimatePresence></div>}
            {m.question&&(index!==state.messages.length-1||!m.question_target||state.active_question_id===m.id)&&<div className="vs-question"><span className="vs-eyebrow">{m.question_target?.label??'Следующий вопрос'}</span><h3>{m.question}</h3>{index===state.messages.length-1&&<><div className="vs-options">{m.options?.map((o,i)=><button key={o} disabled={locked} onClick={()=>void send(o,m.question_target?{option_index:i}:undefined,m.id)}><span>{i+1}</span>{o}<ArrowUpRight size={16}/></button>)}</div>{m.question_target&&<button className="vs-text-button" disabled={locked} onClick={()=>void send('Пока не могу уточнить это требование.',{defer:true},m.id)}>Пока не знаю — пропустить</button>}</>}</div>}
          </>}
        </div>)}
        {processingStatus}
        {state?.pending?.status==='failed'&&<div role="alert" className="vs-turn-error"><p>{errorText(state.pending.error??'')}</p><button disabled={locked} onClick={()=>void retry()}><RotateCcw size={15}/>Повторить обработку</button></div>}
        <div ref={chatEnd}/></div>
        <div className="vs-chat-composer">{composer(false)}</div>
      </section>
    </main>}
    <AnimatePresence>{toast&&<motion.div role="status" className="vs-toast" initial={{opacity:0,y:12}} animate={{opacity:1,y:0}} exit={{opacity:0,y:8}}><Check size={16}/>{toast}</motion.div>}</AnimatePresence>
    <dialog ref={resetDialog} aria-labelledby="vs-reset-title" className="vs-reset-dialog" onCancel={()=>setResetOpen(false)}><h2 id="vs-reset-title">Новая вакансия?</h2><p>Для новой вакансии откроется отдельный разговор. Текущий профиль можно скачать перед переходом.</p><div><button onClick={()=>setResetOpen(false)} autoFocus>Остаться</button><button className="vs-primary" disabled={busy} onClick={()=>void reset()}>Начать новую<Plus size={16}/></button></div></dialog>
  </div></MotionConfig>;
}
