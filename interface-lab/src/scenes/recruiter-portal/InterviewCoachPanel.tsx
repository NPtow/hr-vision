import { useState } from 'react';
import { AlignLeft, ArrowDownToLine, ArrowLeft, ArrowRight, Check, ChevronDown, ListChecks, PanelRightClose, SkipForward, SquarePen } from 'lucide-react';
import { downloadText, type InterviewCoachController, type LiveInterviewTurn } from './interview-coach';
import './interview-coach.css';

export function InterviewCoachPanel({ coach, name, joined, turns, onCollapse }: {
  coach: InterviewCoachController; name: string; joined: boolean; turns: LiveInterviewTurn[]; onCollapse: () => void;
}) {
  const [tab, setTab] = useState<'script' | 'notes' | 'transcript'>('script');
  const script = coach.data?.active;
  const progress = coach.progress;
  const questions = script?.questions || [];
  const index = Math.max(0, questions.findIndex(q => q.id === progress.currentId));
  const question = questions[index];
  const answer = progress.answers?.[question?.id] || { status: '', note: '' };
  const canWrite = joined && coach.data?.interviewStatus === 'live';
  const count = Object.values(progress.answers || {}).filter(value => value.status === 'answered').length;
  function mark(status: 'answered' | 'skipped') {
    coach.change({ ...progress, currentId: questions[Math.min(index + 1, questions.length - 1)].id,
      answers: { ...progress.answers, [question.id]: { ...answer, status } } });
  }
  function exportNotes() {
    downloadText(`${name} — заметки интервью.txt`, `${name}\n\n${questions.map(q =>
      `${q.question}\n${progress.answers?.[q.id]?.note || ''}`).join('\n\n')}\n\n${progress.general || ''}`);
  }
  return <aside className="ric-panel" aria-label="Помощник рекрутера">
    <header className="ric-panel-header"><div className="ric-tabs" role="tablist" aria-label="Материалы интервью">
      {[['script', ListChecks, 'Скрипт'], ['notes', SquarePen, 'Заметки'], ['transcript', AlignLeft, 'Расшифровка']].map(([key, Icon, label]) => {
        const Glyph = Icon as typeof ListChecks;
        return <button key={String(key)} role="tab" aria-label={String(label)} title={String(label)} aria-selected={tab === key} onClick={() => setTab(key as typeof tab)}><Glyph size={21}/></button>;
      })}
    </div><button className="ric-icon" aria-label="Скрыть помощника" onClick={onCollapse}><PanelRightClose size={19}/></button></header>
    <div className="ric-panel-body">
      {tab === 'script' && (question ? <>
        <div className="ric-question-counter"><span>{question.topic || 'Вопрос'}</span><span>{index + 1} / {questions.length}</span></div>
        <h2>{question.question}</h2>
        {question.why && <p className="ric-question-why">{question.why}</p>}
        {!!question.followups.length && <details className="ric-followups"><summary>Уточнить<ChevronDown size={14}/></summary><ul>{question.followups.map((value, i) => <li key={i}>{value}</li>)}</ul></details>}
        <textarea className="ric-answer" aria-label="Заметка к вопросу" placeholder="Ключевые слова из ответа…" value={answer.note} disabled={!canWrite} maxLength={12000} onChange={event => coach.change({ ...progress, answers: { ...progress.answers, [question.id]: { ...answer, note: event.target.value } } })}/>
        <div className="ric-actions"><button className="ric-primary" disabled={!canWrite} onClick={() => mark('answered')}><Check size={16}/>Ответ получен</button><button className="ric-link" disabled={!canWrite} onClick={() => mark('skipped')}>Пропустить<SkipForward size={15}/></button></div>
        <div className="ric-question-nav"><button aria-label="Предыдущий вопрос" disabled={!canWrite || index === 0} onClick={() => coach.change({ ...progress, currentId: questions[index - 1].id })}><ArrowLeft size={18}/></button><span>Раскрыто {count} из {questions.length}</span><button aria-label="Следующий вопрос" disabled={!canWrite || index === questions.length - 1} onClick={() => coach.change({ ...progress, currentId: questions[index + 1].id })}><ArrowRight size={18}/></button></div>
        <details className="ric-all-questions"><summary>Все вопросы</summary><ol>{questions.map((q, i) => <li key={q.id}><button disabled={!canWrite} onClick={() => coach.change({ ...progress, currentId: q.id })} aria-current={i === index ? 'step' : undefined}>{q.question}{progress.answers?.[q.id]?.status === 'answered' && <Check size={14}/>}</button></li>)}</ol></details>
      </> : <div className="ric-empty"><h2>{coach.data?.latest ? 'Скрипт ожидает согласования' : 'Скрипт пока не подготовлен'}</h2><p>Его можно подготовить с агентом и согласовать в карточке интервью. Звонок доступен и без скрипта.</p></div>)}
      {tab === 'notes' && <><h2>Заметки</h2><textarea className="ric-general" aria-label="Общий итог интервью" value={progress.general || ''} disabled={!canWrite} maxLength={24000} placeholder="Что важно запомнить о разговоре…" onChange={event => coach.change({ ...progress, general: event.target.value })}/>{questions.filter(q => progress.answers?.[q.id]?.note).map(q => <div className="ric-note" key={q.id}><small>{q.topic || q.question}</small><p>{progress.answers![q.id].note}</p></div>)}</>}
      {tab === 'transcript' && <><h2>Расшифровка</h2><p className="ric-muted">Реплики с момента подключения этого окна. Полные материалы появятся у завершённого интервью.</p>{turns.length ? turns.map(turn => <div className="ric-turn" key={turn.id}><small>{turn.speaker} · {new Date(turn.at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}</small><p>{turn.text}</p></div>) : <p className="ric-muted">После согласия кандидата включите запись и расшифровку.</p>}</>}
    </div>
    <footer className="ric-panel-footer">
      {coach.error ? <div className="ric-error" role="alert">{coach.error}<button onClick={() => void (coach.dirty ? coach.flush() : coach.refresh())}>Повторить</button></div> : <span role="status">{coach.busy ? 'Сохраняем…' : coach.dirty ? 'Есть несохранённые заметки' : coach.data ? 'Сохранено' : 'Загружаем…'}</span>}
      <button aria-label="Скачать заметки" title="Скачать заметки" onClick={exportNotes}><ArrowDownToLine size={17}/></button>
    </footer>
  </aside>;
}
