import { useEffect, useMemo, useState } from 'react';
import type { AssessmentEvidence, AssessmentPresentation, FeedbackTarget } from './types';
import { blocksAtTime, feedbackBlocks, initialFeedbackPlayback, reduceFeedbackPlayback, visibleFeedbackBlock, type FeedbackEvent, type FeedbackPlayback } from './fragment-feedback';

export function useInterviewAssessment(assessment: AssessmentPresentation | undefined, current: number, playing: boolean, recordingId?: string) {
  const revision = JSON.stringify([assessment?.revisionId ?? '', recordingId ?? '']);
  const blocks = useMemo(() => feedbackBlocks(assessment?.criteria ?? [], recordingId), [assessment?.criteria, recordingId]);
  const [state, setState] = useState<{ revision: string; playback: FeedbackPlayback }>({ revision, playback: initialFeedbackPlayback });
  const [selection, setSelection] = useState({ revision, id: '' });
  const playback = state.revision === revision ? state.playback : initialFeedbackPlayback;
  const selectable = useMemo(() => {
    const criteria = assessment?.criteria ?? [];
    const evidenced = criteria.filter(criterion => criterion.evidence.length > 0);
    return evidenced.length ? evidenced : criteria.filter(criterion => criterion.state !== 'not_applicable');
  }, [assessment?.criteria]);
  const selectedId = selection.revision === revision && selectable.some(criterion => criterion.id === selection.id)
    ? selection.id : selectable[0]?.id ?? '';

  function dispatch(event: FeedbackEvent) {
    setState(previous => ({ revision, playback: reduceFeedbackPlayback(previous.revision === revision ? previous.playback : initialFeedbackPlayback, event, blocks) }));
  }
  useEffect(() => {
    setState(previous => {
      const before = previous.revision === revision ? previous.playback : initialFeedbackPlayback;
      const cause = before.playing !== playing ? playing ? 'play' : 'pause' : 'tick';
      return { revision, playback: reduceFeedbackPlayback(before, { type: 'media', time: current, playing, cause }, blocks) };
    });
  }, [current, playing, revision, blocks]);

  return {
    selectedId, playback, blocks,
    visible: visibleFeedbackBlock(playback, blocks),
    overlapping: blocksAtTime(blocks, current),
    selectCriterion(id: string) { setSelection({ revision, id }); dispatch({ type: 'prefer', criterionId: id }); },
    source(evidence: AssessmentEvidence, criterionId: string) {
      setSelection({ revision, id: criterionId });
      dispatch({ type: 'source', target: { criterionId, evidenceId: evidence.id }, time: evidence.seconds });
    },
    selectQuestion(target: FeedbackTarget) { dispatch({ type: 'source', target, time: current }); },
    seek(time: number) { dispatch({ type: 'media', time, playing, cause: 'seek' }); },
    end(time: number) { dispatch({ type: 'media', time, playing: false, cause: 'end' }); },
    openComment() { dispatch({ type: 'comment-open' }); },
    closeComment() { dispatch({ type: 'comment-close' }); },
  };
}
