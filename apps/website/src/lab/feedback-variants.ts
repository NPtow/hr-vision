export const feedbackVariants = [
  { id: 'graph', label: 'Граф', story: 'feedback-graph', duration: 30 },
  { id: 'reshuffle', label: 'Пересборка', story: 'feedback-reshuffle', duration: 16 },
  { id: 'compare', label: 'До / после', story: 'feedback-compare', duration: 14 },
  { id: 'original', label: 'Исходный', story: 'feedback', duration: 12 },
] as const;
export type FeedbackVariant = typeof feedbackVariants[number]['id'];
export const feedbackVariantById = (id?: string) => feedbackVariants.find(v => v.id === id) ?? feedbackVariants[0];
export const feedbackSteps = [[0, 'После встречи'], [.23, 'Ваш фидбек'], [.48, 'Новое требование'], [.64, 'Меняется подборка'], [1, 'Новый кандидат']] as const;

export const feedbackGraphSteps = [[0, 'Исходный подбор'], [.23, 'Ваш ответ'], [.44, 'Новое требование'], [.65, 'Новые связи'], [.88, 'Другая подборка'], [1, 'Следующий цикл']] as const;
export const reviewFeedbackSteps = [[0, 'Интервью просмотрено'], [.12, 'Ваш фидбэк'], [.46, 'Новое условие'], [.64, 'Пересборка'], [1, 'Новый кандидат']] as const;
