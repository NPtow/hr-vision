export type CandidateId = 'anna' | 'mikhail' | 'elena';
export type Decision = { kind: 'review' | 'invite' | 'decline'; reason?: string; question?: string };
export type CandidateProfile = { name: string; experience: string; expectations: string; availability: string };
export type AgencyState = {
  decisions: Record<CandidateId, Decision>;
  candidateInterest: 'unknown' | 'interested' | 'declined';
  profile: CandidateProfile;
  profileSaved: boolean;
  shareConfirmed: boolean;
  meetingSlot: string;
  candidateReply: 'pending' | 'confirmed' | 'reschedule';
  candidateQuestion: string;
  candidateTimeRequest: string;
  candidateDeclineReason: string;
  coordinatorQuestion: string;
  candidateInterview: 'none' | 'hr-requested';
};
export type JourneyProps = { state: AgencyState; update: (patch: Partial<AgencyState>) => void; notify: (message: string) => void };
export const freshState = (): AgencyState => ({
  decisions: { anna: { kind: 'review' }, mikhail: { kind: 'review' }, elena: { kind: 'review' } },
  candidateInterest: 'unknown',
  profile: { name: 'Анна Миронова', experience: '5 лет в B2B-продажах. Развивала текущих клиентов и возвращала неактивные аккаунты.', expectations: 'От 150 000 ₽ на руки + прозрачный бонус', availability: 'Через 2 недели после договорённости' },
  profileSaved: false, shareConfirmed: false, meetingSlot: '', candidateReply: 'pending', candidateQuestion: '', candidateTimeRequest: '', candidateDeclineReason: '', coordinatorQuestion: '', candidateInterview: 'none',
});
export type Candidate = {
  id: CandidateId; name: string; initials: string; role: string; experience: string; city: string;
  salary: string; availability: string; summary: string; tags: string[];
  evidence: { title: string; detail: string; source: string; fragment: string }[];
  unknown: string; motivation: string; history: { company: string; role: string; years: string }[];
};
// Fictional people and evidence for product review; not a real candidate database.
export const candidates: Candidate[] = [
  { id: 'anna', name: 'Анна Миронова', initials: 'АМ', role: 'Менеджер по работе с ключевыми клиентами', experience: '5 лет в B2B', city: 'Москва', salary: '150–180 тыс. ₽', availability: 'Через 2 недели',
    summary: 'Развивала повторные продажи и возвращала клиентов, которые перестали покупать.', tags: ['Развитие клиентов', 'Повторные продажи'],
    evidence: [
      { title: 'Возвращала неактивных клиентов', detail: 'Разобрала причины ухода, разделила базу на группы и запустила повторные контакты.', source: 'Со слов кандидата · интервью, 04:20', fragment: 'Сначала я посмотрела, кто перестал покупать и почему. Разделила клиентов по причинам: цена, сроки, потерянный контакт. Для каждой группы предложила свой следующий шаг. Выручку здесь нужно отдельно подтвердить у руководителя.' },
      { title: 'Работала с длинным циклом сделки', detail: 'Вела переговоры с закупками и руководителями, фиксировала договорённости в CRM.', source: 'Со слов кандидата · интервью, 11:35', fragment: 'В одной сделке участвовали закупщик и руководитель направления. Я фиксировала, что важно каждому, и согласовывала следующий контакт. Если решение откладывали, возвращалась с новой информацией, а не просто напоминала о себе.' },
    ], unknown: 'Размер личного вклада в рост выручки. Стоит разобрать один кейс на встрече.', motivation: 'Хочет отвечать за развитие клиентов и видеть связь своего результата с бонусом.',
    history: [{ company: 'Вектор', role: 'Менеджер по ключевым клиентам', years: '2023–2026' }, { company: 'Орион', role: 'Менеджер B2B-продаж', years: '2021–2023' }] },
  { id: 'mikhail', name: 'Михаил Белов', initials: 'МБ', role: 'Менеджер B2B-продаж', experience: '4 года в B2B', city: 'Санкт-Петербург', salary: '140–170 тыс. ₽', availability: 'Через месяц',
    summary: 'Сильнее в новых продажах. Есть опыт развития клиентов после первой сделки.', tags: ['Новые продажи', 'Переговоры'],
    evidence: [{ title: 'Самостоятельно вёл сделки', detail: 'От первого контакта до договора. Работал с закупками небольших компаний.', source: 'Со слов кандидата · интервью, 06:10', fragment: 'Я сам находил компанию, выходил на закупщика и проводил сделку. После первой поставки возвращался с предложением по следующей. Отдельный план по повторным покупкам у нас не считался.' }],
    unknown: 'Готовность к роли с упором на текущую базу, а не поиск новых клиентов.', motivation: 'Ищет понятный продукт и возможность вырасти в старшего менеджера.',
    history: [{ company: 'Контур Поставка', role: 'Менеджер B2B-продаж', years: '2022–2026' }] },
  { id: 'elena', name: 'Елена Орлова', initials: 'ЕО', role: 'Аккаунт-менеджер', experience: '6 лет в B2B', city: 'Москва', salary: '180–210 тыс. ₽', availability: 'Обсуждается',
    summary: 'Развивала крупные аккаунты. Ожидания по доходу выше согласованной вилки.', tags: ['Ключевые клиенты', 'Дополнительные продажи'],
    evidence: [{ title: 'Расширяла работу с текущими клиентами', detail: 'Выявляла новые задачи клиента и подключала дополнительные направления.', source: 'Со слов кандидата · интервью, 09:45', fragment: 'У одного клиента мы начинали с одного направления. На встречах я узнала о задачах соседних команд и познакомила их с нашими специалистами. В итоге появились новые проекты.' }],
    unknown: 'Условия перехода и возможность договориться в рамках бюджета.', motivation: 'Хочет более широкую ответственность за портфель клиентов.',
    history: [{ company: 'Сигма', role: 'Аккаунт-менеджер', years: '2020–2026' }] },
];
export const slots = ['7 октября · 11:00 МСК', '7 октября · 15:00 МСК', '8 октября · 12:00 МСК'];
