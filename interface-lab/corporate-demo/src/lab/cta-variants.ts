import { CTA_OFFER_SECONDS } from '../cta-offer';
export { ctaSnapshot } from '../cta-offer';

export const ctaVariants = [
  { id: 'perimeter', label: 'Контур', story: 'cta-perimeter', duration: CTA_OFFER_SECONDS },
  { id: 'liquid', label: 'Уровень', story: 'cta-liquid', duration: CTA_OFFER_SECONDS },
  { id: 'ribbon', label: 'Лента', story: 'cta-ribbon', duration: CTA_OFFER_SECONDS },
] as const;
export type CtaVariant = typeof ctaVariants[number]['id'];
export const ctaVariantById = (id?: string) => ctaVariants.find(v => v.id === id) ?? ctaVariants[0];
export const ctaSteps = [[0, '50% · Первые 10 с'], [.25, '30% · Таймер 00:30'], [.75, '30% · Осталось 10 с'], [1, 'Скидка закончилась']] as const;

