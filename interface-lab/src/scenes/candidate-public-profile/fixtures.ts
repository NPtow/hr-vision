import type { AssessmentPresentation, FragmentFeedback } from '../interview-assessment/types';
import type { InterviewAssistantRequest } from '../interview-review/interview-assistant-api';
import type { RecordingChapter } from '../interview-review/InterviewReview';

/** Fictional, local-only fixtures. This module makes no API requests. */
export const person = { id: 'public-profile-demo-anna', name: 'Анна Морозова', role: 'Customer Success Manager' };
export const introUrl = '/candidate-public-profile/masha-intro.mp4';
export const interviewUrl = '/candidate-public-profile/sample-interview.mp4';
export const resume = `АННА МОРОЗОВА
Customer Success Manager
Санкт-Петербург · удалённо

О СЕБЕ
Помогаю B2B-клиентам освоить продукт и получить от него пользу. 4 года в клиентском сервисе. Люблю превращать сложные запросы в понятные следующие шаги.

ОПЫТ
2023 — 2026 · Customer Success Manager · «Линия»
Вела портфель B2B-клиентов, проводила онбординг, собирала обратную связь и готовила планы развития аккаунтов.

2022 — 2023 · Менеджер по работе с клиентами · «Маяк»
Разбирала обращения и обучала клиентов работе с сервисом.

НАВЫКИ
Онбординг · интервью с клиентами · CRM · работа с возражениями · презентации

ПОЖЕЛАНИЯ
150 000–190 000 ₽ · полная занятость · удалённо

Демонстрационный профиль. Все имена и сведения вымышлены.`;
const turns = [
  ['00:00.000', '00:07.900', 'Последние три года я работала с B2B-клиентами: помогала освоить продукт, проводила встречи и собирала обратную связь.'],
  ['00:08.000', '00:15.900', 'Сначала выясняю, какого результата хочет клиент. Потом предлагаю решение и договариваюсь, как будем измерять результат.'],
  ['00:16.000', '00:23.900', 'Если клиент говорит, что дорого, уточняю, с чем он сравнивает. Обсуждаем задачи и ценность, а не только скидку.'],
  ['00:24.000', '00:31.900', 'В новой команде сначала изучаю продукт и смотрю встречи коллег. Затем беру первые аккаунты с обратной связью наставника.'],
] as const;
export const transcript = `WEBVTT\n\n${turns.map(([start,end,text],i)=>`${i+1}\n${start} --> ${end}\nАнна Морозова: ${text}`).join('\n\n')}`;
export const chapters: RecordingChapter[] = ['Опыт','Диалог с клиентом','Возражения','Адаптация'].map((title,i)=>({title,detail:turns[i][2],fragment:turns[i][2],source:`00:${String(i*8).padStart(2,'0')}`,seconds:i*8,endSeconds:(i+1)*8,isQuote:true,speaker:person.name}));

export function makeAssessment(): AssessmentPresentation {
  const feedback: FragmentFeedback[] = [];
  const data = [
    ['dialogue','Диалог',4,'Начинает с задачи клиента и договаривается об ожидаемом результате.',1],
    ['qualification','Квалификация',3,'Описывает работу с портфелем B2B-клиентов. Масштаб портфеля стоит уточнить.',0],
    ['adaptation','Адаптация',4,'Предлагает освоение продукта через наблюдение и обратную связь.',3],
    ['objections','Возражения',3,'Уточняет причину возражения перед тем, как обсуждать решение.',2],
  ] as const;
  return {
    revisionId:'public-profile-fixture-v1',feedback,
    criteria:data.map(([id,label,level,summary,index])=>({id,label,level,target:null,state:'supported',basis:'narrated',summary,
      evidence:[{id:`demo-${id}`,text:turns[index][2],time:`00:${String(index*8).padStart(2,'0')}`,seconds:index*8,endSeconds:(index+1)*8,sourceKind:'interview',availability:'ready'}]})),
    saveFeedback:async(target,opinion,version)=>{
      const value={...target,...opinion,version:version+1};feedback.push(value);return value;
    },
  };
}

export const demoAssistant: InterviewAssistantRequest = async(action,query,_id,signal)=>{
  signal.throwIfAborted();
  const term=query.toLocaleLowerCase('ru');
  const index=/возраж|дорог|скид/.test(term)?2:/адапт|команд/.test(term)?3:/опыт|кратко/.test(term)?0:1;
  if(action==='search') {
    const matches=turns.flatMap(([, ,text],i)=>text.toLocaleLowerCase('ru').includes(term)?[{id:`demo-${i}`,text,startMs:i*8000,endMs:(i+1)*8000,speakerName:person.name}]:[]);
    return {mode:'search',matches,totalMatches:matches.length,coverage:'Демонстрационная расшифровка',model:'local-fixture',version:'1'};
  }
  const answer=/уточн/.test(term)?'На следующей встрече стоит уточнить размер портфеля клиентов и измеримые результаты. В этом коротком фрагменте их нет.':[
    'Анна рассказывает о работе с B2B-клиентами: освоение продукта, встречи и обратная связь.',
    'Анна предлагает начать с результата, который нужен клиенту, и заранее согласовать способ его измерения.',
    'В ответ на «дорого» Анна сначала уточняет, с чем клиент сравнивает предложение, и обсуждает ценность решения.',
    'Анна описывает адаптацию через изучение продукта, встречи коллег и обратную связь наставника.',
  ][index];
  return {mode:'chat',text:answer,supported:true,evidence:[{id:`demo-${index}`,quote:turns[index][2],startMs:index*8000,endMs:(index+1)*8000,speakerName:person.name}],coverage:{selected:4,omitted:0},model:'local-fixture',version:'1'};
};
