import type { CandidateFields, FieldOptions, Requirement, VacancyFields } from './matching-contract';

export const requiredLabel = (value: boolean | null) => value === null ? 'Обязательность не уточнена' : value ? 'Обязательно' : 'Не требуется';
export const formatIds = (ids: string[] | null, options: { id: string; label: string }[]) => ids === null ? 'Не уточнено'
  : ids.length ? ids.map(id => options.find(o => o.id === id)?.label ?? id).join(', ') : 'Не указаны';

export function vacancyConditionLabel(value: Requirement, options: { id: string; label: string }[]): string {
  return [value.allowed_ids?.length ? formatIds(value.allowed_ids, options) : value.required === true ? 'Значение не уточнено' : null, requiredLabel(value.required)].filter(Boolean).join(' · ');
}
export function vacancyIndustryLabel(value: VacancyFields['industry_experience'], options: FieldOptions['industries']): string {
  return [value.industry_id ? formatIds([value.industry_id], options) : value.required === true ? 'Отрасль не уточнена' : null,
    value.minimum_months === null ? value.required === true ? 'Стаж не уточнён' : null : `от ${value.minimum_months} мес.`, requiredLabel(value.required)].filter(Boolean).join(' · ');
}
export function candidateIndustryLabel(value: CandidateFields['industry_months'], options: FieldOptions['industries']): string {
  if (value === null || !Object.keys(value).length) return 'Не уточнён';
  return Object.entries(value).map(([id, months]) => `${formatIds([id], options)} · ${months} мес.`).join('; ');
}
export const factPathLabels: Record<string, string> = {
  work_locations: 'Где готов работать', employment_types: 'Формат занятости', industry_months: 'Опыт в отраслях',
  'location.required': 'География работы: обязательность', 'location.allowed_ids': 'География работы',
  'employment.required': 'Занятость: обязательность', 'employment.allowed_ids': 'Формат занятости',
  'industry_experience.required': 'Опыт в отрасли: обязательность', 'industry_experience.industry_id': 'Отрасль',
  'industry_experience.minimum_months': 'Минимальный стаж',
};
export function conflictValueLabel(path: string, value: unknown, options: FieldOptions): string {
  if (value === null || value === undefined) return 'Не уточнено';
  if (path.endsWith('.required')) return typeof value === 'boolean' ? requiredLabel(value) : 'Не уточнено';
  if (path === 'industry_months') {
    if (typeof value !== 'object' || Array.isArray(value) || Object.values(value).some(v => typeof v !== 'number' || !Number.isFinite(v) || v < 0)) return 'Не уточнено';
    return candidateIndustryLabel(value as Record<string, number>, options.industries);
  }
  if (path === 'industry_experience.industry_id') return typeof value === 'string' ? formatIds([value], options.industries) : 'Не уточнено';
  if (path === 'industry_experience.minimum_months') return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? `${value} мес.` : 'Не уточнено';
  if (Array.isArray(value) && value.every(v => typeof v === 'string')) {
    const choices = ['location.allowed_ids', 'work_locations'].includes(path) ? options.locations : options.employmentTypes;
    return formatIds(value, choices);
  }
  return 'Не уточнено';
}

type Facts = CandidateFields | VacancyFields;
/** Only an explicit per-field conflict action marks a path for resolution. */
export function withResolvedFact(fields: Facts, path: string, value: unknown): Facts {
  const candidate = 'work_locations' in fields;
  const validPath = candidate ? ['work_locations', 'employment_types', 'industry_months'].includes(path)
    : Object.hasOwn(factPathLabels, path) && path.includes('.');
  const validValue = value === null || (path.endsWith('.required') ? typeof value === 'boolean'
    : path === 'industry_experience.industry_id' ? typeof value === 'string' && !!value
    : path === 'industry_experience.minimum_months' ? typeof value === 'number' && Number.isInteger(value) && value >= 0
    : path === 'industry_months' ? typeof value === 'object' && !Array.isArray(value) && Object.values(value).every(v => typeof v === 'number' && Number.isInteger(v) && v >= 0)
    : Array.isArray(value) && value.every(v => typeof v === 'string' && !!v));
  if (!validPath || !validValue) throw new Error('Не удалось применить уточнение. Проверьте поле вручную.');
  const next = structuredClone(fields);
  if (candidate) Object.assign(next, { [path]: structuredClone(value) });
  else {
    const [group, leaf] = path.split('.') as [keyof VacancyFields, string];
    Object.assign((next as VacancyFields)[group], { [leaf]: structuredClone(value) });
  }
  return next;
}

export function factsUpdateBody(revision: string, fields: Facts, resolvedPaths: string[]) {
  return { revision, fields, ...(resolvedPaths.length ? { resolveConflicts: [...new Set(resolvedPaths)] } : {}) };
}
