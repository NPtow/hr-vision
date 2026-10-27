import { useEffect, useRef, useState } from 'react';
import { Dialog } from 'radix-ui';
import { ArrowLeft, ArrowRight, Check, ChevronDown, ListChecks, Maximize2, PanelRightClose, PanelRightOpen, SquarePen, Video, X } from 'lucide-react';
import { formatDate, type RecruiterCandidate } from './api';
import { useInterviewCoach } from './interview-coach';
import { InterviewContacts, ResumeAction } from './InterviewPreparation';
import { RecruiterMediaActions } from './RecruiterMediaActions';
import './interview-coach.css';

/** Reading a script is side-effect free: no camera, room, progress or status writes. */
export function RecruiterInterviewWorkspace({ candidate, token, videoConfigured, onJoin, onClose, onUpdated }: {
  candidate: RecruiterCandidate; token: string; videoConfigured: boolean;
  onJoin: () => void; onClose: () => void; onUpdated: () => void | Promise<void>;
}) {
  const coach = useInterviewCoach({ candidateId: candidate.id, interviewId: candidate.interview!.id, token });
  const [currentId, setCurrentId] = useState('');
  const [panel, setPanel] = useState(true);
  const [tab, setTab] = useState<'plan' | 'notes'>('plan');
  const [now, setNow] = useState(Date.now);
  const [screenError, setScreenError] = useState('');
  const root = useRef<HTMLDivElement>(null);
  const questions = coach.data?.active?.questions || [];
  const index = Math.max(0, questions.findIndex(q => q.id === currentId));
  const question = questions[index];
  const interview = candidate.interview!;
  const completed = (coach.data?.interviewStatus || interview.status) === 'completed';
  const available = !completed && videoConfigured && now >= Date.parse(interview.start) - 15 * 60_000 && now <= Date.parse(interview.end) + 90 * 60_000;
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(timer);
  }, []);
  async function fullscreen() {
    setScreenError('');
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await root.current?.requestFullscreen();
    } catch { setScreenError('Полноэкранный режим недоступен в этом браузере.'); }
  }
  return <Dialog.Root open onOpenChange={open => { if (!open) onClose(); }}>
    <Dialog.Portal>
      <Dialog.Content ref={root} className={`ric-workspace ${panel ? '' : 'ric-workspace-wide'}`} aria-describedby={undefined}>
        <main className="ric-workspace-stage">
          <header className="ric-workspace-header">
            <div><Dialog.Title>{candidate.name}</Dialog.Title><p>{formatDate(interview.start)}</p></div>
            <div className="ric-actions"><InterviewContacts candidate={candidate}/><ResumeAction coach={coach} name={candidate.name} container={root.current}/><Dialog.Close className="ric-icon" aria-label="Вернуться к интервью"><X size={20}/></Dialog.Close></div>
          </header>
          <div className="ric-workspace-question" key={question?.id || 'empty'}>
            {coach.error ? <div className="ric-error" role="alert">{coach.error}<button onClick={() => void coach.refresh()}>Повторить</button></div> : !coach.data ? <p>Загружаем интервью…</p> : question ? <>
              <p className="ric-workspace-topic">{question.topic} <span>{index + 1} / {questions.length}</span></p>
              <h2>{question.question}</h2>
              {question.why && <p className="ric-workspace-why">{question.why}</p>}
              {!!question.followups.length && <details className="ric-followups"><summary>Уточнить<ChevronDown size={15}/></summary><ul>{question.followups.map((item, i) => <li key={i}>{item}</li>)}</ul></details>}
              {completed && coach.progress.answers?.[question.id]?.note && <p className="ric-workspace-answer">{coach.progress.answers[question.id].note}</p>}
            </> : <h2>Скрипт не подготовлен</h2>}
          </div>
          {screenError && <p className="ric-error" role="alert">{screenError}</p>}
          <footer className="ric-workspace-footer">
            <div className="ric-actions">
              <button className="ric-icon" aria-label="Предыдущий вопрос" disabled={!question || index === 0} onClick={() => setCurrentId(questions[index - 1].id)}><ArrowLeft size={20}/></button>
              <button className="ric-icon" aria-label="Следующий вопрос" disabled={!question || index === questions.length - 1} onClick={() => setCurrentId(questions[index + 1].id)}><ArrowRight size={20}/></button>
            </div>
            <div className="ric-actions">
              {!completed && <><span className="ric-muted">{available ? '' : !videoConfigured ? 'Видеосвязь недоступна' : now < Date.parse(interview.start) - 15 * 60_000 ? 'Вход откроется за 15 минут до встречи' : 'Время подключения истекло'}</span><button className="ric-workspace-join" disabled={!available} onClick={onJoin}><Video size={17}/>{interview.status === 'live' ? 'Вернуться в звонок' : 'Подключиться к звонку'}</button></>}
              <button className="ric-icon" aria-label={panel ? 'Скрыть панель' : 'Показать панель'} onClick={() => setPanel(!panel)}>{panel ? <PanelRightClose size={20}/> : <PanelRightOpen size={20}/>}</button>
              <button className="ric-icon" aria-label="На весь экран" onClick={() => void fullscreen()}><Maximize2 size={19}/></button>
            </div>
          </footer>
        </main>
        {panel && <aside className="ric-panel" aria-label="План интервью">
          <header className="ric-panel-header">
            <div className="ric-tabs" role="tablist" aria-label="Материалы интервью">
              <button role="tab" aria-label="План интервью" aria-selected={tab === 'plan'} onClick={() => setTab('plan')}><ListChecks size={21}/></button>
              {completed && <button role="tab" aria-label="Заметки интервью" aria-selected={tab === 'notes'} onClick={() => setTab('notes')}><SquarePen size={21}/></button>}
            </div><button className="ric-icon" aria-label="Скрыть панель" onClick={() => setPanel(false)}><PanelRightClose size={19}/></button>
          </header>
          <div className="ric-panel-body">
            {tab === 'plan' ? <><h2>План интервью</h2><ol className="ric-plan">{questions.map((q, i) => <li key={q.id}><button aria-current={q.id === question?.id ? 'step' : undefined} onClick={() => setCurrentId(q.id)}><span>{String(i + 1).padStart(2, '0')}</span><div><small>{q.topic}</small><p>{q.question}</p></div>{coach.progress.answers?.[q.id]?.status === 'answered' && <Check size={15}/>}</button></li>)}</ol>{coach.data && !questions.length && <p className="ric-muted">Скрипт не подготовлен</p>}</> : <>
              <h2>Заметки интервью</h2><p className="ric-saved-note">{coach.progress.general || 'Общих заметок нет.'}</p>
              {questions.filter(q => coach.progress.answers?.[q.id]?.note).map(q => <div className="ric-note" key={q.id}><small>{q.topic || q.question}</small><p>{coach.progress.answers![q.id].note}</p></div>)}
            </>}
            {completed && <div className="ric-workspace-materials"><RecruiterMediaActions kind="interview" candidateId={candidate.id} status="completed" token={token} onUpdated={onUpdated}/></div>}
          </div>
        </aside>}
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
