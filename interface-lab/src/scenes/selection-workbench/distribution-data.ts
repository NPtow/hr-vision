import type { MatchParameter, VacancyParameter } from './matching-contract';

/** Presentation only: never normalise probabilities, infer missing values, or recalculate a score. */
export type DistributionOption = { id: string; label: string; description?: string; probability: number | null; unknown?: boolean };
export type DistributionData = { options: DistributionOption[]; choice: string | null; confidence: number | null };
const record = (value: unknown): Record<string, unknown> | null => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
export const probability = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1 ? value : null;
export const probabilityLabel = (value: number | null) => value === null ? 'Нет данных' : new Intl.NumberFormat('ru-RU', { style: 'percent', maximumFractionDigits: 1 }).format(value);
export function choiceDistribution(raw: unknown, definitions: Omit<DistributionOption, 'probability'>[]): DistributionData {
  const answer = record(raw), probabilities = record(answer?.probabilities);
  return {
    options: definitions.map(option => ({ ...option, probability: probability(probabilities?.[option.id]) })),
    choice: typeof answer?.choice === 'string' && definitions.some(o => o.id === answer.choice) ? answer.choice : null,
    confidence: probability(answer?.confidence),
  };
}
export const importanceDefinitions = [
  { id: 'I0', label: 'Не требуется', description: 'Вакансия явно не требует этот навык; он не влияет на выполнение её задач. Это не отсутствие сведений.' }, { id: 'I1', label: 'Желательно', description: 'Навык нужен как вспомогательный: применяется эпизодически и не является основной частью задач.' },
  { id: 'I2', label: 'Важно', description: 'Навык существенно нужен: регулярно применяется для выполнения важных задач вакансии.' }, { id: 'I3', label: 'Очень важно', description: 'Навык критически важен: составляет основную часть задач и определяет их успешное выполнение.' },
  { id: 'insufficient_data', label: 'Недостаточно данных', unknown: true, description: 'В снимке вакансии недостаточно данных, чтобы обоснованно установить важность навыка. Это не нулевая важность.' },
];
export const importanceLabel = (value: VacancyParameter['importance']) => importanceDefinitions.find(o => o.id === (value === 'insufficient_data' ? value : `I${value}`))?.label ?? 'Не определена';
export const levelLabel = (p: VacancyParameter) => p.importance === 0 ? 'Не требуется' : p.levels.find(l => l.level_id === p.required_level_id)?.label ?? 'Не определён';
export const activeRequiredLevel = (parameter: MatchParameter, requirement?: VacancyParameter) =>
  parameter.weight === 0 || requirement?.importance === 0 ? undefined : parameter.required_level_id;
export const requirementDistributions = (p: VacancyParameter) => ({
  level: choiceDistribution(p.raw_answers?.level, [...p.levels.map(l => ({ id: l.level_id, label: l.label, description: l.description })), { id: 'insufficient_data', label: 'Недостаточно данных', unknown: true }]),
  importance: choiceDistribution(p.raw_answers?.importance, importanceDefinitions),
});
