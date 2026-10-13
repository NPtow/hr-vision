import { candidates, type Candidate } from './candidate-data.ts';

export type DemoRequirement = { id: string; label: string };
export type DemoEvidence = {
  criterionId: string;
  label: string;
  fact: string;
  quote: string;
  timecode: string;
};
export type DemoMatch = {
  id: string;
  person: Candidate;
  evidence: DemoEvidence[];
  interview: DemoEvidence[];
  interest: string;
  followUp: string;
  missing: DemoRequirement[];
};
export type DemoMatches = {
  recognized: boolean;
  requirements: DemoRequirement[];
  matches: DemoMatch[];
};

export function candidateCountLabel(count: number): string {
  const lastTwo = count % 100;
  const last = count % 10;
  const noun = lastTwo >= 11 && lastTwo <= 14 ? 'кандидатов'
    : last === 1 ? 'кандидат'
    : last >= 2 && last <= 4 ? 'кандидата'
    : 'кандидатов';
  return `${count} ${noun}`;
}

export const demoCriteria: DemoRequirement[] = [
  { id: 'returning-clients', label: 'Возвращать клиентов' },
  { id: 'b2b-sales', label: 'Развивать B2B-продажи' },
  { id: 'bitrix24', label: 'Работать в Битрикс24' },
  { id: 'crm-workflows', label: 'Настраивать CRM' },
  { id: 'process-ownership', label: 'Выстраивать процессы' },
  { id: 'team-leadership', label: 'Развивать команду' },
  { id: 'project-delivery', label: 'Вести проекты' },
];

export const demoTaskExamples = [
  { label: 'Вернуть клиентов', text: 'Вернуть ушедших клиентов и развивать повторные B2B-продажи.' },
  { label: 'Навести порядок в CRM', text: 'Настроить Битрикс24 и CRM-воронку, чтобы сделки не терялись.' },
  { label: 'Собрать команду', text: 'Выстроить процессы в отделе и развивать команду продаж.' },
  { label: 'Вести проекты', text: 'Вести проекты в срок и координировать работу команды.' },
];

const criterionMatchers: Record<string, RegExp> = {
  'returning-clients': /верну[тл]|возврат|ушедш|потерянн.{0,18}клиент|повторн.{0,16}(?:продаж|заказ)|удержан.{0,16}клиент/iu,
  'b2b-sales': /b2b|б2б|б2b|b2б|корпоративн.{0,18}(?:продаж|клиент)|оптов|продаж/iu,
  'bitrix24': /битрикс|bitrix/iu,
  'crm-workflows': /crm|црм|срм|воронк|автоматиз|не терял.{0,12}сделк|сделки не терял|сделки не теря/iu,
  'process-ownership': /процесс|с нуля|операцион|систематиз|регламент|порядок в отдел/iu,
  'team-leadership': /команд|руковод|управля|нанят|обучен.{0,14}(?:менеджер|сотрудник)/iu,
  'project-delivery': /проект|срок|координир|дедлайн|запуск/iu,
};

/** A transparent, finite demo vocabulary. Unknown input never invents a match. */
export function deriveRequirements(text: string): DemoRequirement[] {
  const normalized = text.trim().toLocaleLowerCase('ru-RU').replace(/ё/g, 'е');
  if (!normalized) return [];
  return demoCriteria.filter(criterion => criterionMatchers[criterion.id].test(normalized));
}

function evidence(criterionId: string, fact: string, quote: string, timecode: string): DemoEvidence {
  return { criterionId, label: demoCriteria.find(criterion => criterion.id === criterionId)!.label, fact, quote, timecode };
}

