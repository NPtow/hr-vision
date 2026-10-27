import {useEffect,useId,useRef,useState,type ReactNode} from 'react';
import {AnimatePresence,LayoutGroup,MotionConfig,motion,useReducedMotion} from 'motion/react';
import {ArrowLeft,ArrowRight,ArrowUpRight,Bookmark,Check,CheckCheck,ChevronRight,ClipboardList,FileText,Layers3,MapPin,MessageCircle,Pause,Play,Plus,Quote,RotateCcw,SlidersHorizontal,Target,Trash2,UserRound,X} from 'lucide-react';
import {editLevel,examples,parameterById,requirementFor,rubricVersion,shortTitles,topics,type Requirement} from './model';
import {portraitDirections,portraitReferences,type PortraitDirection,type PortraitReference} from './portrait-references';
import './profile-research.css';

type View='references'|'try';
type Props={initialView?:View;playing?:boolean;runId?:number;compact?:boolean;onManualInteraction?:()=>void};
type History={index:number;text:string};
const skillTopics=topics.slice(1,4);
const importance=['Не учитываем','Небольшая','Высокая','Ключевая'];
const scenes=['На первом контакте','Когда клиент сомневается','В конце разговора'];
const spring={type:'spring' as const,stiffness:300,damping:34};
const defaultRequirement=(index:number)=>requirementFor(skillTopics[index],skillTopics[index].answers[0])!;
const defaultHistory=()=>skillTopics.map((t,index)=>({index,text:t.answers[0].text}));
const behaviors:Record<string,string[]>={
 A03:['Передаёт клиента, даже когда тот не подходит','Собирает отдельные сведения о клиенте','Выясняет задачу, условия покупки и обосновывает передачу','Проверяет противоречия и пересматривает квалификацию','Сопоставляет интересы участников и проверяет альтернативы'],
 'A06.1':['Спорит с клиентом и игнорирует сомнение','Отвечает по сценарию, сложное передаёт старшему','Выясняет причину сомнения и отвечает по существу','Разделяет несколько причин и проверяет главную','Согласует проверку спорного условия и её результат'],
 A07:['Выдаёт неподтверждённый шаг за договорённость','Предлагает продолжить без конкретной договорённости','Согласует цель, время и участников встречи','Меняет формат шага, если обнаружено препятствие','Согласует последовательность шагов и условия пересмотра']
};

