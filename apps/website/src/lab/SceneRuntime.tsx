import React, { memo, useState } from 'react';
import { useTransform } from 'motion/react';
import { Intro, Needs, Connections, Pool, Interest, Interview, Calendar, Meeting, Feedback, Offer, Finish, StartDialog, initialNeed, candidates, type SceneControl } from '../site';
import { sceneById, type SceneId } from './scenes';
import { InterestConcept } from './InterestConcepts';
import type { InterestVariant } from './interest-variants';
import { InterviewReviewFlow, ReviewFeedback } from './ReviewFeedback';
import type { InterviewVariant } from './interview-variants';
import { FeedbackConcept } from './FeedbackConcepts';
import { FeedbackGraph } from './FeedbackGraph';
import type { FeedbackVariant } from './feedback-variants';
import { CtaConcept } from './CtaConcepts';
import type { CtaVariant } from './cta-variants';

export const SceneRuntime = memo(function SceneRuntime({ id, control, compact = false, onNavigate, interestVariant = 'original', interviewVariant = 'original', feedbackVariant = 'original', ctaVariant = 'perimeter' }: { id: SceneId; control: SceneControl; compact?: boolean; onNavigate: (id: SceneId) => void; interestVariant?: InterestVariant; interviewVariant?: InterviewVariant; feedbackVariant?: FeedbackVariant; ctaVariant?: CtaVariant }) {
  const definition = sceneById(id);
  const progress = useTransform(control.progress, [0, 1], [0, definition.end]);
  const [need, setNeed] = useState(initialNeed);
  const [person, setPerson] = useState(candidates[1]);
  const [dialog, setDialog] = useState(false);
  let content: React.ReactNode;
  switch(id) {
    case 'cta': content = <CtaConcept variant={ctaVariant} control={control} onStart={() => setDialog(true)}/>; break;
    case 'intro': content = <Intro progress={progress}/>; break;
    case 'needs': content = <Needs progress={progress} need={need} setNeed={setNeed} onNext={() => onNavigate('people')}/>; break;
    case 'connections': content = <Connections progress={progress} need={need}/>; break;
    case 'people': content = <Pool paused={!control.playing} control={control}/>; break;
    case 'interest': content = interestVariant === 'original' ? <Interest progress={progress}/> : <InterestConcept variant={interestVariant} progress={control.progress}/>; break;
    case 'interview': content = interviewVariant === 'original' ? <Interview control={control}/> : <InterviewReviewFlow control={control} variant={interviewVariant}/>; break;
    case 'review-feedback': content = <ReviewFeedback control={control}/>; break;
    case 'calendar': content = <Calendar progress={progress} control={control}/>; break;
    case 'meeting': content = <><Meeting progress={progress}/><button className="scene-feedback-next" onClick={() => onNavigate('feedback')}>Уточнить подбор после встречи →</button></>; break;
    case 'feedback': content = feedbackVariant === 'graph' ? <FeedbackGraph control={control} moment="meeting" onOtherMoment={() => onNavigate('interview')}/> : feedbackVariant === 'original' ? <Feedback progress={progress} onMatch={setPerson} control={control}/> : <FeedbackConcept variant={feedbackVariant} control={control}/>; break;
    case 'offer': content = <Offer progress={progress} person={person} control={control}/>; break;
    case 'finish': content = <Finish onStart={() => setDialog(true)}/>; break;
  }
  return <div className={`scene-canvas ${control.playing ? '' : 'motion-paused'}`} data-scene={id} data-compact={compact} onPointerDownCapture={control.pause} onKeyDownCapture={event => { if(!['Tab','Shift'].includes(event.key)) control.pause(); }}>
    <div className={`chapter ${id}-chapter`}><div className="chapter-sticky">{content}</div></div>
    <StartDialog open={dialog} setOpen={setDialog} need={need} setNeed={setNeed} onOpenProfile={()=>onNavigate('interview')}/>
  </div>;
});
