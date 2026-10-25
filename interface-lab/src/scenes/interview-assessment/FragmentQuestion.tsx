import { MessageSquare, ChevronDown } from 'lucide-react';
import { useId, type ReactNode } from 'react';
import type { AssessmentPresentation, FeedbackTarget, FragmentOpinion } from './types';
import { sameOpinion, sameTarget, targetKey, type FeedbackBlock } from './fragment-feedback';
import { skillName } from './graph-contract';
import { useFragmentFeedback } from './useFragmentFeedback';
import './interview-assessment.css';

const answers = [
  { value: 'yes', label: 'Скорее да' }, { value: 'no', label: 'Скорее нет' }, { value: 'unknown', label: 'Не могу сказать' },
] as const;
const answerLabel = (answer: FragmentOpinion['answer']) => answers.find(item => item.value === answer)?.label ?? 'Без ответа';

export function FragmentQuestion({ assessment, block, overlapping, playing, commentOpen, onCommentOpen, onCommentClose, onChoose, fallback, legacyComment }: {
  assessment: AssessmentPresentation;
  block: FeedbackBlock | null;
  overlapping: FeedbackBlock[];
  playing: boolean;
  commentOpen: boolean;
  onCommentOpen: () => void;
  onCommentClose: () => void;
  onChoose: (target: FeedbackTarget) => void;
  fallback: ReactNode;
  legacyComment: ReactNode;
}) {
  const id = useId();
  const feedback = useFragmentFeedback(assessment);
  if (!block) return <>{fallback}</>;
  const target = { criterionId: block.criterionId, evidenceId: block.evidenceId };
  const draft = feedback.get(target);
  const busy = draft.status === 'saving';
  const dirty = !sameOpinion(draft.opinion, draft.saved);
  const activeOptions = overlapping.some(item => sameTarget(block, item)) ? overlapping : [block, ...overlapping];
  const pulsing = playing && !commentOpen && draft.opinion.answer === undefined && overlapping.some(item => sameTarget(block, item));

  return <section className="iv-fragment-question" aria-labelledby={`${id}-question`} data-pulsing={pulsing}>
    <div className="iv-fragment-heading">
      <p id={`${id}-question`}>{block.evidence.feedbackQuestion}</p>
      {activeOptions.length > 1 && !commentOpen && <label className="iv-fragment-picker" title="Другой вопрос к этому моменту">
        <span className="iv-sr-only">Вопрос к этому моменту</span>
        <select value={targetKey(block)} onChange={event => {
          const next = activeOptions.find(item => item.key === event.target.value);
          if (next) onChoose(next);
        }}>{activeOptions.map(item => <option key={item.key} value={item.key}>{skillName(item.criterion)} · {item.evidence.time}</option>)}</select>
        <span aria-hidden="true">{activeOptions.length}<ChevronDown size={12}/></span>
      </label>}
    </div>
    <div className="iv-fragment-actions">
      <div className="iv-fragment-answers" role="group" aria-label="Ваш ответ на вопрос">
        {answers.map(answer => <button type="button" key={answer.value} data-answer={answer.value} aria-pressed={draft.opinion.answer === answer.value}
          disabled={busy} onClick={() => { void feedback.save(target, { ...draft.opinion, answer: answer.value }); }}>{answer.label}</button>)}
      </div>
      <button type="button" className="iv-fragment-comment" aria-expanded={commentOpen} onClick={commentOpen ? onCommentClose : onCommentOpen}>
        <MessageSquare size={14}/><span>Пояснить</span>
      </button>
      <span className="iv-fragment-status" role="status">{busy ? 'Сохраняю…' : draft.status === 'saved' || (!dirty && draft.saved.version > 0) ? 'Сохранено' : dirty && draft.status === 'idle' ? 'Черновик' : ''}</span>
      {legacyComment}
    </div>
    {commentOpen && <form className="iv-fragment-editor" onSubmit={event => { event.preventDefault(); void feedback.save(target).then(saved => { if (saved) onCommentClose(); }); }}>
      <label className="iv-sr-only" htmlFor={`${id}-comment`}>Пояснение к вашему ответу: {skillName(block.criterion)}</label>
      <textarea id={`${id}-comment`} autoFocus rows={2} maxLength={4000} placeholder="Ваше пояснение к этому фрагменту…" value={draft.opinion.comment}
        onChange={event => feedback.edit(target, { ...draft.opinion, comment: event.target.value })}
        onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); onCommentClose(); } }}/>
      <div><span>{skillName(block.criterion)} · {block.evidence.time}</span><button type="button" onClick={onCommentClose}>Закрыть</button><button type="submit" disabled={busy || (!dirty && draft.status !== 'error' && draft.status !== 'conflict')}>Сохранить</button></div>
    </form>}
    {(draft.status === 'error' || draft.status === 'conflict') && <div className="iv-fragment-error" role="alert">
      <span>{draft.error}</span>
      {draft.status === 'conflict' && <p>Сейчас сохранено: {answerLabel(draft.saved.answer)}{draft.saved.comment ? ` · ${draft.saved.comment}` : ''}</p>}
      <div><button type="button" onClick={() => { void feedback.save(target); }}>{draft.status === 'conflict' ? 'Сохранить мой ответ' : 'Повторить'}</button>
        {draft.status === 'conflict' && <button type="button" onClick={() => feedback.acceptRemote(target)}>Принять сохранённый</button>}</div>
    </div>}
  </section>;
}