function Dialog({title,children,onClose}:{title:string;children:ReactNode;onClose:()=>void}){
 const ref=useRef<HTMLDialogElement>(null);const titleId=useId();
 useEffect(()=>{const d=ref.current;d?.showModal();return()=>d?.close()},[]);
 return <dialog className="vp-dialog" ref={ref} aria-labelledby={titleId} onCancel={onClose} onClick={e=>{if(e.target===e.currentTarget){const b=e.currentTarget.getBoundingClientRect();if(e.clientX<b.left||e.clientX>b.right||e.clientY<b.top||e.clientY>b.bottom)onClose()}}}>
  <header><h2 id={titleId}>{title}</h2><button aria-label="Закрыть референс" className="vp-icon" onClick={onClose}><X size={20}/></button></header>{children}
 </dialog>
}
function SourceImage({item}:{item:PortraitReference}){
 const [failed,setFailed]=useState(false);
 return failed?<div className="vp-image-failed"><FileText/><p>Изображение не загрузилось.</p><a href={item.source} target="_blank" rel="noreferrer">Посмотреть у автора <ArrowUpRight size={15}/></a></div>:<img src={item.image} alt={item.brand+' — '+item.imageKind} loading="lazy" onError={()=>setFailed(true)}/>;
}
function ReferenceGallery({onTry}:{onTry:(d:PortraitDirection)=>void}){
 const [selected,setSelected]=useState<PortraitReference|null>(null);const [filter,setFilter]=useState('Все');const [video,setVideo]=useState(false);
 const [favorites,setFavorites]=useState<string[]>(()=>{try{const value=JSON.parse(localStorage.getItem('hr-vacancy-portrait-favorites')??'[]');return Array.isArray(value)?value.filter((id:unknown)=>typeof id==='string'&&portraitReferences.some(r=>r.id===id)):[]}catch{return []}});
 function favorite(id:string){setFavorites(prev=>{const next=prev.includes(id)?prev.filter(v=>v!==id):[...prev,id];try{localStorage.setItem('hr-vacancy-portrait-favorites',JSON.stringify(next))}catch{}return next})}
 const shown=portraitReferences.filter(r=>filter==='Все'||(filter==='Отобранные'?favorites.includes(r.id):r.family===filter));
 return <section className="vp-references">
  <div className="vp-section-title"><div><span className="vp-eyebrow">8 визуальных референсов · 3 формы для HR Vision</span><h2>Как выглядит человек,<br/>которого мы собираемся искать?</h2></div><p>В этот раз смотрим на сам профиль. Открой изображение, посмотри устройство блоков и попробуй нашу интерпретацию.</p></div>
  <div className="vp-reference-filters" aria-label="Виды профиля">{['Все','Портрет из блоков','Карточка с подробностями','Профиль навыков','Предложение → добавить','Отобранные'].map(f=><button key={f} aria-pressed={f===filter} onClick={()=>setFilter(f)}>{f}{f==='Отобранные'&&favorites.length>0&&<span>{favorites.length}</span>}</button>)}</div>
  <div className="vp-reference-grid">{shown.map((item)=><article key={item.id} className="vp-reference-card">
   <button className="vp-reference-open" aria-label={'Посмотреть профиль '+item.brand} onClick={()=>{setSelected(item);setVideo(false)}}><div className="vp-reference-image"><SourceImage item={item}/><span>{item.imageKind}</span></div><div className="vp-reference-copy"><div><b>{item.brand}</b><span>{item.family}</span></div><h3>{item.title}</h3><p>{item.take}</p></div></button>
   <footer><a href={item.source} target="_blank" rel="noreferrer">Источник <ArrowUpRight size={14}/></a><button onClick={()=>favorite(item.id)} aria-pressed={favorites.includes(item.id)} aria-label={'Отобрать '+item.brand}><Bookmark size={15} fill={favorites.includes(item.id)?'currentColor':'none'}/>{favorites.includes(item.id)?'Отобрано':'Отобрать'}</button><button onClick={()=>onTry(item.test)}>Наша версия <ArrowRight size={15}/></button></footer>
  </article>)}</div>
  {shown.length===0&&<div className="vp-empty-gallery">В этой подборке пока нет референсов.<button onClick={()=>setFilter('Все')}>Посмотреть все</button></div>}
  <div className="vp-research-conclusion"><span>Что сравниваем</span><h3>Лист о человеке. Карточку с подробностями. Портрет через действия.</h3><p>Во всех трёх вариантах одинаковые навыки и уровни. Меняется способ прочитать профиль. Можно наполнить один вариант и переключиться на другой — данные останутся.</p><button className="vp-primary" onClick={()=>onTry('dossier')}>Собрать портрет <ArrowRight size={16}/></button></div>
  <p className="vp-footnote">Изображения принадлежат авторам продуктов. Источники проверены 26.10.2026; иллюстрации и небольшие превью помечены. Изучены публичные страницы и изображения, без входа в чужие рабочие аккаунты. Наши варианты ниже — самостоятельные макеты, не экраны этих сервисов.</p>
  {selected&&<Dialog title={selected.brand} onClose={()=>{setSelected(null);setVideo(false)}}><div className="vp-reference-detail"><div className="vp-large-media">{video&&selected.video?(selected.video.kind==='mp4'?<video src={selected.video.url} controls autoPlay playsInline/>:<iframe title={selected.video.label} src={'https://www.youtube-nocookie.com/embed/'+selected.video.id} allow="encrypted-media; picture-in-picture" allowFullScreen/>):<SourceImage item={selected}/>}</div><aside><span className="vp-eyebrow">{selected.family}</span><h3>{selected.title}</h3><p>{selected.observation}</p><h4>Как перенести в наш портрет</h4><p>{selected.take}</p><h4>Граница заимствования</h4><p>{selected.boundary}</p><a href={selected.source} target="_blank" rel="noreferrer">Открыть первоисточник <ArrowUpRight size={15}/></a>{selected.video&&<><button className="vp-text-button" onClick={()=>setVideo(!video)}>{video?<FileText size={15}/>:<Play size={15}/>} {video?'Вернуться к изображению':selected.video.label}</button><a href={selected.video.url} target="_blank" rel="noreferrer">Видео отдельно <ArrowUpRight size={15}/></a></>}<button className="vp-primary" onClick={()=>onTry(selected.test)}>Попробовать нашу форму <ArrowRight size={16}/></button><small>{selected.imageKind}</small></aside></div></Dialog>}
 </section>
}

