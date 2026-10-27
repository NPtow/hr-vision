import { useId, useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';
import { motion, useReducedMotion } from 'motion/react';
import {
  FeedbackChoice, MeetingCommentComposer, MeetingCommentList, PrivateMeetingNote,
  type CompletedPanelProps,
} from './completed-meeting-shared';
import './completed-outcome.css';

const feedbackPrompts = {
  yes: 'Что помогло принять решение?',
  no: 'Что не подошло для вашей задачи?',
  unsure: 'Что ещё нужно проверить?',
} as const;

/** Decision first; supporting observations stay in the document below the recording. */
export function CompletedOutcome(props: CompletedPanelProps) {
  const id = useId();
  const reduce = useReducedMotion();
  const [commentsOpen, setCommentsOpen] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const feedbackOpen = props.verdict !== null || !!props.feedback.trim();
  const transition = reduce ? { duration: 0 } : { duration: .24, ease: [0.22, 1, 0.36, 1] as const };

  return <section className="cm-panel cm-outcome" aria-labelledby={`${id}-heading`}>
    <div className="cm-outcome-decision">
      <div className="cm-outcome-heading">
        <h3 className="cm-section-heading" id={`${id}-heading`}>Как прошла встреча?</h3>
        <p className="cm-muted">Подходит для вашей задачи?</p>
      </div>
      <FeedbackChoice value={props.verdict} onChange={props.onVerdictChange}/>
    </div>

    <motion.div className="cm-outcome-reveal" initial={false}
      animate={{ height: feedbackOpen ? 'auto' : 0, opacity: feedbackOpen ? 1 : 0 }} transition={transition}
      aria-hidden={!feedbackOpen} inert={!feedbackOpen}>
      <div className="cm-outcome-feedback">
        <label htmlFor={`${id}-feedback`}>{props.verdict ? feedbackPrompts[props.verdict] : 'Ваш итог'}</label>
        <textarea id={`${id}-feedback`} value={props.feedback} maxLength={5000}
          placeholder="Пара слов о том, что повлияло на ваш ответ…"
          onChange={event => props.onFeedbackChange(event.target.value)} rows={2}/>
      </div>
    </motion.div>

    <div className="cm-outcome-dossier">
      <OutcomeDisclosure id={`${id}-comments`} label="Комментарии к записи" open={commentsOpen} onToggle={() => setCommentsOpen(open => !open)}>
        <MeetingCommentList comments={props.comments} onSeek={props.onSeek}/>
        <MeetingCommentComposer currentTime={props.currentTime} onAddComment={props.onAddComment}/>
      </OutcomeDisclosure>
      <OutcomeDisclosure id={`${id}-note`} label="Личная заметка" open={noteOpen} onToggle={() => setNoteOpen(open => !open)}>
        <PrivateMeetingNote note={props.note} onNoteChange={props.onNoteChange}/>
      </OutcomeDisclosure>
    </div>
  </section>;
}

function OutcomeDisclosure({ id, label, open, onToggle, children }: {
  id: string; label: string; open: boolean; onToggle: () => void; children: ReactNode;
}) {
  const reduce = useReducedMotion();
  const transition = reduce ? { duration: 0 } : { duration: .24, ease: [0.22, 1, 0.36, 1] as const };
  return <section className="cm-outcome-disclosure">
    <h4><button id={`${id}-trigger`} type="button" aria-expanded={open} aria-controls={id} onClick={onToggle}>
      <span>{label}</span>
      <motion.span className="cm-outcome-chevron" animate={{ rotate: open ? 180 : 0 }} transition={transition} aria-hidden="true"><ChevronDown size={16}/></motion.span>
    </button></h4>
    <motion.div id={id} className="cm-outcome-reveal" role="region" aria-labelledby={`${id}-trigger`}
      initial={false} animate={{ height: open ? 'auto' : 0, opacity: open ? 1 : 0 }} transition={transition}
      aria-hidden={!open} inert={!open}>
      <div className="cm-outcome-disclosure-body">{children}</div>
    </motion.div>
  </section>;
}
