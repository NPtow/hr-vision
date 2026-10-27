import React, { useEffect, useState } from 'react';
import { motion, useMotionValue, useReducedMotion, useTransform } from 'motion/react';
import { ArrowLeft, Check, RotateCcw } from 'lucide-react';
import type { SceneControl } from '../site';
import { Reshuffle } from './FeedbackConcepts';
import { InterviewTour } from './InterviewTour';
import { reviewMatches } from './review-data';
import { useFrameProgressChange } from './useFrameProgressChange';
import './review-feedback.css';

const smooth = (n: number) => n * n * (3 - 2 * n);
const stageFor = (p: number) => p < .3 ? 'answer' : p < .8 ? 'reshuffle' : 'result';

function useReview(control: SceneControl) {
  const [choice, setChoice] = useState(0);
  const [stage, setStage] = useState(() => stageFor(control.progress.get()));
  const [showProfile, setShowProfile] = useState(false);
  const [compact, setCompact] = useState(() => matchMedia('(max-width:600px)').matches);
  const selected = reviewMatches[choice];
  const reduced = useReducedMotion();
  const detailProgress = useMotionValue(.4);
  useEffect(() => {
    const media = matchMedia('(max-width:600px)');
    const update = () => setCompact(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  useEffect(() => setStage(stageFor(control.progress.get())), [control.progress]);
  useFrameProgressChange(control.progress, value => {
    setStage(stageFor(value));
    if (value < .98) setShowProfile(false);
  });
  const progress = useTransform(() => reduced ? control.progress.get() < .3 ? 0 : control.progress.get() < .8 ? .55 : 1 : control.progress.get());
  const apply = () => { control.seek(reduced ? 1 : .24); if (!reduced) control.play(); };
  const choose = (index: number) => { setChoice(index); control.seek(.12); };
  const openProfile = () => { control.seek(1); detailProgress.set(.4); setShowProfile(true); };
  const back = () => { setShowProfile(false); control.seek(.95); };
  const retry = () => control.seek(.12);
  return { choice, stage, selected, compact, reduced, detailProgress, showProfile, progress, apply, choose, openProfile, back, retry };
}
type ReviewState = ReturnType<typeof useReview>;

function ReviewTitle({ stage }: { stage: string }) {
  if (stage === 'result') return <>Ваш ответ учтён.<br/><em>Вот кто подходит точнее.</em></>;
  if (stage === 'reshuffle') return <>Добавили ваше требование.<br/><em>Смотрим на кандидатов точнее.</em></>;
  return <>Посмотрели интервью.<br/><em>Что для вас важно?</em></>;
}

// Both chapters use the same product shell. Only the work area below the
// candidate changes: the task, identity and toolbar never fade to a blank page.
function ReviewContents({ review }: { review: ReviewState }) {
  const { progress, stage, selected, choice, compact, reduced, apply, choose, openProfile, retry } = review;
  const noteOpacity = useTransform(progress, [.26, .38], [1, 0]);
  const noteY = useTransform(progress, [.26, .38], [0, reduced ? 0 : -12]);
  const tokenOpacity = useTransform(progress, [.29, .39], [0, 1]);
  const tokenY = useTransform(progress, [.29, .39], [reduced ? 0 : 12, 0], { ease: smooth });
  const proofOpacity = useTransform(progress, [.78, .88], [0, 1]);
  return <div className="rr-stage" data-answer={selected.id} data-stage={stage}>
    <Reshuffle progress={progress} compact={compact} onApply={apply} showNote={false} match={selected} onOpen={openProfile}/>
    <motion.form className="rr-note" style={{ opacity: noteOpacity, y: noteY }} aria-hidden={stage !== 'answer'} inert={stage !== 'answer'} onSubmit={event => { event.preventDefault(); apply(); }}>
      <div className="rr-observation"><span>Из оценки Анны</span><strong>Битрикс24 — понадобится освоение</strong></div>
      <fieldset><legend>Что важно уточнить?</legend>{reviewMatches.map((answer, index) => <button type="button" key={answer.id} aria-pressed={index === choice} onClick={() => choose(index)}><span>{index === choice && <Check size={12}/>}</span>{answer.feedback}</button>)}</fieldset>
      <button className="rr-apply" type="submit">Уточнить подбор</button>
    </motion.form>
    <motion.div className="rr-criterion" style={{ opacity: tokenOpacity, y: tokenY }} aria-hidden={stage === 'answer'}><span>Теперь учитываем</span><strong>{selected.criterion}</strong></motion.div>
    <motion.div className="rr-evidence" style={{ opacity: proofOpacity }} aria-hidden={stage !== 'result'} inert={stage !== 'result'}>
      <span>Подтверждено в интервью · {selected.time}</span>
      <blockquote>«{selected.quote}»</blockquote>
      <button className="rr-proof-open" type="button" onClick={openProfile}>Открыть этот ответ</button>
    </motion.div>
    <button className="rr-retry" onClick={retry} disabled={stage === 'answer'}><RotateCcw size={13}/>Изменить требование</button>
  </div>;
}

function ReviewedProfile({ review, control }: { review: ReviewState; control: SceneControl }) {
  return <div className="rr-detail" data-result={review.selected.id}>
    <InterviewTour variant="focus" match={review.selected} control={{ progress: review.detailProgress, seek: value => { control.pause(); review.detailProgress.set(value); } }}/>
    <button className="rr-back" onClick={review.back}><ArrowLeft size={14}/>К подборке</button>
  </div>;
}

export function ReviewFeedback({ control }: { control: SceneControl }) {
  const review = useReview(control);
  const complete = useMotionValue(1);
  const tourProgress = useMotionValue(1);
  if (review.showProfile) return <ReviewedProfile review={review} control={control}/>;
  return <div className="review-reshuffle" data-answer={review.selected.id} data-stage={review.stage}>
    <InterviewTour control={{ progress: tourProgress, seek: () => {} }} variant="focus" continuation={{ progress: complete, active: true, content: <ReviewContents review={review}/>, title: <ReviewTitle stage={review.stage}/>, match: review.stage === 'result' ? review.selected : undefined }}/>
  </div>;
}

// One reversible clock controls the tour, handoff and feedback. The handoff
// starts while the CRM gap is still on screen; their opacities overlap.
export function InterviewReviewFlow({ control, variant }: { control: SceneControl; variant: 'focus' | 'closeup' }) {
  const [reviewing, setReviewing] = useState(() => control.progress.get() >= .6);
  const reduced = useReducedMotion();
  const tourProgress = useTransform(control.progress, [0, .6], [0, 1]);
  const reviewProgress = useTransform(control.progress, [.6, 1], [0, 1]);
  const handoff = useTransform(() => reduced ? control.progress.get() >= .6 ? 1 : 0 : smooth(Math.max(0, Math.min(1, (control.progress.get() - .58) / .045))));
  useFrameProgressChange(control.progress, p => setReviewing(p >= .6));
  const reviewControl = { ...control, progress: reviewProgress, seek: (value: number) => control.seek(.6 + Math.max(0, Math.min(1, value)) * .4) };
  const review = useReview(reviewControl);
  if (review.showProfile) return <ReviewedProfile review={review} control={control}/>;
  return <div className="interview-review-flow" data-part={reviewing ? 'feedback' : 'tour'}>
    <InterviewTour control={{ progress: tourProgress, seek: value => control.seek(value * .6) }} variant={variant} onFeedback={reviewing ? () => review.retry() : () => control.seek(.648)} continuation={{ progress: handoff, active: reviewing, content: <ReviewContents review={review}/>, title: <ReviewTitle stage={review.stage}/>, match: reviewing && review.stage === 'result' ? review.selected : undefined }}/>
  </div>;
}