function LevelDots({value}:{value:number}){return <span className="vp-level-dots" aria-label={'Требуемый уровень '+value+' из 5'}>{[1,2,3,4,5].map(n=><i key={n} className={n<=value?'is-on':''}/>)}<b>{value}/5</b></span>}
function RoleMark(){return <div className="vp-role-mark" aria-hidden="true"><UserRound size={30} strokeWidth={1.25}/><span>SDR</span></div>}
function Formalities(){return <div className="vp-formalities"><span className="vp-small-label">Обязательные условия</span><p><MapPin size={14}/> Москва · гибрид</p><p><ClipboardList size={14}/> Полная занятость</p><small>Опыт в нашей отрасли необязателен.</small></div>}
function EmptyProfile({direction}:{direction:PortraitDirection}){return <div className={'vp-empty-profile is-'+direction}><span><Plus size={24}/></span><h3>{direction==='situations'?'Пока знаем только задачу':direction==='cards'?'Здесь появятся черты рабочего портрета':'Навыки ещё не добавлены'}</h3><p>Справа есть предложение. Проверьте уровень и добавьте — оно станет частью этого профиля.</p><span className="vp-empty-arrow"><ArrowRight size={22}/></span></div>}
function AcceptedSkill({r,direction,onOpen,index}:{r:Requirement;direction:PortraitDirection;onOpen:()=>void;index:number}){
 const p=parameterById(r.id);const level=p.levels[r.level-1];const text=behaviors[r.id][r.level-1];
 return <motion.button layout data-requirement={r.id} className={'vp-accepted-skill is-'+direction} onClick={onOpen} aria-label={'Открыть навык '+p.title} initial={{opacity:0,y:8}} animate={{opacity:1,y:0}} transition={{duration:.2}}>
  {direction==='dossier'?<><div className="vp-skill-icon">{r.id==='A03'?<Target size={20}/>:r.id==='A06.1'?<MessageCircle size={20}/>:<ArrowUpRight size={20}/>}</div><div className="vp-skill-copy"><h3>{shortTitles[r.id]}</h3><p>{level.label}</p><span>{importance[r.importance]} важность{r.manual?' · изменено вами':''}</span></div><LevelDots value={r.level}/><ChevronRight size={17}/></>:
   direction==='cards'?<><div className="vp-card-kicker"><span>{shortTitles[r.id]}</span><Check size={15}/></div><h3>{text}</h3><p>{level.label}</p><footer><LevelDots value={r.level}/><span>{importance[r.importance]}</span><ChevronRight size={15}/></footer></>:
   <><div className="vp-situation-order">0{index+1}</div><div className="vp-situation-copy"><span className="vp-small-label">{scenes[skillTopics.findIndex(t=>t.parameter===r.id)]}</span><h3>{text}</h3><blockquote>{examples[r.id][r.level-1]}</blockquote><small>Пример речи условного кандидата</small><footer><span>{shortTitles[r.id]}</span><span>Уровень {r.level}/5</span><span>{importance[r.importance]} важность</span><ChevronRight size={15}/></footer></div></>}
 </motion.button>
}
function Portrait({direction,requirements,onOpen}:{direction:PortraitDirection;requirements:Requirement[];onOpen:(id:string)=>void}){
 return <div className={'vp-portrait is-'+direction} data-profile-direction={direction}>
  <header className="vp-role-heading"><RoleMark/><div><span className="vp-small-label">Портрет будущего сотрудника</span><h2>{direction==='situations'?'Человек, который начинает продажи':'SDR в B2B-продажи'}</h2><p>От первого контакта до встречи с менеджером</p></div><span className="vp-draft">Черновик</span></header>
  <div className="vp-portrait-body"><aside className="vp-role-context"><span className="vp-small-label">Зачем он команде</span><p>Находит потенциальных клиентов и передаёт встречи с теми, кому действительно подходит продукт.</p><div className="vp-role-result"><Target size={16}/><span>Результат работы<strong>Квалифицированные встречи</strong></span></div><Formalities/></aside>
   <section className="vp-skills-section"><div className="vp-skills-heading"><div><span className="vp-small-label">{direction==='situations'?'Как он работает':'Принятые навыки'}</span><h3>{direction==='cards'?'Из чего складывается портрет':direction==='situations'?'Три рабочих ситуации':'Что он должен уметь'}</h3></div><span className="vp-skills-count">{requirements.length}</span></div>
    {requirements.length===0?<EmptyProfile direction={direction}/>:<div className="vp-accepted-list"><AnimatePresence initial={false}>{requirements.map((r,index)=><AcceptedSkill key={r.id} r={r} direction={direction} index={index} onOpen={()=>onOpen(r.id)}/>)}</AnimatePresence></div>}
    {requirements.length>0&&requirements.length<3&&<div className="vp-more-pending"><Plus size={15}/> Следующее принятое предложение появится здесь</div>}
   </section>
  </div>
 </div>
}

