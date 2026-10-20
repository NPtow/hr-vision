import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Dialog, Popover, Tabs } from 'radix-ui';
import {
  AlignLeft, ArrowLeft, ArrowRight, Check, ChevronDown, ListChecks, Maximize2, Mic,
  MicOff, Minimize2, MonitorUp, PanelRightClose, PanelRightOpen, PhoneOff, RotateCcw,
  SquarePen, Video, VideoOff, X,
} from 'lucide-react';
import type { CoachDraft, CoachPerson, CoachTranscriptTurn } from './coach-types';
import { TiptapNoteEditor } from './TiptapNoteEditor';
import { documentFromText } from './note-document';
import { OriginalResumeViewer } from './OriginalResumeViewer';
import './recruiter-coach.css';
import './recruiter-resume.css';

type MediaControls = {
  micOn: boolean; cameraOn: boolean; screenOn: boolean; busy?: boolean;
  toggleMic: () => void; toggleCamera: () => void; toggleScreen: () => void;
};
export type RecruiterInterviewShellProps = {
  person: CoachPerson; draft: CoachDraft; onChange: (value: Partial<CoachDraft>) => void;
  /** Mount the existing call here. Collapsing the panel never unmounts this slot. */
  mediaSlot: ReactNode; selfPreview?: ReactNode;
  /** Omit for Daily Prebuilt: its iframe already owns device controls. */
  mediaControls?: MediaControls;
  phase: 'preview' | 'live' | 'finishing' | 'completed';
  onFinish: () => void; onResume?: () => void; onExport?: () => void;
  notesContent?: (navigateToQuestion: (index: number) => void) => ReactNode;
  resumeContent?: ReactNode; stageToolbar?: ReactNode;
  onClose?: () => void; closeDisabled?: boolean; finishDisabled?: boolean; finishLabel?: string;
  transcript?: CoachTranscriptTurn[];
  notice?: ReactNode; saveError?: string; durationLabel?: string; recording?: boolean;
  writable?: boolean; completedLabel?: string;
};

