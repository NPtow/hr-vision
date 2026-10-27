export const interviewVariants = [
  { id: 'focus', label: 'Фокус', story: 'interview-focus', duration: 54 },
  { id: 'closeup', label: 'Крупный план', story: 'interview-closeup', duration: 54 },
  { id: 'original', label: 'Исходный', story: 'interview', duration: 24 },
] as const;
export type InterviewVariant = typeof interviewVariants[number]['id'];
export const interviewVariantById = (id?: string) => interviewVariants.find(v => v.id === id) ?? interviewVariants[0];
export const interviewSteps = [[0, 'Обзор'], [.084, 'Запись'], [.24, 'Ответы'], [.366, 'Оценка'], [.51, 'Почему Анна'], [.648, 'Ваш фидбэк'], [.856, 'Пересборка'], [1, 'Новый кандидат']] as const;
