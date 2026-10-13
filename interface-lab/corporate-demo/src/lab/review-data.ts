import { candidates } from '../candidate-data';

// Authored demo scenarios. These are not live candidate assessments.
export const reviewMatches = [
  {
    id: 'bitrix', feedback: 'Не хватает опыта в Битрикс24', criterion: 'Опыт в Битрикс24',
    person: candidates[1], accusative: 'Михаила', headline: ['Битрикс24.', '4 года.'],
    proof: 'Вёл сделки и настраивал воронки', skill: 'Битрикс24 · 4 года',
    quote: 'Вёл сделки в Битрикс24. Сам настраивал этапы воронки и автоматические задачи.',
    marked: 'Сам настраивал этапы воронки', time: '09:35',
    strengths: [
      { title: 'Настраивать воронку', detail: 'Сам настраивал этапы продаж' },
      { title: 'Автоматизировать работу', detail: 'Настраивал автоматические задачи' },
    ],
    reason: 'Уже работает в вашей CRM.', reasonDetail: 'И сам настраивает воронку под продажи.',
  },
  {
    id: 'initiative', feedback: 'Хочется больше инициативы', criterion: 'Самостоятельно выстраивать работу',
    person: candidates[3], accusative: 'Илью', headline: ['Сам строит', 'процесс.'],
    proof: 'Запустил продажи с нуля', skill: 'Самостоятельность',
    quote: 'Сам собрал процесс: от первого контакта до повторной продажи. Команда выросла до шести человек.',
    marked: 'Сам собрал процесс', time: '12:10',
    strengths: [
      { title: 'Выстраивать процесс продаж', detail: 'От первого контакта до повторной продажи' },
      { title: 'Развивать команду', detail: 'Команда выросла до шести человек' },
    ],
    reason: 'Сам запускал процессы продаж.', reasonDetail: 'И хочет развивать новое направление.',
  },
] as const;
export type ReviewMatch = typeof reviewMatches[number];
