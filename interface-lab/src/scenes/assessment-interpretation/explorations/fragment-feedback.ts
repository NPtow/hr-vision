import type { AssessmentCriterion, AssessmentEvidence } from '../assessment-view';

type TimedTurn = { id: number; seconds: number | null; endSeconds?: number };
export type FeedbackTarget = { criterionId: string; evidenceId: string };
export type FeedbackBlock = FeedbackTarget & {
  key: string;
  criterion: AssessmentCriterion;
  evidence: AssessmentEvidence;
  start: number | null;
  end: number | null;
};
export type FragmentOpinion = { answer?: 'yes' | 'no' | 'unknown'; comment: string };
export type FeedbackPlayback = {
  time: number;
  playing: boolean;
  active: FeedbackTarget | null;
  preferredCriterionId: string | null;
  commentLock: FeedbackTarget | null;
};
export type FeedbackEvent =
  | { type: 'media'; time: number; playing: boolean; cause: 'tick' | 'play' | 'pause' | 'seek' | 'end' }
  | { type: 'source'; target: FeedbackTarget; time: number | null }
  | { type: 'prefer'; criterionId: string }
  | { type: 'comment-open' }
  | { type: 'comment-close' };

export const initialFeedbackPlayback: FeedbackPlayback = { time: 0, playing: false, active: null, preferredCriterionId: null, commentLock: null };
export const targetKey = (target: FeedbackTarget) => JSON.stringify([target.criterionId, target.evidenceId]);
export const opinionKey = (candidateId: string, revisionId: string, target: FeedbackTarget) =>
  `hrv-fragment-feedback:v1:${JSON.stringify([candidateId, revisionId, target.criterionId, target.evidenceId])}`;

export function feedbackBlocks(criteria: AssessmentCriterion[], turns: TimedTurn[]): FeedbackBlock[] {
  const byId = new Map(turns.map(turn => [turn.id, turn]));
  return criteria.flatMap(criterion => criterion.evidence.map(evidence => {
    const sourceTurns = (evidence.turnIds ?? []).map(id => byId.get(id)).filter((turn): turn is TimedTurn => !!turn);
    const validStart = typeof evidence.seconds === 'number' && Number.isFinite(evidence.seconds) && evidence.seconds >= 0;
    const start = evidence.sourceKind === 'resume' ? null : validStart ? evidence.seconds : null;
    const explicitEnd = typeof evidence.endSeconds === 'number' && Number.isFinite(evidence.endSeconds) ? evidence.endSeconds : null;
    const ends = sourceTurns.flatMap(turn => typeof turn.endSeconds === 'number' && Number.isFinite(turn.endSeconds) ? [turn.endSeconds] : []);
    const end = start === null ? null : explicitEnd !== null && explicitEnd > start ? explicitEnd : ends.length && Math.max(...ends) > start ? Math.max(...ends) : null;
    const target = { criterionId: criterion.id, evidenceId: evidence.id };
    return { ...target, key: targetKey(target), criterion, evidence, start, end };
  }));
}

export function blocksAtTime(blocks: FeedbackBlock[], time: number): FeedbackBlock[] {
  if (!Number.isFinite(time)) return [];
  return blocks.filter(block => block.start !== null && block.end !== null && time >= block.start && time < block.end);
}

const sameTarget = (a: FeedbackTarget | null, b: FeedbackTarget) => !!a && a.criterionId === b.criterionId && a.evidenceId === b.evidenceId;
const targetOf = (block: FeedbackBlock | undefined): FeedbackTarget | null => block ? { criterionId: block.criterionId, evidenceId: block.evidenceId } : null;

function activeAtTime(blocks: FeedbackBlock[], state: FeedbackPlayback, time: number) {
  const available = blocksAtTime(blocks, time);
  const preferred = available.filter(block => block.criterionId === state.preferredCriterionId);
  return targetOf(preferred.find(block => sameTarget(state.active, block)) ?? preferred[0]
    ?? available.find(block => sameTarget(state.active, block)) ?? available[0]);
}

/** Playback controls the question; reading/scrolling the transcript is deliberately not an event. */
export function reduceFeedbackPlayback(state: FeedbackPlayback, event: FeedbackEvent, blocks: FeedbackBlock[]): FeedbackPlayback {
  if (event.type === 'comment-open') return { ...state, commentLock: state.commentLock ?? state.active };
  if (event.type === 'comment-close') return { ...state, commentLock: null };
  if (event.type === 'source') {
    if (!blocks.some(block => sameTarget(event.target, block))) return state;
    return { ...state, time: event.time ?? state.time, active: event.target, preferredCriterionId: event.target.criterionId };
  }
  if (event.type === 'prefer') {
    const next = { ...state, preferredCriterionId: event.criterionId };
    return { ...next, active: activeAtTime(blocks, next, state.time) };
  }
  if (!Number.isFinite(event.time)) return state;
  // Loading metadata or an idle clock at zero must not ask an unsolicited question.
  const shouldResolve = event.playing || event.cause === 'seek' || event.cause === 'play' || event.cause === 'pause' || !!state.active;
  return {
    ...state, time: event.time, playing: event.playing,
    active: event.cause === 'end' ? null : shouldResolve ? activeAtTime(blocks, state, event.time) : null,
  };
}

export function visibleFeedbackBlock(state: FeedbackPlayback, blocks: FeedbackBlock[]) {
  const target = state.commentLock ?? state.active;
  return target ? blocks.find(block => sameTarget(target, block)) ?? null : null;
}

export function readOpinion(value: string | null): FragmentOpinion | null {
  if (!value) return null;
  try {
    const parsed: unknown = JSON.parse(value);
    if (!parsed || typeof parsed !== 'object') return null;
    const candidate = parsed as Record<string, unknown>;
    const answer = candidate.answer === 'yes' || candidate.answer === 'no' || candidate.answer === 'unknown' ? candidate.answer : undefined;
    return { answer, comment: typeof candidate.comment === 'string' ? candidate.comment : '' };
  } catch { return null; }
}