function RequirementControls({r,onChange,idPrefix}:{r:Requirement;onChange:(r:Requirement)=>void;idPrefix:string}){
 const p=parameterById(r.id);
 return <div className="vp-requirement-controls"><label htmlFor={idPrefix+'-level'}>Требуемый уровень<select id={idPrefix+'-level'} value={r.level} onChange={e=>onChange(editLevel(r,Number(e.target.value)))}>{p.levels.map((l,i)=><option key={l.level_id} value={i+1}>{i+1}. {l.label}</option>)}</select></label><label htmlFor={idPrefix+'-importance'}>Важность<select id={idPrefix+'-importance'} value={r.importance} onChange={e=>onChange({...r,importance:Number(e.target.value)})}>{importance.map((v,i)=><option key={i} value={i}>{v}</option>)}</select></label></div>
}
function DetailEditor({r,onChange,onClose,onRemove}:{r:Requirement;onChange:(r:Requirement)=>void;onClose:()=>void;onRemove:()=>void}){
 const p=parameterById(r.id);return <motion.div className="vp-editor" initial={{opacity:0}} animate={{opacity:1}}>
  <button className="vp-text-button" onClick={onClose}><ArrowLeft size={15}/> К портрету</button><div className="vp-editor-heading"><span className="vp-small-label">Навык {r.id} · уже в профиле</span><h2>{p.title}</h2><p>Выберите, какое поведение нужно в вашей работе.</p></div>
  <RequirementControls r={r} onChange={onChange} idPrefix="accepted"/>
  <div className="vp-anchor-description"><LevelDots value={r.level}/><h3>{p.levels[r.level-1].label}</h3><p>{p.levels[r.level-1].description}</p></div>
  <div className="vp-speech"><div><span>М</span><p><b>Маша, условный кандидат</b><small>Сочинённый пример речи этого уровня</small></p></div><AnimatePresence mode="wait"><motion.blockquote key={r.level} initial={{opacity:0,y:4}} animate={{opacity:1,y:0}} exit={{opacity:0}} transition={{duration:.14}}>{examples[r.id][r.level-1]}</motion.blockquote></AnimatePresence></div>
  <div className="vp-employer-source"><span><Quote size={14}/> Из ответа работодателя</span><blockquote>«{r.quote}»</blockquote><details><summary>Почему предложен этот навык</summary><p>{r.reason}</p></details></div>
  <footer><button className="vp-text-button" onClick={onRemove}><Trash2 size={14}/> Убрать из профиля</button>{r.manual&&<button className="vp-text-button" onClick={()=>onChange({...r,level:r.proposed,manual:false})}><RotateCcw size={14}/> Вернуть уровень {r.proposed}</button>}<button className="vp-primary" onClick={onClose}>Готово <Check size={15}/></button></footer>
 </motion.div>
}

