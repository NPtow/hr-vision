import { useId, useState } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import { type CompletedPanelProps, FeedbackChoice, MeetingCommentComposer, MeetingCommentList, PrivateMeetingNote } from './completed-meeting-shared';

const tabs = [{ id: 'comments', label: 'Комментарии' }, { id: 'feedback', label: 'Фидбек' }, { id: 'notes', label: 'Мои заметки' }] as const;
export function CompletedWorkbench(props: CompletedPanelProps) {
  const [tab, setTab] = useState<'comments' | 'feedback' | 'notes'>('comments');
  const id = useId();
  const reduce = useReducedMotion();
  return <section className="cm-panel cm-workbench">
    <div className="cm-workbench-bar"><div className="cm-workbench-tabs" role="tablist" aria-label="Материалы встречи">
      {tabs.map((item, index) => <button key={item.id} id={`${id}-${item.id}-tab`} role="tab" type="button" aria-controls={`${id}-${item.id}`} aria-selected={tab === item.id} tabIndex={tab === item.id ? 0 : -1}
        onClick={() => setTab(item.id)} onKeyDown={event => {
          if (!['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault(); const next = tabs[event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (index + (event.key === 'ArrowRight' ? 1 : 2)) % 3];
          setTab(next.id); document.getElementById(`${id}-${next.id}-tab`)?.focus();
        }}>
        {tab === item.id && <motion.span className="cm-tab-active" layoutId={`workbench-${id}`} transition={reduce ? { duration: 0 } : { type: 'spring', bounce: 0, duration: .28 }}/>}<span>{item.label}</span>
      </button>)}
    </div><span className="cm-muted">{tab === 'notes' ? 'Только для вас' : 'Видно команде'}</span></div>
    <div className="cm-workbench-content">
      <div hidden={tab !== 'comments'} role="tabpanel" id={`${id}-comments`} aria-labelledby={`${id}-comments-tab`} className="cm-workbench-pane">
      <><MeetingCommentList comments={props.comments} onSeek={props.onSeek}/><MeetingCommentComposer currentTime={props.currentTime} onAddComment={props.onAddComment}/></>
      </div>
      <div hidden={tab !== 'feedback'} role="tabpanel" id={`${id}-feedback`} aria-labelledby={`${id}-feedback-tab`}>
      <div className="cm-workbench-feedback"><div className="cm-feedback-row"><h3 className="cm-section-heading">Подходит для вашей задачи?</h3><FeedbackChoice value={props.verdict} onChange={props.onVerdictChange}/></div><textarea className="cm-feedback-text" aria-label="Обоснование оценки" placeholder="На чём основана ваша оценка?" value={props.feedback} onChange={event => props.onFeedbackChange(event.target.value)} maxLength={5000}/></div>
      </div>
      <div hidden={tab !== 'notes'} role="tabpanel" id={`${id}-notes`} aria-labelledby={`${id}-notes-tab`}>
      <PrivateMeetingNote note={props.note} onNoteChange={props.onNoteChange}/>
      </div>
    </div>
  </section>;
}
