import { Tooltip } from '../radix';
import type { InterviewAnswer } from './interview-assistant-api';
import { interviewAnswerParts } from './interview-answer-model';
import { interviewTime } from './candidate-player-model';

export function InterviewChatAnswer({ answer, seek, canSeek, portal }: {
  answer: InterviewAnswer; seek: (seconds: number) => void; canSeek: boolean; portal: HTMLElement | null;
}) {
  return <div className="iv-chat-answer-parts">{interviewAnswerParts(answer).map((part, index) =>
    <p className="iv-chat-answer" key={index}>{part.text}{part.sources.map(source =>
      <Tooltip.Root key={source.id}>
        <Tooltip.Trigger asChild><button className="iv-source-chip" type="button"
          aria-label={`Источник ${source.number}: ${interviewTime(source.startMs / 1000)}, ${source.speakerName}`}
          disabled={!canSeek} data-source-id={source.id} data-source-ms={source.startMs}
          onClick={() => seek(source.startMs / 1000)}>{source.number}</button></Tooltip.Trigger>
        <Tooltip.Portal container={portal}><Tooltip.Content className="iv-source-tooltip" side="top" sideOffset={8} collisionPadding={14}>
          <strong>{interviewTime(source.startMs / 1000)}{source.speakerName && ` · ${source.speakerName}`}</strong>
          <span>{source.quote}</span>
        </Tooltip.Content></Tooltip.Portal>
      </Tooltip.Root>)}</p>)}</div>;
}