function Workspace({direction,playing,onManual,onFillRef}:{direction:PortraitDirection;playing:boolean;onManual:()=>void;onFillRef:(fn:()=>void)=>void}){
 const [requirements,setRequirements]=useState<Requirement[]>([]);const [index,setIndex]=useState(0);
 const [history,setHistory]=useState<History[]>([{index:0,text:skillTopics[0].answers[0].text}]);
 const [pending,setPending]=useState<Requirement|null>(defaultRequirement(0));const [selected,setSelected]=useState<string|null>(null);
 const [mode,setMode]=useState<'question'|'chat'>('question');const [notice,setNotice]=useState('');const [skipped,setSkipped]=useState<string[]>([]);
 const id=useId();const reduced=useReducedMotion();const selectedRequirement=requirements.find(r=>r.id===selected);
 const topic=skillTopics[index];const done=index>=skillTopics.length;
 function answer(answerIndex:number){const answer=topic.answers[answerIndex];setHistory(prev=>[...prev.filter(h=>h.index!==index),{index,text:answer.text}]);setPending(requirementFor(topic,answer));setNotice('Предложение готово. В профиль пока не добавлено.')}
 function nextUnresolved(processedId:string){const resolved=new Set([...requirements.map(r=>r.id),...skipped,processedId]);const next=skillTopics.findIndex(t=>!resolved.has(t.parameter!));setIndex(next<0?skillTopics.length:next)}
 function add(){if(!pending)return;setRequirements(prev=>[...prev.filter(r=>r.id!==pending.id),pending]);setSkipped(prev=>prev.filter(v=>v!==pending.id));setNotice(shortTitles[pending.id]+': добавлено в профиль');nextUnresolved(pending.id);setPending(null)}
 function fill(){setRequirements(skillTopics.map((_,i)=>defaultRequirement(i)));setHistory(defaultHistory());setIndex(3);setPending(null);setSelected(null);setSkipped([]);setNotice('Показан заполненный пример с тремя навыками')}
 useEffect(()=>{onFillRef(fill)},[onFillRef]);
 useEffect(()=>{if(!playing||done)return;const t=setTimeout(()=>{if(pending)add();else answer(0)},2300);return()=>clearTimeout(t)},[playing,index,pending,done]);
 function manual(fn:()=>void){onManual();fn()}
 function remove(id:string){setRequirements(prev=>prev.filter(r=>r.id!==id));setSelected(null);setSkipped(prev=>[...new Set([...prev,id])]);setNotice(shortTitles[id]+': убрано из профиля')}
 function reopen(id:string){const i=skillTopics.findIndex(t=>t.parameter===id);setIndex(i);const previous=history.find(h=>h.index===i);const original=skillTopics[i].answers.find(a=>a.text===previous?.text)??skillTopics[i].answers[0];setPending(requirementFor(skillTopics[i],original));setSelected(null);setNotice('Предложение возвращено для проверки')}
 return <LayoutGroup id={id}><div className={'vp-workspace '+(selectedRequirement?'has-detail':'')}>
  <div className="vp-product-top"><span className="vp-product-brand">HR Vision</span><span>Новая вакансия <ChevronRight size={14}/> Портрет сотрудника</span><span className="vp-live-status"><i/> Составляем вместе</span></div>
  <div className="vp-product-progress"><div><span>Принято предложений</span><strong data-accepted-count={requirements.length}>{requirements.length}<span> / 3</span></strong></div><div className="vp-progress-track" role="progressbar" aria-label="Принятые предложения" aria-valuemin={0} aria-valuemax={3} aria-valuenow={requirements.length}><motion.span initial={false} animate={{width:(requirements.length/3*100)+'%'}} transition={{duration:reduced?0:.35}}/></div><span>{requirements.length===3?'Навыки этого примера собраны':'Добавляйте то, что нужно для вашей роли'}</span></div>
  <motion.div layout className="vp-workspace-columns" transition={spring}>
   <motion.section layout className="vp-profile-pane" transition={spring} aria-label="Собираемый профиль">
    {selectedRequirement?<DetailEditor r={selectedRequirement} onChange={r=>manual(()=>setRequirements(prev=>prev.map(v=>v.id===r.id?r:v)))} onClose={()=>manual(()=>setSelected(null))} onRemove={()=>manual(()=>remove(selectedRequirement.id))}/>:<Portrait direction={direction} requirements={requirements} onOpen={id=>manual(()=>setSelected(id))}/>}
   </motion.section>
   <motion.aside layout className="vp-conversation-pane" transition={spring} aria-label="Вопросы и предложения"><header><div><span className="vp-assistant-mark"><MessageCircle size={17}/></span><strong>Уточняем портрет</strong></div><div className="vp-mode-picker" aria-label="Форма разговора"><button aria-pressed={mode==='question'} onClick={()=>manual(()=>setMode('question'))}>Один вопрос</button><button aria-pressed={mode==='chat'} onClick={()=>manual(()=>setMode('chat'))}>Разговор</button></div></header>
    <div className="vp-conversation-content">
     {mode==='chat'&&history.length>0&&<div className="vp-chat-history">{history.map(h=><div key={h.index}><p className="vp-chat-assistant">{skillTopics[h.index].question}</p><p className="vp-chat-employer">{h.text}</p></div>)}</div>}
     {pending?<motion.div key={'suggestion-'+pending.id} className="vp-suggestion" initial={{opacity:0,y:4}} animate={{opacity:1,y:0}}><div className="vp-suggestion-status"><Plus size={14}/> Предлагаем добавить</div><h3>{parameterById(pending.id).title}</h3><p className="vp-suggestion-intro">{behaviors[pending.id][pending.level-1]}</p><RequirementControls r={pending} onChange={r=>manual(()=>setPending(r))} idPrefix="suggested"/><div className="vp-suggestion-anchor"><span>{parameterById(pending.id).levels[pending.level-1].label}</span><p>{parameterById(pending.id).levels[pending.level-1].description}</p></div><details className="vp-proposal-source"><summary><Quote size={13}/> Из вашего ответа</summary><blockquote>«{pending.quote}»</blockquote></details><button className="vp-primary vp-add-button" onClick={()=>manual(add)}><Plus size={16}/> Добавить в профиль</button><div className="vp-proposal-actions"><button onClick={()=>manual(()=>{setPending(null);setHistory(h=>h.filter(v=>v.index!==index))})}>Ответить иначе</button><button onClick={()=>manual(()=>{setSkipped(v=>[...new Set([...v,pending.id])]);nextUnresolved(pending.id);setPending(null);setNotice('Предложение пропущено и не добавлено в профиль')})}>Не добавлять</button></div></motion.div>:
      !done?<div className="vp-question"><span className="vp-small-label">Вопрос {index+1} из 3 · {topic.title}</span><h3>{topic.question}</h3><div className="vp-answer-options">{topic.answers.map((a,i)=><button key={i} onClick={()=>manual(()=>answer(i))}>{a.text}<ArrowRight size={15}/></button>)}</div></div>:
      <div className="vp-discussion-complete"><span><CheckCheck size={28}/></span><h3>{requirements.length===3?'Вот такой человек нам нужен':'Предложения разобраны'}</h3><p>{requirements.length===3?'Проверьте портрет. Любой навык можно открыть и уточнить.':'В профиле только принятые навыки. Пропущенные можно вернуть к обсуждению.'}</p>{skipped.map(id=><button key={id} className="vp-text-button" onClick={()=>manual(()=>reopen(id))}><Plus size={14}/> Обсудить: {shortTitles[id]}</button>)}</div>}
    </div>
    <footer className="vp-conversation-footer"><span><Quote size={13}/> Сохраняем основание каждого предложения</span><small>Сначала предлагаем. Добавляете вы.</small></footer>
   </motion.aside>
  </motion.div>
  <div className="vp-update-notice" aria-live="polite"><Check size={14}/>{notice||'Портрет роли задан. Первый навык предложен после ответа про квалификацию.'}</div>
 </div></LayoutGroup>
}

