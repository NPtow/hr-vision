import type { HRInterview } from './matching-contract';
import { hrRequest } from '../hh-outreach/api';
import type { AssessmentPresentation } from '../interview-assessment/types';
import type { FullCorpusRun } from '../interview-assessment/FullCorpusAssessment';

export type WorkflowState = { state: string; origin?: string | null; revisionId?: string | null };
export type Workflow = { interview: WorkflowState; resume: WorkflowState; assessment: WorkflowState; match: WorkflowState };
export type Readiness = {
  candidateId: string;
  hrInterview?: HRInterview;
  materials: { state: string; recordings: number | null; transcripts: number | null;
    items: { id: string; kind: string; recordings: number; transcripts: number; transcriptState: string }[] };
  assessment: { state: string; result: Omit<AssessmentPresentation, 'saveFeedback'> | null; match?: WorkflowState;
    /** Reserved for the authorised new-core adapter; never a browser service-token call. */
    coreRun?: FullCorpusRun; canRun?: boolean;
    rubric?: { id: string; version: string; parameterCount: number };
    corpus?: { sources: number; units: number; characters: number } };
};

export type Criterion = { criterion_id: string; state: string; level: number | null; failed_processing: boolean; missing_reason?: string | null; reason_codes?: string[]; citations?: unknown[] };
export type Assessment = {
  id: string; createdAt: string; model: string; jobId: string;
  counts: Record<string, number>; stale: boolean; recommendation: string; reason: string;
  criteria: Criterion[]; limitations: { code: string; subject: string }[]; documentHash: string;
};
export type SelectionPerson = {
  id: string; name: string; desiredRole: string; city: string; about: string; email: string;
  revision: string; sourceRevision: string; accountActive: boolean; searching: boolean;
  emailConnected: boolean; telegramConnected: boolean;
  consentReuse: boolean; interviewId: string | null; interviewStatus: string | null;
  resumeAvailable: boolean; inCohort: boolean; canAdd: boolean;
  assessment: Assessment | null; application: { id: string; status: string } | null;
  nomination: { id: string; state: string; error?: string | null; retryAt?: number; attempts?: number } | null; deliveries: { id: string; event: string; channel: string; status: string; error: string | null }[];
  workflow?: Workflow;
  hrInterview?: HRInterview;
};
export type SelectionVacancy = { id: string; title: string; company: string; city: string; salary: string; description: string; status?: string; revision: number; generation: string; workspaceId: string; contentHash: string };
export type Directory = { candidates: SelectionPerson[]; count: number };
export const selectionRequest = <T,>(path: string, token: string, body?: unknown) => hrRequest<T>('selection/' + path, token, body);

export const verdicts: Record<string, string> = { ranked: 'Рекомендует', not_matched: 'Не сравнивали', needs_assessment: 'Нужна оценка', non_matching: 'Не соответствует', conflicting: 'Противоречия', failed_processing: 'Ошибка обработки', stale: 'Результат устарел' };
export const states: Record<string, string> = { supported: 'Есть оценка', unknown: 'Недостаточно данных', not_applicable: 'Не применимо', conflict: 'Противоречие' };
export const reasons: Record<string, string> = {
  outside_anchor_scope: 'За пределами утверждённых шкал', aggregation_rule_not_approved: 'Правило объединения ещё не утверждено',
  self_report_lacks_period: 'В ответе не указан период', axis_not_applicable: 'Критерий не применим',
  basis_axis_unknown: 'Недостаточно данных по обязательным критериям',
};

// Display names from approved criteria-2026-10-v1; no scoring logic here.
export const criterionNames: Record<string, string> = {
  "A01": "Деловой диалог и слушание",
  "A02": "Поиск клиентов и выход на ЛПР",
  "A03": "Квалификация возможности",
  "A04": "Диагностика потребности и бизнеса клиента",
  "A05": "Презентация ценности под выявленную потребность",
  "A06": "Работа с сомнениями и переговоры",
  "A06.1": "Работа с сомнениями и возражениями",
  "A06.2": "Переговоры об условиях и цене",
  "A07": "Продвижение к согласованному следующему шагу",
  "A08": "Организация воронки и выполнение договорённостей",
  "A09": "Работа с показателями и личный вклад",
  "A10": "Знание продукта, рынка и продаж; корректность обещаний",
  "A11": "Освоение нового и корректировка подхода",
  "A11.1": "Адаптация подхода в ходе разговора",
  "A11.2": "Обучение на обратной связи и результатах",
  "A12": "Организация сложной сделки",
  "A13": "Развитие действующих клиентов",
  "M01": "Подбор и ввод в работу продавцов",
  "M02": "Система контроля команды и дисциплина CRM",
  "M03": "Построение системы продаж, воронки и аналитики",
  "M04": "Развитие сотрудников и разговоры о результатах",
  "M05": "Планирование и атрибуция результатов команды",
  "NEW-01": "Подготовка к контакту и передача лида",
  "NEW-02": "Объём контактов и скорость реакции",
  "C02": "Структурный анализ задачи",
  "C03": "Планирование, приоритеты и доведение до результата",
  "C06": "Согласование с участниками работы",
  "C07": "Точность утверждений и границ знания",
  "C08": "Убеждение и согласование условий"
};
