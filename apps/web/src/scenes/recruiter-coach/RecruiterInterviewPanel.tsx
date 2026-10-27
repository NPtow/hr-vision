import { useEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { Popover, Tabs } from 'radix-ui';
import { AlignLeft, ArrowLeft, ArrowRight, ChevronDown, ListChecks, SquarePen, X } from 'lucide-react';
import type { CoachDraft, CoachPerson, CoachTranscriptTurn } from './coach-types';
import { TiptapNoteEditor } from './TiptapNoteEditor';
import { documentFromText } from './note-document';

export type RecruiterInterviewPanelProps = {
  person: CoachPerson; draft: CoachDraft; onChange: (value: Partial<CoachDraft>) => void;
  completed?: boolean; writable?: boolean; transcript?: CoachTranscriptTurn[];
  notesContent?: (navigate: (index: number) => void) => ReactNode;
  saveError?: string; onExport?: () => void; container?: RefObject<HTMLElement | null>;
};
/** Optional interview content; this component never owns the call or its lifecycle. */
export function RecruiterInterviewPanel({ person, draft, onChange, completed = false, writable = true,
  transcript, notesContent, saveError, onExport, container }: RecruiterInterviewPanelProps) {
  const [tab, setTab] = useState('script');
  const [planOpen, setPlanOpen] = useState(false);
  const questionScroll = useRef<HTMLDivElement>(null);
  const current = Math.min(Math.max(draft.current, 0), Math.max(person.questions.length - 1, 0));
  const question = person.questions[current];
  useEffect(() => { questionScroll.current?.scrollTo({ top: 0 }); }, [question?.id, tab]);
  useEffect(() => { if (completed) setTab('notes'); }, [completed]);
  function change(patch: Partial<CoachDraft>) { if (writable) onChange(patch); }
  function go(index: number) { onChange({ current: index }); setPlanOpen(false); setTab('script'); }
  function next() { if (current < person.questions.length - 1) go(current + 1); else setTab('notes'); }
  return (
      <Tabs.Root value={tab} onValueChange={setTab} className="rcc-tabs-root">
        <header className="rcc-side-head">
          <Tabs.List className="rcc-tabs" aria-label="Материалы интервью">
            <Tabs.Trigger value="script" aria-label="Вопросы" title="Вопросы"><ListChecks size={20}/></Tabs.Trigger>
            <Tabs.Trigger value="notes" aria-label="Заметки" title="Заметки"><SquarePen size={20}/></Tabs.Trigger>
            {transcript !== undefined && <Tabs.Trigger value="transcript" aria-label="Расшифровка" title="Расшифровка"><AlignLeft size={20}/></Tabs.Trigger>}
          </Tabs.List>
        </header>
        <Tabs.Content value="script" className="rcc-script-panel">
          {question ? <>
          <div className="rcc-question-meta">
            <Popover.Root open={planOpen} onOpenChange={setPlanOpen}>
              <Popover.Trigger className="rcc-plan-trigger" aria-label={`План интервью. Вопрос ${current + 1} из ${person.questions.length}`}>{current + 1}<span>/ {person.questions.length}</span><ChevronDown size={14}/></Popover.Trigger>
              <Popover.Portal container={container?.current}><Popover.Content className="rcc-plan-popover" align="start" sideOffset={10} collisionPadding={16}>
                <div className="rcc-plan-heading">План интервью<Popover.Close aria-label="Закрыть план"><X size={17}/></Popover.Close></div>
                <nav aria-label="Вопросы интервью">{person.questions.map((q, index) => <button key={q.id} onClick={() => go(index)} aria-current={index === current ? 'step' : undefined}>
                  <span className="rcc-plan-number">{index + 1}</span>
                  <span>{q.topic}<small>{q.question}</small></span>{index === current && <span className="rcc-plan-dot"/>}
                </button>)}</nav>
              </Popover.Content></Popover.Portal>
            </Popover.Root>
          </div>
          <div ref={questionScroll} className="rcc-question-scroll">
            <section key={question.id} className="rcc-question">
              <h2>{question.question}</h2>
              {!!question.followups?.length && <details className="rcc-disclosure rcc-followup"><summary>Уточнить<ChevronDown size={15}/></summary><div>{question.followups.map((item, index) => <p key={index}>{item}</p>)}</div></details>}
              {(question.why || question.evidenceNeeded?.length || question.exitRule) && <details className="rcc-disclosure"><summary>На что обратить внимание<ChevronDown size={15}/></summary><div>{question.why && <p>{question.why}</p>}{!!question.evidenceNeeded?.length && <ul>{question.evidenceNeeded.map((item, i) => <li key={i}>{item}</li>)}</ul>}{question.exitRule && <p>{question.exitRule}</p>}</div></details>}
              {question.caseGuide && <details className="rcc-disclosure"><summary>Карточка клиента<ChevronDown size={15}/></summary><div><p>{question.caseGuide.setup}</p><strong>Раскрывать в ответ на вопросы</strong><ul>{question.caseGuide.client_facts.map((item, i) => <li key={i}>{item}</li>)}</ul><p><strong>Сомнение:</strong> {question.caseGuide.concern}</p><p><strong>Когда согласиться:</strong> {question.caseGuide.acceptance_condition}</p><p><strong>Когда завершить:</strong> {question.caseGuide.stop_condition}</p></div></details>}
              <textarea readOnly={!writable} className="rcc-answer-note" aria-label="Заметка к текущему вопросу" placeholder="Заметка к ответу…" value={draft.notes[question.id] || ''} onChange={e => change({ notes: { ...draft.notes, [question.id]: e.target.value } })}/>
            </section>
          </div>
          <footer className="rcc-question-controls">
            <button className="rcc-back" disabled={current === 0} aria-label="Предыдущий вопрос" title="Предыдущий вопрос" onClick={() => go(current - 1)}><ArrowLeft size={19}/></button>
            <button className="rcc-next" onClick={next}>{current === person.questions.length - 1 ? 'К заметкам' : 'Далее'}<ArrowRight size={17}/></button>
          </footer>
          </> : <div className="rcc-script-empty"><ListChecks size={24}/><p>Сценарий пока не добавлен</p><button onClick={() => setTab('notes')}>Открыть заметки<ArrowRight size={15}/></button></div>}
        </Tabs.Content>
        <Tabs.Content value="notes" className="rcc-notes-panel">{notesContent ? notesContent(go) : <div className="rcc-notes-scroll">
          <h2>Заметки</h2>
          <TiptapNoteEditor value={draft.noteDocument || documentFromText(draft.general)} onChange={(noteDocument, general) => change({ noteDocument, general })} editable={writable} label="Общие заметки"/>
        </div>}</Tabs.Content>
        {transcript !== undefined && <Tabs.Content value="transcript" className="rcc-transcript-panel"><h2>Расшифровка</h2>{transcript.length ? transcript.map(turn => <article key={turn.id}><time>{turn.time}</time><div><strong>{turn.speaker}</strong><p>{turn.text}</p></div></article>) : <p className="rcc-quiet">Речь появится здесь после подключения распознавания.</p>}</Tabs.Content>}
        {saveError && <div className="rcc-save-error" role="alert">{saveError}{onExport && <button onClick={onExport}>Скачать заметки</button>}</div>}
      </Tabs.Root>
  );
}
