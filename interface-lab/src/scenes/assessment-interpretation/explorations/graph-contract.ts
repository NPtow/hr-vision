import type { AssessmentCriterion, AssessmentEvidence } from '../assessment-view';

export type AssessmentGraphVariant = 'orbit' | 'lanes' | 'petals' | 'matrix' | 'timeline';
export type AssessmentGraphProps = {
  criteria: AssessmentCriterion[];
  selectedId: string;
  onSelect: (id: string) => void;
  onEvidence?: (evidence: AssessmentEvidence) => void;
  duration?: number;
};

export function graphValue(criterion: AssessmentCriterion): number | null {
  const estimate = criterion.modelEstimate?.value;
  if (typeof estimate === 'number' && Number.isFinite(estimate) && estimate >= 1 && estimate <= 5) return estimate;
  return criterion.state === 'supported' && typeof criterion.level === 'number' && criterion.level >= 1 && criterion.level <= 5 ? criterion.level : null;
}

export const graphNumber = (value: number) => new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(value);

export function graphConfidence(criterion: AssessmentCriterion): number | null {
  const value = criterion.modelEstimate?.confidence;
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1 ? value : null;
}
