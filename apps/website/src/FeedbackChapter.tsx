import React, { useRef } from 'react';
import { FeedbackGraph } from './lab/FeedbackGraph';
import { useChapterControl } from './useChapterControl';
import { useSceneFrame } from './useSceneFrame';
import type { Candidate } from './candidate-data';
import './feedback-chapter.css';

export function FeedbackChapter({ onMatch, paused = false }: { onMatch: (person: Candidate) => void; paused?: boolean }) {
  const chapter = useRef<HTMLElement>(null);
  const space = useRef<HTMLDivElement>(null);
  const control = useChapterControl(chapter, 10000, paused);
  const frame = useSceneFrame(space, 1224, 666);
  const overview = () => {
    control.pause();
    document.getElementById('interview')?.scrollIntoView({ behavior: 'instant', block: 'start' });
  };
  return <section ref={chapter} id="feedback" className="site-feedback" aria-label="Как ответ после встречи меняет подбор">
    <div className="site-feedback-sticky">
      <div ref={space} className="site-feedback-space">
        <div className="site-feedback-frame" style={frame}>
          <div className="site-feedback-viewport" data-graph-viewport>
            <FeedbackGraph control={control} moment="meeting" onOtherMoment={overview} onMatch={onMatch}/>
          </div>
        </div>
      </div>
    </div>
  </section>;
}
