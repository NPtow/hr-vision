export type ViewId = 'E01'|'E02'|'E03'|'E04'|'E05'|'H01'|'H02'|'H03'|'H04'|'A01'|'A02'|'A03'|'A04'|'A05'|'A06'|'C01'|'C02'|'C03'|'C04';
export type Role = 'manager'|'hr'|'agency'|'candidate';
export const roles: Record<Role,string> = {manager:'Нанимающий',hr:'HR компании',agency:'Команда HR Vision',candidate:'Кандидат'};
export const views: {id:ViewId;role:Role;name:string;job:string;future?:boolean}[] = [
{id:'E01',role:'manager',name:'Обзор найма',job:'Понять, где нужно моё решение'},
{id:'E02',role:'manager',name:'Задача и роль',job:'Согласовать, кого и зачем нанимаем'},
{id:'E03',role:'manager',name:'Подборка кандидатов',job:'Выбрать, с кем продолжить'},
{id:'E04',role:'manager',name:'Карточка кандидата',job:'Проверить доказательства и принять решение'},
{id:'E05',role:'manager',name:'Оффер и выход',job:'Согласовать условия и дождаться выхода',future:true},
{id:'H01',role:'hr',name:'Вакансии и задачи',job:'Организовать работу по вакансиям'},
{id:'H02',role:'hr',name:'Поток кандидатов',job:'Понять, кто на каком этапе'},
{id:'H03',role:'hr',name:'Проверка кандидата',job:'Изучить переданную оценку и уточнить пробелы'},
{id:'H04',role:'hr',name:'Встречи и следующие шаги',job:'Не потерять кандидата между этапами'},
{id:'A01',role:'agency',name:'Компании и сигналы',job:'Найти компанию с потребностью в найме'},
{id:'A02',role:'agency',name:'Заказы и команда',job:'Распределить работу по запросу клиента'},
{id:'A03',role:'agency',name:'База кандидатов',job:'Найти подходящих людей в общей базе'},
{id:'A04',role:'agency',name:'Интервью и оценка',job:'Собрать и проверить доказательства'},
{id:'A05',role:'agency',name:'Подборки клиентам',job:'Передать готовых кандидатов заказчику'},
{id:'A06',role:'agency',name:'Сопровождение',job:'Довести договорённости до выхода'},
{id:'C01',role:'candidate',name:'Приглашение',job:'Понять предложение и разрешить показ профиля'},
{id:'C02',role:'candidate',name:'Интервью и задания',job:'Пройти проверку и показать опыт'},
{id:'C03',role:'candidate',name:'Моя вакансия',job:'Понять условия и следующий шаг'},
{id:'C04',role:'candidate',name:'Мой поиск',job:'Сравнить параллельные варианты',future:true},
];
export const people = [
{id:'anna',name:'Анна Миронова',initials:'АМ',experience:'5 лет в B2B-продажах',city:'Москва',salary:'от 150 000 ₽',score:'4,3',summary:'Выстроила обработку входящих заявок в команде из 6 человек.',gap:'Нужно уточнить управление командой',ready:true},
{id:'max',name:'Максим Лебедев',initials:'МЛ',experience:'7 лет в B2B-продажах',city:'Москва',salary:'от 180 000 ₽',score:'4,1',summary:'Вёл длинные сделки и переговоры с производственными компаниями.',gap:'Ожидания выше согласованной вилки',ready:true},
{id:'elena',name:'Елена Соколова',initials:'ЕС',experience:'4 года в клиентском сервисе',city:'Тверь',salary:'от 130 000 ₽',score:'—',summary:'Работала с повторными продажами и развитием клиентской базы.',gap:'Не хватает примера работы с новым клиентом',ready:false},
];
export type Person = typeof people[number];
export type DemoState = {
 businessProblem:string;businessResult:string;roleRequirements:string;candidateInterest:boolean;counterProposal:string;declineReason:string;clarifications:Record<string,string>;meetings:Record<string,string>;candidateExperience:string;candidateExpectations:string;interviewAnswer:string;
 briefApproved:boolean;orderCreated:boolean;owner:string;consent:boolean;interviewRoute:'hr'|'ai'|null;interviewDone:boolean;assessmentReady:boolean;batchSent:boolean;
 selected:string;decisions:Record<string,string>;reasons:Record<string,string>;meeting:string;offerApproved:boolean;offerSent:boolean;offerResponse:'pending'|'accepted'|'counter'|'declined';started:boolean;replacement:boolean;
 salary:string;startDate:string;kpi:string;candidateDeadline:string;documentsReady:boolean;
};
export const initialState:DemoState={businessProblem:"Теряем часть входящих заявок: клиенту отвечают поздно, повторный контакт не назначают.",businessResult:"Организовать обработку входящих обращений и повысить конверсию во встречу.",roleRequirements:"Опыт B2B-продаж; работа с CRM; самостоятельное ведение сделки.",candidateInterest:true,counterProposal:"",declineReason:"",clarifications:{},meetings:{},candidateExperience:"5 лет в B2B-продажах. Выстроила обработку входящих заявок в команде из 6 человек.",candidateExpectations:"От 150 000 ₽ + бонус. Интересна понятная зона ответственности и возможность влиять на процесс.",interviewAnswer:"",briefApproved:false,orderCreated:false,owner:'Мария · рекрутер',consent:false,interviewRoute:null,interviewDone:false,assessmentReady:false,batchSent:false,selected:'anna',decisions:{},reasons:{},meeting:'',offerApproved:false,offerSent:false,offerResponse:'pending',started:false,replacement:false,salary:'150 000 ₽ + бонус',startDate:'19 октября',kpi:'В первый месяц: все входящие заявки получают ответ в течение 30 минут.',candidateDeadline:'Нужен ответ до 9 октября',documentsReady:false};
export type ViewProps={view:ViewId;state:DemoState;update:(patch:Partial<DemoState>)=>void;go:(view:ViewId)=>void;notify:(message:string)=>void};
