import type { AssessmentCriterion, AssessmentEvidence } from './types';

export type AssessmentGraphProps = {
  criteria: AssessmentCriterion[];
  selectedId: string;
  onSelect: (id: string) => void;
  onEvidence?: (evidence: AssessmentEvidence, criterionId: string) => void;
};

export function graphValue(criterion: AssessmentCriterion): number | null {
  const estimate = criterion.modelEstimate?.value;
  if (typeof estimate === 'number' && Number.isFinite(estimate) && estimate >= 1 && estimate <= 5) return estimate;
  const level = criterion.level;
  return criterion.state === 'supported' && typeof level === 'number' && Number.isFinite(level) && level >= 1 && level <= 5 ? level : null;
}

export const graphNumber = (value: number) => new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(value);

const aliases: Record<string, string> = {
  A01: 'Диалог и слушание', A03: 'Квалификация', A08: 'CRM и сопровождение',
  'NEW-01': 'Передача лида', A10: 'Продукт и обещания', A07: 'Следующий шаг',
  A04: 'Понимание задачи', 'A06.1': 'Работа с возражениями', 'A11.1': 'Гибкость в разговоре',
};
export const skillName = (criterion: Pick<AssessmentCriterion, 'id' | 'label'>) => aliases[criterion.id] || criterion.label;
