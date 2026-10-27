import {useState,type ReactNode} from 'react';
import {Check,Pencil,X} from 'lucide-react';

export type ProfileField={key:string;label:string;value:string;multiline?:boolean;options?:string[]};

export function InlineProfileBlock({id,label,enabled,active,onOpen,onClose,onSave,fields,children}:{
 id:string;label:string;enabled:boolean;active:boolean;onOpen:()=>void;onClose:()=>void;
 onSave:(values:Record<string,string>)=>void;fields:ProfileField[];children:ReactNode;
}){
 if(active)return <InlineForm key={id} label={label} fields={fields} onClose={onClose} onSave={onSave}/>;
 return <div className={'cpds-editable'+(enabled?' is-editable':'')}>
  <div inert={enabled?true:undefined}>{children}</div>
  {enabled&&<button className="cpds-edit-hit" aria-label={`Изменить: ${label}`} onClick={onOpen}><span><Pencil size={14}/>{label}</span></button>}
 </div>;
}

function InlineForm({label,fields,onClose,onSave}:{label:string;fields:ProfileField[];onClose:()=>void;onSave:(values:Record<string,string>)=>void}){
 const [values,setValues]=useState(()=>Object.fromEntries(fields.map(f=>[f.key,f.value])));
 return <form className="cpds-inline-form cpds-surface" aria-label={`Редактирование: ${label}`} onSubmit={event=>{event.preventDefault();onSave(Object.fromEntries(Object.entries(values).map(([key,value])=>[key,value.trim()])));}}>
  <div className="cpds-inline-heading"><h3>{label}</h3><button type="button" aria-label={`Отменить: ${label}`} onClick={onClose}><X size={17}/></button></div>
  {fields.map((field,index)=><label key={field.key}><span>{field.label}</span>{field.multiline?
   <textarea autoFocus={index===0} rows={6} value={values[field.key]} onChange={e=>setValues(v=>({...v,[field.key]:e.target.value}))}/>:
   field.options?<select autoFocus={index===0} value={values[field.key]} onChange={e=>setValues(v=>({...v,[field.key]:e.target.value}))}>{field.options.map(option=><option key={option}>{option}</option>)}</select>:
   <input autoFocus={index===0} required value={values[field.key]} onChange={e=>setValues(v=>({...v,[field.key]:e.target.value}))}/>}</label>)}
  <div className="cpds-inline-actions"><button type="button" className="cpds-text-button" onClick={onClose}>Отмена</button><button className="cpds-primary" type="submit"><Check size={15}/>Сохранить</button></div>
 </form>;
}