// Fictional interview fragments belong to their named demo profiles, not to a live search.
const profiles = [
  {
    id: 'anna-mironova', personIndex: 0,
    interest: 'Хочет развивать действующую клиентскую базу',
    followUp: 'Уточнить опыт в вашей CRM: Битрикс24 пока не использовала.',
    interview: [
      evidence('returning-clients', 'Возвращала клиентов после отказа от сотрудничества', 'Сначала выясняла, почему клиент перестал заказывать. Делила базу по причинам и готовила своё предложение.', '04:20'),
      evidence('b2b-sales', 'Три года развивала повторные B2B-продажи', 'Последние три года я работала с действующей B2B-базой. Моя зона — продления, повторные заказы и рост клиента.', '01:22'),
    ],
  },
  {
    id: 'mikhail-belov', personIndex: 1,
    interest: 'Готов обсудить продажи с опорой на CRM',
    followUp: 'Обсудить длину сделки и специфику вашего продукта.',
    interview: [
      evidence('bitrix24', 'Четыре года ведёт сделки в Битрикс24', 'В Битрикс24 работаю четыре года: от первого контакта до повторной продажи. Настраивал этапы и обязательные поля.', '09:35'),
      evidence('b2b-sales', 'Вёл полный цикл продаж корпоративным клиентам', 'Сам выходил на клиента, разбирал задачу, готовил предложение и доводил сделку до оплаты.', '02:18'),
      evidence('crm-workflows', 'Настраивал воронку и контроль следующих действий', 'У каждой сделки было следующее действие и дата. Если контакта не было, сделка попадала в отдельный список на разбор.', '11:04'),
    ],
  },
  {
    id: 'elena-orlova', personIndex: 2,
    interest: 'Интересна работа с долгосрочными клиентами',
    followUp: 'Уточнить, насколько самостоятельно вела переговоры об условиях.',
    interview: [
      evidence('returning-clients', 'Возобновляла работу с неактивными клиентами', 'Я выбирала клиентов без заказа за последние три месяца и искала конкретный повод вернуться к разговору.', '05:12'),
      evidence('b2b-sales', 'Развивала ключевые аккаунты и повторные заказы', 'С каждым ключевым клиентом строили план на квартал: что уже покупают и какие задачи ещё можем закрыть.', '03:40'),
      evidence('crm-workflows', 'Систематизировала историю работы с клиентами', 'Собрала историю договорённостей в CRM, чтобы следующий разговор начинался с контекста, а не с нуля.', '08:16'),
    ],
  },
  {
    id: 'ilya-sokolov', personIndex: 3,
    interest: 'Хочет выстроить работу отдела продаж',
    followUp: 'Согласовать масштаб команды и границы самостоятельности.',
    interview: [
      evidence('process-ownership', 'Построил процесс работы отдела с нуля', 'Начал с пути сделки: кто отвечает за каждый шаг, что считаем результатом и где теряем клиента.', '03:26'),
      evidence('team-leadership', 'Нанимал и обучал менеджеров продаж', 'Новые менеджеры сначала разбирали реальные звонки, затем проводили свои и получали конкретную обратную связь.', '07:14'),
      evidence('b2b-sales', 'Руководил отделом корпоративных продаж', 'Вёл отдел B2B-продаж: план, качество переговоров и самые сложные клиентские сделки.', '01:58'),
    ],
  },
  {
    id: 'maria-volkova', personIndex: 4,
    interest: 'Готова обсуждать запуск нового проекта',
    followUp: 'Уточнить предметную область и состав команды проекта.',
    interview: [
      evidence('project-delivery', 'Вела проекты от постановки задачи до запуска', 'Разбивала цель на этапы с понятным результатом. Раз в неделю проверяли риски и пересобирали план, если что-то изменилось.', '02:42'),
      evidence('team-leadership', 'Координировала работу команды из разных функций', 'Собирала вокруг задачи разработку, маркетинг и продажи. У каждого был свой результат и понятный срок.', '06:08'),
      evidence('process-ownership', 'Ввела единые правила передачи задач', 'Зафиксировали, что передаём вместе с задачей, кто её принимает и как узнаём, что она действительно закончена.', '10:15'),
    ],
  },
  {
    id: 'daniil-morozov', personIndex: 5,
    interest: 'Хочет развивать новое B2B-направление',
    followUp: 'Проверить опыт работы с вашей длительностью цикла продаж.',
    interview: [
      evidence('b2b-sales', 'Выводил B2B-продукт в новые клиентские сегменты', 'Начинал с интервью клиентов и проверял, кому наш продукт помогает сильнее всего. Затем тестировал предложение.', '03:04'),
      evidence('project-delivery', 'Запускал коммерческие пилоты', 'Для пилота договаривались о критерии успеха и дате решения. После этого было понятно, масштабируем или меняем подход.', '08:30'),
    ],
  },
  {
    id: 'alina-kim', personIndex: 6,
    interest: 'Интересно сделать CRM удобной для команды',
    followUp: 'Обсудить необходимые интеграции и текущие данные.',
    interview: [
      evidence('bitrix24', 'Настраивала Битрикс24 под процессы отдела', 'Сначала разбирала, как команда работает сейчас, затем настраивала этапы, права и автоматические действия в Битрикс24.', '02:54'),
      evidence('crm-workflows', 'Настраивала воронки и автоматизацию CRM', 'Убрали ручные напоминания: система сама ставила следующую задачу и показывала сделки без движения.', '06:11'),
      evidence('process-ownership', 'Описывала процесс перед автоматизацией', 'Если процесс непонятен, автоматизация только ускоряет хаос. Сначала согласовывали правила, затем переносили их в CRM.', '09:18'),
    ],
  },
  {
    id: 'alexey-petrov', personIndex: 7,
    interest: 'Готов обсуждать развитие коммерческой команды',
    followUp: 'Сверить уровень роли, полномочия и задачи на первый квартал.',
    interview: [
      evidence('team-leadership', 'Развивал руководителей и коммерческую команду', 'С руководителями разбирали решения, а не только цифры: где нужна помощь и что они смогут сделать сами.', '05:40'),
      evidence('process-ownership', 'Согласовывал общие правила работы коммерческого блока', 'Связали маркетинг, продажи и сопровождение едиными этапами. Передача клиента перестала зависеть от личных договорённостей.', '08:22'),
      evidence('b2b-sales', 'Развивал B2B-продажи и клиентское сопровождение', 'Смотрел на весь путь: как приходит клиент, что ему обещаем и насколько это выполняется после продажи.', '02:10'),
    ],
  },
];

export function getDemoMatches(text: string, criterionIds?: string[]): DemoMatches {
  const requirements = criterionIds === undefined
    ? deriveRequirements(text)
    : demoCriteria.filter(criterion => criterionIds.includes(criterion.id));
  if (requirements.length === 0) return { recognized: false, requirements, matches: [] };
  const ids = new Set(requirements.map(requirement => requirement.id));
  const matches = profiles
    .map(profile => ({
      id: profile.id,
      person: candidates[profile.personIndex],
      evidence: profile.interview.filter(item => ids.has(item.criterionId)),
      interview: profile.interview,
      interest: profile.interest,
      followUp: profile.followUp,
      missing: requirements.filter(requirement => !profile.interview.some(item => item.criterionId === requirement.id)),
    }))
    .filter(profile => profile.evidence.length > 0)
    .sort((a, b) => b.evidence.length - a.evidence.length)
    .slice(0, 3);
  return { recognized: true, requirements, matches };
}
