/** HR adapter contracts agreed with «Ядро». No model calls or scoring in the browser. */
export type HRInterview = {
  state: 'ready' | 'processing' | 'missing' | 'incomplete' | 'unavailable';
  recordingCount: number | null; transcriptCount: number | null; allPartsReady: boolean;
};
export const hasHRInterview = (p: { hrInterview?: HRInterview }) => p.hrInterview?.state === 'ready'
  && p.hrInterview.allPartsReady === true && (p.hrInterview.recordingCount ?? 0) > 0
  && (p.hrInterview.transcriptCount ?? 0) > 0;

export type Requirement = { required: boolean | null; allowed_ids: string[] | null };
export type VacancyFields = {
  location: Requirement; employment: Requirement;
  industry_experience: { required: boolean | null; industry_id: string | null; minimum_months: number | null };
};
export type CandidateFields = { work_locations: string[] | null; employment_types: string[] | null; industry_months: Record<string, number> | null };
export type FieldOptions = { locations: { id: string; label: string }[]; employmentTypes: { id: string; label: string }[]; industries: { id: string; label: string }[] };
export type FieldSource = { kind: string; sourceRevision: string | null; sourceFields: string[]; state: string; author?: string; manualRevision?: string };
export type FieldConflict = { manualValue: unknown; sourceValue: unknown; manualSourceRevision: string | null; sourceRevision: string | null };
export type FieldEvidence = { sourceRevision?: string; fieldSources?: Record<string, FieldSource>; conflicts?: Record<string, FieldConflict> };
export type RequiredFieldsResponse = FieldEvidence & { vacancyId: string; revision: string; fields: VacancyFields; options?: FieldOptions };
export type CandidateFieldsResponse = FieldEvidence & { candidateId: string; revision: string; fields: CandidateFields; options?: FieldOptions };
export type Importance = 0 | 1 | 2 | 3 | 'insufficient_data';
export type VacancyParameter = {
  parameter_id: string; title: string;
  levels: { level_id: string; label: string; description: string }[];
  required_level_id: string | null; importance: Importance; status: 'ready' | 'irrelevant' | 'needs_review';
  raw_answers: { level: unknown; importance: unknown };
};
export type VacancyProfile = {
  contract_version: 'vacancy-level-importance-v1'; profile_id: string; run_id: string;
  vacancy_id: string; source_revision: string; vacancy_sha256: string;
  rubric_id: string; rubric_version: string; rubric_sha256: string; importance_version: string;
  model: string; parameters: VacancyParameter[]; usage: Record<string, number>;
};
export type ProfileEnvelope = {
  vacancyId: string; revision: string; state: string; stale: boolean; staleReasons: string[];
  canRun: boolean; runId: string | null; profile: VacancyProfile | null; requiredFields: RequiredFieldsResponse;
};
export type RequiredCheck = { field: string; status: 'pass' | 'fail' | 'unknown'; reason: string };
export type MatchParameter = {
  parameter_id: string; title?: string; excluded: boolean; exclusion_reason: string | null; weight?: number;
  required_level_id?: string; required_position?: number; m?: number;
  levels?: { level_id: string; position: number; p_raw: number; q: number | null; u: number | null; contribution: number | null }[];
};
export type RecommendationItem = {
  candidate_id: string; eligibility: 'pass' | 'fail' | 'unknown'; required_checks: RequiredCheck[];
  score: number | null; status: 'scored' | 'unscored' | 'filtered'; rank: number | null;
  parameters: MatchParameter[];
  coverage: { total_parameters: number; included_parameters: number; excluded_parameters: number; included_weight: number; excluded_weight: number; total_weight: number };
};
export type RecommendationResult = {
  contract_version: 'recommendations-v1'; recommendation_id: string; vacancy_id: string;
  vacancy_profile_id: string; rubric_id: string; rubric_version: string; rubric_sha256: string;
  policy: { version: string; k: number; probability_mode: string }; items: RecommendationItem[];
};
export type InvitationGate = { candidateId: string; canInvite: boolean; reason: string | null; channels: ('email' | 'telegram')[] };
export type RecommendationsEnvelope = {
  vacancyId: string; revision: string; state: string; stale: boolean; staleReasons: string[]; canRun: boolean;
  recommendationId: string | null; result: RecommendationResult | null; candidateIds: string[];
  pendingCandidates: { candidateId: string; state: string }[]; invitationEligibility: InvitationGate[];
};
export type PublishReceipt = { added: string[]; waitingProfile: string[]; proposed: string[]; pendingVerification?: string[] };
export const matchingLabels: Record<string, string> = {
  not_started: 'Ещё не рассчитано', missing: 'Ещё не рассчитано', queued: 'В очереди', sent: 'Считается',
  processing: 'Считается', running: 'Считается', response_saved: 'Сохраняется результат', ready: 'Готово',
  completed: 'Готово', needs_review: 'Нужно уточнить требования', stale: 'Результат устарел',
  failed: 'Ошибка расчёта', error: 'Ошибка расчёта', unavailable: 'Не удалось проверить',
  outcome_unknown: 'Исход расчёта неизвестен', dispatch_unknown: 'Проверьте запуск',
  waiting_sources: 'Ждёт материалы', waiting_assessment: 'Ожидает оценки', unscored: 'Недостаточно данных',
};
export const indexLabel = (value: number | null) => value === null || !Number.isFinite(value) ? '—'
  : new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 3, signDisplay: 'exceptZero' }).format(value);
export const percentLabel = (value: number | null) => value === null ? '—' : new Intl.NumberFormat('ru-RU', { style: 'percent', maximumFractionDigits: 1 }).format(value);
