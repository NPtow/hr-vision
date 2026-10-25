import { useId, useState } from 'react';
import { ArrowUp } from 'lucide-react';
import { motion, useReducedMotion } from 'motion/react';

export type Verdict = 'yes' | 'no' | 'unsure';
export type MeetingComment = { id: string; author: string; text: string; seconds: number; createdAt: string };
export type CompletedPanelProps = {
  comments: MeetingComment[];
  onAddComment: (text: string) => void;
  note: string;
  onNoteChange: (value: string) => void;
  verdict: Verdict | null;
  onVerdictChange: (value: Verdict) => void;
  currentTime: number;
  onSeek: (seconds: number) => void;
  feedback: string;
  onFeedbackChange: (value: string) => void;
};
export const momentLabel = (seconds: number) => `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`;

export function FeedbackChoice({ value, onChange }: { value: Verdict | null; onChange: (value: Verdict) => void }) {
  const id = useId();
  const reduce = useReducedMotion();
  return <div className="cm-verdict" role="group" aria-label="Подходит для вашей задачи?">
    {([{ id: 'yes', label: 'Скорее да' }, { id: 'no', label: 'Скорее нет' }, { id: 'unsure', label: 'Не могу сказать' }] as const).map(option =>
      <button key={option.id} type="button" aria-pressed={value === option.id} onClick={() => onChange(option.id)}>
        {value === option.id && <motion.span className="cm-verdict-selected" layoutId={`verdict-${id}`} transition={reduce ? { duration: 0 } : { type: 'spring', bounce: 0, duration: .25 }}/>}<span>{option.label}</span>
      </button>)}
  </div>;
}

export function MeetingCommentComposer({ currentTime, onAddComment }: Pick<CompletedPanelProps, 'currentTime' | 'onAddComment'>) {
  const [value, setValue] = useState('');
  return <form className="cm-composer" onSubmit={event => { event.preventDefault(); if (!value.trim()) return; onAddComment(value.trim()); setValue(''); }}>
    <span className="cm-compose-time" title="Комментарий к текущему моменту записи">{momentLabel(currentTime)}</span>
    <input aria-label="Комментарий к встрече" placeholder="Добавить комментарий…" value={value} onChange={event => setValue(event.target.value)} maxLength={2000}/>
    <button type="submit" aria-label="Добавить комментарий" disabled={!value.trim()}><ArrowUp size={18}/></button>
  </form>;
}

export function MeetingCommentList({ comments, onSeek }: Pick<CompletedPanelProps, 'comments' | 'onSeek'>) {
  return <div className="cm-comments" aria-label="Комментарии встречи" aria-live="polite">
    {comments.map(comment => <article className="cm-comment" key={comment.id}>
      <button type="button" className="cm-time" aria-label={`Перейти к ${momentLabel(comment.seconds)}`} onClick={() => onSeek(comment.seconds)}>{momentLabel(comment.seconds)}</button>
      <div><span className="cm-comment-author">{comment.author}</span><p>{comment.text}</p></div>
    </article>)}
  </div>;
}

export function PrivateMeetingNote({ note, onNoteChange }: Pick<CompletedPanelProps, 'note' | 'onNoteChange'>) {
  return <textarea className="cm-private-note" aria-label="Личная заметка" placeholder="Что важно запомнить для себя…" value={note} onChange={event => onNoteChange(event.target.value)} maxLength={5000}/>;
}
