import React, { useRef } from 'react';
import { InterviewReviewFlow } from './lab/ReviewFeedback';
import { useChapterControl } from './useChapterControl';
import { useSceneFrame } from './useSceneFrame';
import './interview-chapter.css';

export function InterviewChapter({ paused = false }: { paused?: boolean }) {
  const chapter = useRef<HTMLElement>(null);
  const space = useRef<HTMLDivElement>(null);
  const control = useChapterControl(chapter, 18000, paused);
  const frame = useSceneFrame(space);
  return <section ref={chapter} id="interview" className="site-interview" aria-label="Интервью, причины выбора и ваш фидбэк">
    <div className="site-interview-sticky">
      <div className="site-interview-space" ref={space}>
        <div className="site-interview-frame" style={frame}>
          <InterviewReviewFlow control={control} variant="focus"/>
        </div>
      </div>
    </div>
  </section>;
}
