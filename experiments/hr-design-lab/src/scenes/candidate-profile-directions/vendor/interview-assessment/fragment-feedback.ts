import type { AssessmentCriterion, AssessmentEvidence, FeedbackTarget, FragmentFeedback, FragmentOpinion } from './types';

export type FeedbackBlock = FeedbackTarget & {
  key: string;
  criterion: AssessmentCriterion;
  evidence: AssessmentEvidence;
  start: number | null;
  end: number | null;
};
export type FeedbackPlayback = {
  time: number;
  playing: boolean;
  active: FeedbackTarget | null;
  preferredCriterionId: string | null;
  commentLock: FeedbackTarget | null;
  explicitSource: boolean;
};
export type FeedbackEvent =
  | { type: 'media'; time: number; playing: boolean; cause: 'tick' | 'play' | 'pause' | 'seek' | 'end' }
  | { type: 'source'; target: FeedbackTarget; time: number | null }
  | { type: 'prefer'; criterionId: string }
  | { type: 'comment-open' }
  | { type: 'comment-close' };

export const initialFeedbackPlayback: FeedbackPlayback = {
  time: 0, playing: false, active: null, preferredCriterionId: null, commentLock: null, explicitSource: false,
};
export const targetKey = (target: FeedbackTarget) => JSON.stringify([target.criterionId, target.evidenceId]);
export const sameTarget = (a: FeedbackTarget | null, b: FeedbackTarget) => !!a && a.criterionId === b.criterionId && a.evidenceId === b.evidenceId;
const targetOf = (block: FeedbackBlock | undefined): FeedbackTarget | null => block ? { criterionId: block.criterionId, evidenceId: block.evidenceId } : null;

/** Only supplied, valid bounds are used; missing timing never becomes an invented interval. */
export function feedbackBlocks(criteria: AssessmentCriterion[], recordingId?: string): FeedbackBlock[] {
  const linked = criteria.some(criterion => criterion.evidence.some(evidence => !!evidence.recordingId));
  return criteria.flatMap(criterion => criterion.evidence.filter(evidence => evidence.feedbackQuestion?.trim() &&
    (recordingId === undefined || evidence.recordingId === recordingId || !linked && !evidence.recordingId)).map(evidence => {
    const start = evidence.sourceKind !== 'resume' && evidence.availability !== 'unavailable' && evidence.availability !== 'text_only'
      && typeof evidence.seconds === 'number' && Number.isFinite(evidence.seconds) && evidence.seconds >= 0 ? evidence.seconds : null;
    const end = start !== null && typeof evidence.endSeconds === 'number' && Number.isFinite(evidence.endSeconds) && evidence.endSeconds > start ? evidence.endSeconds : null;
    const target = { criterionId: criterion.id, evidenceId: evidence.id };
    return { ...target, key: targetKey(target), criterion, evidence, start, end };
  }));
}

export function blocksAtTime(blocks: FeedbackBlock[], time: number): FeedbackBlock[] {
  return Number.isFinite(time) ? blocks.filter(block => block.start !== null && block.end !== null && time >= block.start && time < block.end) : [];
}

function activeAtTime(blocks: FeedbackBlock[], state: FeedbackPlayback, time: number) {
  const available = blocksAtTime(blocks, time);
  const preferred = available.filter(block => block.criterionId === state.preferredCriterionId);
  return targetOf(preferred.find(block => sameTarget(state.active, block)) ?? preferred[0]
    ?? available.find(block => sameTarget(state.active, block)) ?? available[0]);
}

/** Transcript scrolling is not an event. The canonical player's time controls the question. */
export function reduceFeedbackPlayback(state: FeedbackPlayback, event: FeedbackEvent, blocks: FeedbackBlock[]): FeedbackPlayback {
  if (event.type === 'comment-open') return { ...state, commentLock: state.commentLock ?? state.active };
  if (event.type === 'comment-close') return { ...state, commentLock: null };
  if (event.type === 'source') {
    if (!blocks.some(block => sameTarget(event.target, block))) return { ...state, active: null, explicitSource: false };
    return { ...state, time: event.time ?? state.time, active: event.target, preferredCriterionId: event.target.criterionId, explicitSource: true };
  }
  if (event.type === 'prefer') {
    const next = { ...state, preferredCriterionId: event.criterionId, explicitSource: false };
    return { ...next, active: activeAtTime(blocks, next, state.time) };
  }
  if (!Number.isFinite(event.time)) return state;
  // A source click is useful while paused, including a source without an exact end.
  const retainSource = state.explicitSource && !event.playing && event.time === state.time && (event.cause === 'tick' || event.cause === 'pause');
  const resolve = event.playing || event.cause === 'seek' || event.cause === 'play' || event.cause === 'pause' || !!state.active;
  return {
    ...state, time: event.time, playing: event.playing, explicitSource: retainSource,
    active: event.cause === 'end' ? null : retainSource ? state.active : resolve ? activeAtTime(blocks, state, event.time) : null,
  };
}

export function visibleFeedbackBlock(state: FeedbackPlayback, blocks: FeedbackBlock[]) {
  const target = state.commentLock ?? state.active;
  return target ? blocks.find(block => sameTarget(target, block)) ?? null : null;
}

export type FeedbackDraft = {
  saved: FragmentFeedback;
  opinion: FragmentOpinion;
  status: 'idle' | 'saving' | 'saved' | 'error' | 'conflict';
  error?: string;
};
export const sameOpinion = (a: FragmentOpinion, b: FragmentOpinion) => a.answer === b.answer && a.comment === b.comment;
export const emptyFeedback = (target: FeedbackTarget): FragmentFeedback => ({ ...target, comment: '', version: 0 });
export const initialDraft = (saved: FragmentFeedback): FeedbackDraft => ({ saved, opinion: { answer: saved.answer, comment: saved.comment }, status: 'idle' });

export function validFeedback(value: unknown, target: FeedbackTarget): value is FragmentFeedback {
  if (!value || typeof value !== 'object') return false;
  const row = value as FragmentFeedback;
  return sameTarget(row, target) && Number.isInteger(row.version) && row.version >= 0 && typeof row.comment === 'string'
    && (row.answer === undefined || row.answer === 'yes' || row.answer === 'no' || row.answer === 'unknown');
}

export function feedbackConflict(error: unknown, target: FeedbackTarget): FragmentFeedback | null {
  if (!error || typeof error !== 'object') return null;
  const cause = error as { status?: number; current?: unknown };
  return cause.status === 409 && validFeedback(cause.current, target) ? cause.current : null;
}

/** A response confirms only its submitted snapshot; newer typing stays a draft. */
export function applyFeedbackSuccess(draft: FeedbackDraft, submitted: FragmentOpinion, saved: FragmentFeedback): FeedbackDraft {
  if (saved.version <= draft.saved.version) return { ...draft, status: 'error', error: 'Сервер не подтвердил новую версию. Повторите сохранение.' };
  const unchanged = sameOpinion(draft.opinion, submitted);
  return { saved, opinion: unchanged ? { answer: saved.answer, comment: saved.comment } : draft.opinion, status: unchanged ? 'saved' : 'idle' };
}

export function applyRemoteFeedback(draft: FeedbackDraft, saved: FragmentFeedback): FeedbackDraft {
  if (saved.version <= draft.saved.version || draft.status === 'saving') return draft;
  if (sameOpinion(draft.opinion, draft.saved)) return initialDraft(saved);
  return { ...draft, saved, status: 'conflict', error: 'Ответ изменён в другом окне. Ваш черновик сохранён здесь.' };
}
