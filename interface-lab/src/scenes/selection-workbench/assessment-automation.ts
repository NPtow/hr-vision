/** Public adapter states; automaticState is diagnostic and does not drive UI. */
export type AssessmentAutomation = {
  state: string; automaticState?: string; errorCode?: string | null;
  sourceRevision?: string | null; runId?: string | null; pollErrors?: number;
  updatedAt?: number | null; retryAt?: number | null; lastPollAt?: number | null;
};

export const automationLabels: Record<string, string> = {
  not_enrolled: 'Автообработка не включена', waiting_materials: 'Ожидает материалы',
  source_unavailable: 'Материалы недоступны', queued: 'В очереди', dispatching: 'Запускается',
  processing: 'Считается', processing_delayed: 'Ожидает результат',
  dependency_unavailable: 'Сервис временно недоступен', ready: 'Обработка завершена',
  error: 'Ошибка обработки', stale: 'Материалы изменились',
  dispatch_unknown: 'Запуск не подтверждён', outcome_unknown: 'Результат не подтверждён',
};
const waiting = new Set(['waiting_materials', 'source_unavailable', 'queued', 'dispatching', 'processing', 'processing_delayed', 'dependency_unavailable']);
const withoutResult = new Set(['not_started', 'waiting_sources', 'queued', 'processing', 'unknown']);
const terminalAssessment = new Set(['stale', 'error', 'expired', 'cancelled']);
type AssessmentState = { state: string; coreRun?: { status: string } };

export function assessmentStatus(state: string, automation?: AssessmentAutomation): string {
  // A known assessment remains authoritative. Automation only fills an absent result.
  return withoutResult.has(state) && automation
    ? 'automation:' + automation.state : state;
}
export const pendingAssessmentLabels = Object.fromEntries(Object.entries(automationLabels).map(([key, value]) => ['automation:' + key, value]));

export function shouldPollReadiness(value: { assessment: AssessmentState; automation?: AssessmentAutomation }): boolean {
  if (terminalAssessment.has(value.assessment.state)) return false;
  const core = value.assessment.coreRun;
  if (core) return ['queued', 'sent', 'response_saved'].includes(core.status)
    && !['dispatch_unknown', 'outcome_unknown'].includes(value.automation?.state ?? '');
  if (['ready', 'saved', 'insufficient_data'].includes(value.assessment.state)) return false;
  return value.automation ? waiting.has(value.automation.state) : ['queued', 'processing'].includes(value.assessment.state);
}

export function automationRetryLabel(retryAt?: number | null): string | null {
  if (typeof retryAt !== 'number' || !Number.isFinite(retryAt) || retryAt <= 0) return null;
  const date = new Date(retryAt * 1000);
  return Number.isNaN(date.getTime()) ? null : new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(date);
}