/** Presentational call surface. No API, storage, room creation or media capture. */
export function RecruiterInterviewShell({
  person, draft, onChange, mediaSlot, selfPreview, mediaControls, phase,
  onFinish, onResume, onExport, notesContent, transcript, notice, saveError,
  resumeContent, stageToolbar, onClose, closeDisabled, finishDisabled, finishLabel,
  durationLabel, recording, writable = phase !== 'finishing', completedLabel = 'Интервью завершено',
}: RecruiterInterviewShellProps) {
  const [panelOpen, setPanelOpen] = useState(true);
  const [tab, setTab] = useState('script');
  const [planOpen, setPlanOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [full, setFull] = useState(false);
  const [screenError, setScreenError] = useState('');
  const root = useRef<HTMLDivElement>(null);
  const nameButton = useRef<HTMLButtonElement>(null);
  const questionScroll = useRef<HTMLDivElement>(null);
  const current = Math.min(Math.max(draft.current, 0), Math.max(person.questions.length - 1, 0));
  const question = person.questions[current];
  const completed = phase === 'completed';
  const finishing = phase === 'finishing';
  useEffect(() => {
    const change = () => setFull(document.fullscreenElement === root.current);
    document.addEventListener('fullscreenchange', change);
    return () => document.removeEventListener('fullscreenchange', change);
  }, []);
  useEffect(() => { questionScroll.current?.scrollTo({ top: 0 }); }, [question?.id, tab]);
  useEffect(() => { if (completed) { setTab('notes'); setPanelOpen(true); } }, [completed]);
  function change(patch: Partial<CoachDraft>) { if (writable) onChange(patch); }
  function go(index: number) { onChange({ current: index }); setPlanOpen(false); setTab('script'); }
  function next() { if (current < person.questions.length - 1) go(current + 1); else setTab('notes'); }
  async function toggleFull() {
    setScreenError('');
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (root.current?.requestFullscreen) await root.current.requestFullscreen();
      else setScreenError('Полный экран недоступен в этом браузере.');
    } catch { setScreenError('Не удалось открыть полный экран. Попробуйте ещё раз.'); }
  }
  return <div ref={root} className={`rcc-root ${panelOpen ? '' : 'rcc-wide'}`}>
    <main className="rcc-stage" aria-label="Видеовстреча">
      <header className="rcc-header">
        {onClose && <button className="rcc-tool rcc-tool-quiet" aria-label="Вернуться к интервью" disabled={closeDisabled || finishing} onClick={onClose}><ArrowLeft size={20}/></button>}
        <button ref={nameButton} className="rcc-person-button" onClick={() => setProfileOpen(true)} aria-haspopup="dialog" aria-label={`Профиль: ${person.name}`}><span>{person.name}</span><ChevronDown size={16}/></button>
        {recording && <span className="rcc-recording"><i/> Запись</span>}
      </header>
      {stageToolbar && <div className="rcc-stage-toolbar">{stageToolbar}</div>}
      <div className="rcc-video-stage">
        <div className="rcc-remote">{mediaSlot}</div>
        {selfPreview && !completed && <div className="rcc-self-preview">{selfPreview}<span className="rcc-self-label">Вы</span></div>}
        {completed && <div className="rcc-finished"><div><Check size={25}/></div><h2>{completedLabel}</h2>{onResume && <button onClick={onResume}><RotateCcw size={16}/> Вернуться</button>}</div>}
      </div>
      {(notice || screenError) && <div className="rcc-call-notice" role="status">{notice || screenError}</div>}
      <footer className="rcc-call-controls" aria-label="Управление встречей">
        <div className="rcc-device-controls">
          {mediaControls && !completed && <>
            <button className="rcc-tool" aria-label={mediaControls.micOn ? 'Выключить микрофон' : 'Включить микрофон'} title={mediaControls.micOn ? 'Выключить микрофон' : 'Включить микрофон'} aria-pressed={mediaControls.micOn} disabled={mediaControls.busy || finishing} onClick={mediaControls.toggleMic}>{mediaControls.micOn ? <Mic/> : <MicOff/>}</button>
            <button className="rcc-tool" aria-label={mediaControls.cameraOn ? 'Выключить камеру' : 'Включить камеру'} title={mediaControls.cameraOn ? 'Выключить камеру' : 'Включить камеру'} aria-pressed={mediaControls.cameraOn} disabled={mediaControls.busy || finishing} onClick={mediaControls.toggleCamera}>{mediaControls.cameraOn ? <Video/> : <VideoOff/>}</button>
            <button className={`rcc-tool ${mediaControls.screenOn ? 'rcc-tool-active' : ''}`} aria-label={mediaControls.screenOn ? 'Остановить показ экрана' : 'Показать экран'} title={mediaControls.screenOn ? 'Остановить показ экрана' : 'Показать экран'} aria-pressed={mediaControls.screenOn} disabled={mediaControls.busy || finishing} onClick={mediaControls.toggleScreen}><MonitorUp/></button>
          </>}
          {durationLabel && <span className="rcc-duration">{durationLabel}</span>}
        </div>
        {!completed && <button className="rcc-hangup" aria-label={finishLabel || (phase === 'preview' ? 'Завершить просмотр' : 'Завершить интервью')} title={finishLabel || (phase === 'preview' ? 'Завершить просмотр' : 'Завершить интервью')} disabled={finishing || finishDisabled} onClick={onFinish}><PhoneOff size={21}/></button>}
        <div className="rcc-view-controls">
          <button className="rcc-tool rcc-tool-quiet" aria-label={panelOpen ? 'Скрыть панель' : 'Показать панель'} title={panelOpen ? 'Скрыть панель' : 'Показать панель'} aria-expanded={panelOpen} aria-controls="rcc-panel" onClick={() => setPanelOpen(!panelOpen)}>{panelOpen ? <PanelRightClose/> : <PanelRightOpen/>}</button>
          <button className="rcc-tool rcc-tool-quiet" aria-label={full ? 'Выйти из полноэкранного режима' : 'На весь экран'} title={full ? 'Выйти из полноэкранного режима' : 'На весь экран'} onClick={() => void toggleFull()}>{full ? <Minimize2/> : <Maximize2/>}</button>
        </div>
      </footer>
    </main>
    <aside id="rcc-panel" className="rcc-sidebar" inert={!panelOpen} aria-hidden={!panelOpen}>
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
              <Popover.Portal container={root.current}><Popover.Content className="rcc-plan-popover" align="start" sideOffset={10} collisionPadding={16}>
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
    </aside>
    <Dialog.Root open={profileOpen} onOpenChange={setProfileOpen}><Dialog.Portal container={root.current}>
      <Dialog.Overlay className="rcc-resume-overlay"/>
      <Dialog.Content className="rcc-resume-sheet" onCloseAutoFocus={event => { event.preventDefault(); nameButton.current?.focus(); }}>
        <header className="rcc-resume-header"><div><span className="rcc-resume-label">Профиль</span><Dialog.Title>{person.name}</Dialog.Title></div><Dialog.Close className="rcc-resume-close" aria-label="Закрыть профиль"><X size={21}/></Dialog.Close></header>
        <Dialog.Description className="rcc-sr-only">Просмотр оригинального файла резюме</Dialog.Description>
        <div className="rcc-resume-body">{resumeContent ?? <OriginalResumeViewer scope={person.id} document={person.originalResume}/>}</div>
      </Dialog.Content>
    </Dialog.Portal></Dialog.Root>
  </div>;
}