function ResearchBody({initialView='references',playing=false,compact=false,onManualInteraction}:Omit<Props,'runId'>){
 const [view,setView]=useState<View>(initialView);const [direction,setDirection]=useState<PortraitDirection>('dossier');
 const [reset,setReset]=useState(0);const [localPlaying,setLocalPlaying]=useState(false);const [manualOverride,setManualOverride]=useState(false);
 const fillRef=useRef<()=>void>(()=>{});const topRef=useRef<HTMLDivElement>(null);const selected=portraitDirections.find(d=>d.id===direction)!;
 const onFillRef=useRef((fn:()=>void)=>{fillRef.current=fn}).current;
 useEffect(()=>{topRef.current?.scrollIntoView({behavior:'instant'})},[reset]);
 function manual(){setLocalPlaying(false);setManualOverride(true);onManualInteraction?.()}
 function show(v:View){manual();setView(v);topRef.current?.scrollIntoView({behavior:'instant'})}
 function tryDirection(d:PortraitDirection){setDirection(d);show('try')}
 return <div className={'vp-lab '+(compact?'is-compact':'')} ref={topRef}>
  <header className="vp-lab-header"><div><span className="vp-brand">HR Vision</span><i/><span>Лаборатория вакансии</span></div><a href="?id=hr-vision-vacancy-research--references&viewMode=story">Первый поиск и ваши отметки <ArrowUpRight size={14}/></a></header>
  <div className="vp-intro"><div><span className="vp-eyebrow">Итерация 02 · по вашим замечаниям</span><h1>Собираем портрет.<br/><span>Находим его форму.</span></h1></div><div><p>Разговор помогает понять работу.<br/>Предложения превращают ответы в требования.<br/><strong>А профиль показывает, кого мы будем искать.</strong></p><div className="vp-agreed"><Check size={14}/> Один вопрос <Check size={14}/> Предложения <Check size={14}/> Бар заполнения</div></div></div>
  <nav className="vp-main-nav" aria-label="Разделы исследования"><button aria-current={view==='references'?'page':undefined} onClick={()=>show('references')}>01 <span>Референсы профиля</span><b>8</b></button><button aria-current={view==='try'?'page':undefined} onClick={()=>show('try')}>02 <span>Три формы на наших данных</span><b>3</b></button><a className="vp-live-link" href="http://127.0.0.1:4321/iframe.html?id=hr-vision-vacancy-studio--live&viewMode=story">03 · Рабочий макет с моделью <ArrowUpRight size={14}/></a></nav>
  <main>{view==='references'?<ReferenceGallery onTry={tryDirection}/>:<section className="vp-playground">
   <div className="vp-comparison-heading"><div><span className="vp-eyebrow">Один профиль · три способа его увидеть</span><h2>Что хочется собирать и читать?</h2></div><p>Переключайте форму, добавляйте навыки, раскрывайте подробности. Принятые данные сохраняются между вариантами.</p></div>
   <div className="vp-directions">{portraitDirections.map(d=><button key={d.id} aria-pressed={direction===d.id} onClick={()=>{manual();setDirection(d.id)}}><span>{d.letter}</span><div><b>{d.title}</b><small>{d.subtitle}</small></div>{d.id==='dossier'?<ClipboardList size={20}/>:d.id==='cards'?<Layers3 size={20}/>:<FileText size={20}/>}</button>)}</div>
   <div className="vp-demo-toolbar"><p>{selected.description}<small>Основа композиции: {selected.source}</small></p><div><button onClick={()=>{manual();fillRef.current()}}>Заполненный пример <ArrowRight size={14}/></button><button onClick={()=>{setManualOverride(true);setLocalPlaying(v=>!v);onManualInteraction?.()}}>{localPlaying?<Pause size={14}/>:<Play size={14}/>} {localPlaying?'Пауза':'Автопоказ'}</button><button onClick={()=>{manual();setReset(v=>v+1)}}><RotateCcw size={14}/> Сначала</button></div></div>
   <Workspace key={reset} direction={direction} playing={localPlaying||(playing&&!manualOverride)} onManual={manual} onFillRef={onFillRef}/>
   <div className="vp-demo-boundary"><span>Живой макет</span><p>Три навыка из общего каталога ядра. Роль и условия уже заданы; ответы, предложения и примеры речи подготовлены для сравнения форм. Это локальная демонстрация, без модели и записи вакансии. Бар считает принятые предложения этого примера, а не готовность всей вакансии.</p></div>
   <div className="vp-pattern-details"><details><summary><SlidersHorizontal size={16}/> Что уже учитывает взаимодействие</summary><ul><li>Уровень и важность можно изменить до добавления и после.</li><li>Ответ работодателя сохраняется при ручных правках.</li><li>Развёрнутый навык занимает больше места, разговор остаётся рядом.</li><li>«Не добавлять» не заполняет профиль; навык можно вернуть к обсуждению.</li><li>В каждой форме используются одни и те же параметры и поведенческие шкалы.</li></ul></details><details><summary>Основа кода · Context7</summary><p>Проверены управляемое раскрытие Collapsible и структура Card в shadcn/ui, анимации layout и LayoutGroup в Motion. В макете используются React и уже установленный Motion; отдельный UI-фреймворк не добавлялся.</p><a href="https://ui.shadcn.com/docs/components/collapsible" target="_blank" rel="noreferrer">shadcn/ui · Collapsible <ArrowUpRight size={13}/></a><a href="https://motion.dev/docs/react-layout-animations" target="_blank" rel="noreferrer">Motion · layout <ArrowUpRight size={13}/></a><small>Каталог шкал: {rubricVersion}</small></details></div>
  </section>}</main>
  <footer className="vp-lab-footer"><span>HR Vision · поиск формы профиля</span><span>Сейчас выбираем взаимодействие. Затем собираем цельный путь создания вакансии.</span></footer>
 </div>
}
export function ProfileResearchLab(props:Props){return <MotionConfig reducedMotion="user"><ResearchBody key={props.runId??0} {...props}/></MotionConfig>}
