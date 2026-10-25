import { useEffect, useRef, useState } from 'react';
import type { AssessmentPresentation, FeedbackTarget, FragmentOpinion } from './types';
import { applyFeedbackSuccess, applyRemoteFeedback, emptyFeedback, feedbackConflict, initialDraft, targetKey, validFeedback, type FeedbackDraft } from './fragment-feedback';

/** Mounted once per revision. In-memory drafts never masquerade as server feedback. */
export function useFragmentFeedback(assessment: AssessmentPresentation) {
  const [entries, setEntries] = useState<Record<string, FeedbackDraft>>({});
  const values = useRef(entries);
  const latestFeedback = useRef(assessment.feedback);
  latestFeedback.current = assessment.feedback;
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const update = (key: string, value: FeedbackDraft) => {
    values.current = { ...values.current, [key]: value };
    if (mounted.current) setEntries(values.current);
  };
  const get = (target: FeedbackTarget) => values.current[targetKey(target)]
    ?? initialDraft(assessment.feedback.find(row => validFeedback(row, target)) ?? emptyFeedback(target));

  useEffect(() => {
    for (const saved of assessment.feedback) {
      if (!validFeedback(saved, saved)) continue;
      const key = targetKey(saved);
      const before = values.current[key];
      if (!before) continue;
      const next = applyRemoteFeedback(before, saved);
      if (next !== before) update(key, next);
    }
  }, [assessment.feedback]);

  return {
    get,
    edit(target: FeedbackTarget, opinion: FragmentOpinion) {
      const before = get(target);
      update(targetKey(target), { ...before, opinion, status: before.status === 'saving' || before.status === 'conflict' ? before.status : 'idle', error: before.status === 'conflict' ? before.error : undefined });
    },
    acceptRemote(target: FeedbackTarget) { update(targetKey(target), initialDraft(get(target).saved)); },
    async save(target: FeedbackTarget, opinion?: FragmentOpinion): Promise<boolean> {
      const key = targetKey(target);
      const before = get(target);
      if (before.status === 'saving') return false;
      const submitted = { ...(opinion ?? before.opinion) };
      update(key, { ...before, opinion: submitted, status: 'saving', error: undefined });
      try {
        const saved = await assessment.saveFeedback(target, submitted, before.saved.version);
        if (!mounted.current) return false;
        if (!validFeedback(saved, target)) throw new Error('Invalid feedback response');
        let next = applyFeedbackSuccess(get(target), submitted, saved);
        const remote = latestFeedback.current.find(row => validFeedback(row, target));
        if (remote) next = applyRemoteFeedback(next, remote);
        update(key, next);
        return next.status === 'saved';
      } catch (error) {
        if (!mounted.current) return false;
        const conflict = feedbackConflict(error, target);
        update(key, { ...get(target), ...(conflict ? { saved: conflict } : {}), status: conflict ? 'conflict' : 'error',
          error: conflict ? 'Ответ изменён в другом окне. Ваш черновик сохранён здесь.' : 'Не удалось сохранить. Ваш черновик сохранён здесь.' });
        return false;
      }
    },
  };
}
