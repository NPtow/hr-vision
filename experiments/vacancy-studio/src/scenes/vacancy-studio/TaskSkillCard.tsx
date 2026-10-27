import {useEffect,useRef,useState} from 'react';
import {AnimatePresence,motion,useReducedMotion} from 'motion/react';
import {Check,ChevronDown,ChevronRight,Plus} from 'lucide-react';
import {parameterById} from '../vacancy-research/model';
import type {Proposal} from './api';
import {taskChoices} from './task-options';
import './task-skill-card.css';

type Props={p:Proposal;expanded:boolean;busy:boolean;onOpen:()=>void;onClose:()=>void;
  onCommit:(values:{level:number;importance:number})=>Promise<boolean>;onDismiss:()=>void};
const importanceLabels=['','Полезно','Важно','Ключевое'];
// Reuses the candidate cabinet's CandidateSearch card expansion: layout spring
// 380/38/.8, content fade .18s with .08s delay, no second dialog/surface.
const cardSpring={type:'spring' as const,stiffness:380,damping:38,mass:.8};

export function TaskSkillCard({p,expanded,busy,onOpen,onClose,onCommit,onDismiss}:Props){
  const reduced=useReducedMotion();
  const [level,setLevel]=useState(p.level??0),[importance,setImportance]=useState(p.importance??2);
  const [step,setStep]=useState<'task'|'importance'>('task'),[saving,setSaving]=useState(false);
  const heading=useRef<HTMLButtonElement>(null),importanceHeading=useRef<HTMLButtonElement>(null);
  const parameter=parameterById(p.parameter_id!),choices=taskChoices(p);
  const tint=p.parameter_id?.startsWith('M')?'sage':p.parameter_id==='A07'?'blue':p.parameter_id==='A06.1'?'peach':'lilac';
  const selected=choices.find(choice=>choice.level===level),saved=choices.find(choice=>choice.level===p.level);
  const blocked=busy||saving;
  useEffect(()=>{if(!expanded){setLevel(p.level??0);setImportance(p.importance??2);setStep('task');}},[expanded,p.level,p.importance]);
  useEffect(()=>{if(expanded&&step==='importance')importanceHeading.current?.focus();},[expanded,step]);
  async function commit(){
    if(blocked||!selected)return;
    setSaving(true);
    try{if(await onCommit({level,importance})){onClose();heading.current?.focus();}}
    finally{setSaving(false);}
  }
  return <motion.article layout={!reduced} initial={false} transition={{layout:cardSpring}}
    className={`vs-card vs-task-card vs-tint-${tint}${expanded?' is-open':''}${p.status==='pending'?' vs-proposed':''}`}
    data-proposal-id={p.id} data-kind="skill">
    <button ref={heading} type="button" className="vs-task-trigger" disabled={saving} onClick={onOpen}
      aria-expanded={expanded} aria-controls={expanded?`task-${p.id}`:undefined} aria-label={`Открыть: ${parameter.title}`}>
      <strong>{parameter.title}</strong>{expanded?<ChevronDown size={18}/>:<ChevronRight size={18}/>}
    </button>
    {!expanded&&<button type="button" className="vs-task-summary" onClick={onOpen}>
      <span>{saved?.text??'Выбрать задачу'}</span><small>{importanceLabels[p.importance??2]}</small>
    </button>}
    <AnimatePresence initial={false}>{expanded&&<motion.div id={`task-${p.id}`} className="vs-task-body"
      initial={{opacity:0}} animate={{opacity:1}} exit={{opacity:0}}
      transition={{duration:reduced?0:.18,delay:reduced?0:.08}}>
      {step==='task'?<fieldset className="vs-task-choices" disabled={blocked}>
        <legend>Какую работу нужно выполнять?</legend>
        {choices.map(choice=><label key={choice.levelId} className={`vs-task-option${level===choice.level?' is-selected':''}`}>
          <input type="radio" name={`task-${p.id}`} value={choice.levelId} checked={level===choice.level} onChange={()=>setLevel(choice.level)}/>
          <span className="vs-task-radio" aria-hidden="true">{level===choice.level&&<Check size={13}/>}</span><span>{choice.text}</span>
        </label>)}
      </fieldset>:<button ref={importanceHeading} type="button" className="vs-task-chosen" onClick={()=>setStep('task')}>
        <span>{selected?.text}</span><ChevronRight size={16}/>
      </button>}
      {step==='importance'&&<fieldset className="vs-task-importance" disabled={blocked}>
        <legend>Важность</legend><div>{[1,2,3].map(n=><button type="button" key={n} aria-pressed={importance===n} onClick={()=>setImportance(n)}>{importanceLabels[n]}</button>)}</div>
      </fieldset>}
      <div className="vs-task-actions">
        <button type="button" className="vs-text-button" disabled={blocked} onClick={onDismiss}>{p.status==='accepted'?'Убрать':'Не добавлять'}</button>
        {step==='task'?<button type="button" className="vs-primary" disabled={blocked||!selected} onClick={()=>setStep('importance')}>Далее<ChevronRight size={16}/></button>
          :<button type="button" className="vs-primary" disabled={blocked||!selected} onClick={()=>void commit()}>{saving?'Сохраняем…':p.status==='pending'?'Добавить':'Сохранить'}{p.status==='pending'?<Plus size={16}/>:<Check size={16}/>}</button>}
      </div>
    </motion.div>}</AnimatePresence>
  </motion.article>;
}
