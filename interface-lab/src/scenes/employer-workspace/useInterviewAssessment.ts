import { useEffect, useRef, useState } from 'react';
import { request, RequestError } from '../connected-journey/api';
import { AssessmentFeedbackConflictError, type AssessmentPresentation, type FragmentFeedback } from '../interview-assessment/types';

type RecordingIdentity = { source: 'intake'; id: string; index: number; vacancyId: string }
  | { source: 'screening'; candidate: string; applicationId: string; vacancyId: string; generation: string; recordingId: string };
type Snapshot = Omit<AssessmentPresentation, 'saveFeedback'>;

/** Bind an immutable assessment to the same authorised recording as the player. */
export function useInterviewAssessment(token: string, identity: RecordingIdentity | null, materialIdentity = '') {
  const scope = JSON.stringify(identity);
  const context = JSON.stringify([token, identity, materialIdentity]);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ context: string; assessment?: AssessmentPresentation; error?: string }>();
  const currentContext = useRef(context);
  currentContext.current = context;
  useEffect(() => {
    const controller = new AbortController();
    const source = JSON.parse(scope) as RecordingIdentity | null;
    if (!source || !token) return;
    const commands = new Map<string, string>();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = () => request<{ assessment: Snapshot | null; status?: string; matchStatus?: string }>('employer/interview-assessment', token, source, controller.signal);
    const load = () => void read().then(({ assessment, status, matchStatus }) => {
      if (controller.signal.aborted || currentContext.current !== context) return;
      if (!assessment) { setResult({ context }); if (['pending','waiting','queued','running'].includes(status ?? '')) timer = setTimeout(load, 8_000); return; }
      if (['pending','waiting','queued','running'].includes(matchStatus ?? '')) timer = setTimeout(load, 8_000);
      if (source.source === 'screening') { setResult({ context, assessment: { ...assessment,
        saveFeedback: async () => { throw new Error('Оценка доступна только для просмотра.'); },
      } }); return; }
      setResult({ context, assessment: { ...assessment, saveFeedback: async (target, opinion, expectedVersion) => {
        if (controller.signal.aborted || currentContext.current !== context) throw new Error('Кандидат изменился. Откройте фрагмент ещё раз.');
        const command = { ...source, revisionId: assessment.revisionId, ...target, ...opinion, expectedVersion };
        const key = JSON.stringify(command);
        const requestId = commands.get(key) ?? crypto.randomUUID();
        commands.set(key, requestId);
        try {
          const saved = await request<{ feedback: FragmentFeedback }>('employer/interview-assessment/feedback', token, { ...command, requestId }, controller.signal);
          commands.delete(key);
          return saved.feedback;
        } catch (cause) {
          if (cause instanceof RequestError && cause.status === 409) {
            commands.delete(key);
            const latest = (await read()).assessment;
            if (!latest || latest.revisionId !== assessment.revisionId) throw new Error('Оценка или материалы изменились. Обновите страницу.');
            const current = latest.feedback.find(item => item.criterionId === target.criterionId && item.evidenceId === target.evidenceId);
            throw new AssessmentFeedbackConflictError(current ?? { ...target, comment: '', version: 0 });
          }
          throw cause;
        }
      } } });
    }).catch(cause => {
      if (!controller.signal.aborted && currentContext.current === context) setResult({ context, error: cause instanceof Error ? cause.message : 'Не удалось загрузить оценку.' });
    });
    load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [token, scope, context, attempt]);
  return { assessment: result?.context === context ? result.assessment : undefined,
    error: result?.context === context ? result.error : undefined, retry: () => setAttempt(value => value + 1) };
}
