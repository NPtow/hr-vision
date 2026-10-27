import { useId } from 'react';
import {
  type CompletedPanelProps,
  FeedbackChoice,
  MeetingCommentComposer,
  MeetingCommentList,
  PrivateMeetingNote,
} from './completed-meeting-shared';
import './completed-journal.css';

/** A shared conversation and a private margin, followed by the employer's judgment. */
export function CompletedJournal(props: CompletedPanelProps) {
  const id = useId();

  return <div className="cm-panel ecj-journal">
    <div className="ecj-journal__columns">
      <section className="ecj-journal__section" aria-labelledby={`${id}-comments`}>
        <header className="ecj-journal__heading">
          <h3 className="cm-section-heading" id={`${id}-comments`}>Комментарии</h3>
          <p className="cm-muted">Видны команде</p>
        </header>
        <div className="ecj-journal__discussion">
          <MeetingCommentList comments={props.comments} onSeek={props.onSeek}/>
          <MeetingCommentComposer currentTime={props.currentTime} onAddComment={props.onAddComment}/>
        </div>
      </section>

      <section className="ecj-journal__section" aria-labelledby={`${id}-note`}>
        <header className="ecj-journal__heading">
          <h3 className="cm-section-heading" id={`${id}-note`}>Личная заметка</h3>
          <p className="cm-muted">Только для вас</p>
        </header>
        <PrivateMeetingNote note={props.note} onNoteChange={props.onNoteChange}/>
      </section>
    </div>

    <section className="ecj-journal__verdict" aria-labelledby={`${id}-verdict`}>
      <div className="ecj-journal__verdict-row">
        <h3 className="cm-section-heading" id={`${id}-verdict`}>Подходит для вашей задачи?</h3>
        <FeedbackChoice value={props.verdict} onChange={props.onVerdictChange}/>
      </div>
      <input
        className="ecj-journal__reason"
        aria-label="Почему кандидат подходит или не подходит"
        placeholder="Что повлияло на ваше мнение?"
        value={props.feedback}
        onChange={event => props.onFeedbackChange(event.target.value)}
        maxLength={2000}
      />
    </section>
  </div>;
}
