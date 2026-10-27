export const interestVariants = [
  { id: 'letters', label: 'Письма', story: 'interest-letters', duration: 14 },
  { id: 'signal', label: 'Импульс', story: 'interest-signal', duration: 14 },
  { id: 'cards', label: 'Карточки', story: 'interest-cards', duration: 14 },
  { id: 'original', label: 'Исходный', story: 'interest', duration: 11 },
] as const;
export type InterestVariant = typeof interestVariants[number]['id'];
export const interestVariantById = (id?: string) => interestVariants.find(v => v.id === id) ?? interestVariants[0];
export const interestSteps = [[0, 'Вакансия'], [.3, 'Приглашения'], [.78, 'Первые отклики'], [1, 'Взаимный интерес']] as const;
