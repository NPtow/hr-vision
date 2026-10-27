import interviewSample from './interview-sample.json';
export const candidateName = 'Анастасия Глухова';

// Illustrative content for layout comparison. Never imported by the service entry.
export const transcript = interviewSample;

export const variants = [
  { id: 'inline', number: '01', label: 'Одна строка', caption: 'Имя, резюме и решение — в одной строке. Документ открывается попапом.', tradeoff: 'Рекомендую: всё видно сразу, а интервью начинается выше.' },
  { id: 'overlay', number: '02', label: 'Внутри видео', caption: 'Имя и резюме расположены поверх видео. Отдельной шапки нет.', tradeoff: 'Максимум места для записи. На светлом видео нужна подложка.' },
  { id: 'side', number: '03', label: 'Рядом с видео', caption: 'Резюме заменяет расшифровку справа; интервью остаётся перед глазами.', tradeoff: 'Удобно сверять опыт с ответами. Для чтения PDF есть разворот в попап.' },
] as const;
export type HeaderVariant = typeof variants[number]['id'];
