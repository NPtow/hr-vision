export type Attachment = {id:string;name:string;characters:number;kind:'file'|'link'};
export type Proposal = {
  id:string; kind:'fact'|'skill'; status:'pending'|'accepted'|'dismissed'|'superseded'; manual:boolean; automatic?:boolean;
  key?:string; value?:string; hard_filter?:boolean; parameter_id?:string;
  level?:number; importance?:number; confidence?:'low'|'medium'|'high';
  task_scenarios?:Record<string,string>;
  source_id:string; quote:string; reason:string; original:Record<string,unknown>;
};
export type QuestionTarget = {kind:'skill'|'condition'|'context';key:string;decision:string;label:string};
export type Answer = {option_index:number}|{defer:true};
export type Message = {id:string;role:'user'|'assistant';content:string;question?:string;question_target?:QuestionTarget;options?:string[];proposalIds?:string[];sourceIds?:string[];in_reply_to?:{id?:string;question?:string;question_target?:QuestionTarget};answer?:Answer;interview_complete?:boolean};
export type VacancyContextFact = {value:string;proposal_id:string;source_id:string;quote:string;reason:string;manual:boolean;automatic:boolean;hard_filter:boolean;suggested_hard_filter:boolean;requirement_status?:'required'|'preferred'|'unknown'};
export type StudioState = {
  id:string;revision:number;version:string;rubricVersion:string;title:string;summary:string;
  context_version:number;vacancy_context:Record<string,VacancyContextFact>;
  proposals:Proposal[];messages:Message[];sources:Attachment[];confirmed:boolean;
  active_question_id?:string|null;interview_progress?:{version:string;unresolved:QuestionTarget[]};
  pending:null|{id:string;status:'running'|'failed';phase:string;error?:string;started_at?:number;updated_at?:number;phase_started_at?:number;attempt_started_at?:number};
};
export type Health = {ok:boolean;model:string;voice:boolean;version:string;rubricVersion:string};
export const BASE = 'http://127.0.0.1:4321';
export const SESSION_KEY = 'hr-vision.vacancy-studio.session.v1';
const errors:Record<string,string> = {
  MODEL_UNAVAILABLE:'Модель сейчас не ответила. Ваш текст сохранён — можно повторить.',
  INVALID_MODEL_OUTPUT:'Ответ модели не прошёл проверку формата или цитат. Попробуйте ещё раз.',
  REVIEW_REJECTED:'В ответе модели нашлись неподтверждённые выводы. Они не добавлены. Попробуйте ещё раз.',
  REVISION_CONFLICT:'Профиль изменился в другой вкладке. Обновили его — повторите действие.',
  STALE_QUESTION:'Этот вопрос уже изменился или решён. Обновили профиль; ваш текст сохранён.',
  MODEL_NOT_CONFIGURED:'Сервис моделей ещё не подключён.',
  VOICE_NOT_CONFIGURED:'Распознавание голоса пока недоступно.',
  TRANSCRIPTION_FAILED:'Не удалось распознать запись. Попробуйте ещё раз.',
  NO_SPEECH:'Речь не распознана. Попробуйте записать ответ ещё раз.',
  UNSAFE_INSTRUCTIONS:'В тексте есть инструкции для изменения работы модели. Уберите их и опишите только вакансию.',
  FILE_TYPE:'Подойдут PDF, DOCX, TXT, MD или CSV.',
  FILE_UNREADABLE:'Файл не удалось прочитать. Можно вставить его текст в сообщение.',
  FILE_TOO_LARGE:'Файл должен быть не больше 5 МБ.',
  PDF_UNSUPPORTED:'Нужен PDF без пароля, до 60 страниц.',
  EMPTY_DOCUMENT:'В файле нет читаемого текста. Для скана вставьте текст вручную.',
  DOCUMENT_TOO_LONG:'Документ слишком длинный. Прикрепите фрагмент до 60 000 символов.',
  CORPUS_TOO_LARGE:'Материалов слишком много для одного разбора. Начните новую вакансию с более коротким описанием.',
  LINK_UNSAFE:'Эту ссылку нельзя безопасно открыть. Вставьте текст вакансии в сообщение.',
  LINK_UNAVAILABLE:'Сайт не отдал текст вакансии. Скопируйте его в сообщение или прикрепите файл.',
  LINK_TYPE:'По этой ссылке нет страницы с текстом. Файл можно прикрепить отдельно.',
  SERVER_RESTARTED:'Сервис перезапустился. Ваш ответ сохранён — повторите обработку.',
  TURN_RUNNING:'Предыдущий ответ ещё обрабатывается.',
  BUSY:'Сейчас обрабатываются другие ответы. Попробуйте чуть позже.',
  CONVERSATION_LIMIT:'Достигнут предел этого диалога. Начните новую вакансию.',
  MODEL_BUDGET:'Достигнут лимит запросов этого диалога.',
  VOICE_BUDGET:'Достигнут лимит голосовых записей этого диалога.',
  SESSION_REQUIRED:'Черновик не найден. Создайте новую вакансию.',
  SOURCE_LIMIT:'В этом диалоге уже 20 документов.',
  PARAMETER_ALREADY_PRESENT:'Для этого параметра уже есть более новое предложение или принятое требование.',
  CONTEXT_NOT_FILTER:'Отрасль компании и название роли описывают вакансию. Требование к опыту задаётся отдельно.',
};
export function errorText(code:string) {return errors[code]??'Не удалось выполнить действие. Попробуйте ещё раз.';}
export class ApiError extends Error {constructor(public code:string){super(errorText(code));}}
export async function request<T>(path:string,session?:string,body?:unknown,signal?:AbortSignal):Promise<T> {
  const controller=new AbortController();
  const abort=()=>controller.abort(); signal?.addEventListener('abort',abort,{once:true});
  const timer=setTimeout(abort,path.includes('sources/link')?35_000:25_000);
  try {
    const r=await fetch(BASE+path,{method:body===undefined?'GET':'POST',headers:{...(session?{Authorization:'Bearer '+session}:{}),...(body!==undefined?{'Content-Type':'application/json'}:{})},body:body===undefined?undefined:JSON.stringify(body),signal:controller.signal});
    const d=await r.json(); if(!r.ok)throw new ApiError(d.error); return d;
  } catch(e) {
    if(e instanceof ApiError)throw e;
    throw new Error('Не удалось связаться с локальным сервисом. Черновик в этом окне сохранён.');
  } finally {clearTimeout(timer);signal?.removeEventListener('abort',abort);}
}
export async function getSession():Promise<StudioState> {
  const sid=localStorage.getItem(SESSION_KEY);
  if(sid){try{return await request<StudioState>('/api/state',sid);}catch(e){if(!(e instanceof ApiError&&e.code==='SESSION_REQUIRED'))throw e;}}
  return newSession();
}
export async function newSession():Promise<StudioState>{
  const s=await request<StudioState>('/api/sessions',undefined,{});localStorage.setItem(SESSION_KEY,s.id);return s;
}
export async function uploadFile(session:string,file:File):Promise<Attachment>{
  if(file.size>5_000_000)throw new ApiError('FILE_TOO_LARGE');
  const bytes=new Uint8Array(await file.arrayBuffer());let binary='';
  for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
  return request('/api/sources/file',session,{name:file.name,data:btoa(binary)});
}
export async function transcribe(session:string,blob:Blob,signal:AbortSignal):Promise<string>{
  const r=await fetch(BASE+'/api/transcribe',{method:'POST',headers:{Authorization:'Bearer '+session,'Content-Type':blob.type},body:blob,signal});
  const d=await r.json();if(!r.ok)throw new ApiError(d.error);return d.text;
}
