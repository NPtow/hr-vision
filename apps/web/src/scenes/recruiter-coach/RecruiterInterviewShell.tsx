import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Dialog } from 'radix-ui';
import {
  ArrowLeft, Check, ChevronDown, Maximize2, Mic,
  MicOff, Minimize2, MonitorUp, PanelRightClose, PanelRightOpen, PhoneOff, RotateCcw,
  Video, VideoOff, X,
} from 'lucide-react';
import type { CoachDraft, CoachPerson, CoachTranscriptTurn } from './coach-types';
import { RecruiterInterviewPanel } from './RecruiterInterviewPanel';
import { OptionalCallPanel } from '../../components/call/OptionalCallPanel';
import { OriginalResumeViewer } from './OriginalResumeViewer';
import './recruiter-coach.css';
import './recruiter-resume.css';

type MediaControls = {
  micOn: boolean; cameraOn: boolean; screenOn: boolean; busy?: boolean; canMic?: boolean; canCamera?: boolean; canShare?: boolean;
  toggleMic: () => void; toggleCamera: () => void; toggleScreen: () => void;
};
export type RecruiterInterviewShellProps = {
  person: CoachPerson; draft: CoachDraft; onChange: (value: Partial<CoachDraft>) => void;
  /** Mount the existing call here. Collapsing the panel never unmounts this slot. */
  mediaSlot: ReactNode; selfPreview?: ReactNode;
  /** Omit when the provider iframe owns its native device controls. */
  mediaControls?: MediaControls;
  /** Native provider owns all call chrome; optional sidebar stays independent. */
  providerOwnsControls?: boolean;
  phase: 'preview' | 'live' | 'finishing' | 'completed';
  onFinish: () => void; onResume?: () => void; onExport?: () => void;
  notesContent?: (navigateToQuestion: (index: number) => void) => ReactNode;
  /** Connected optional content owns its own hooks, outside the media lifecycle. */
  panelContent?: ReactNode;
  resumeContent?: ReactNode; stageToolbar?: ReactNode;
  onClose?: () => void; closeDisabled?: boolean; finishDisabled?: boolean; finishLabel?: string;
  transcript?: CoachTranscriptTurn[];
  notice?: ReactNode; saveError?: string; durationLabel?: string; recording?: boolean;
  writable?: boolean; completedLabel?: string;
};

/** Presentational call surface. No API, storage, room creation or media capture. */
export function RecruiterInterviewShell({
  person, draft, onChange, mediaSlot, selfPreview, mediaControls, phase,
  onFinish, onResume, onExport, notesContent, panelContent, transcript, notice, saveError,
  resumeContent, stageToolbar, onClose, closeDisabled, finishDisabled, finishLabel,
  durationLabel, recording, writable = phase !== 'finishing', completedLabel = 'Интервью завершено', providerOwnsControls = false,
}: RecruiterInterviewShellProps) {
  const [panelOpen, setPanelOpen] = useState(true);
  const [profileOpen, setProfileOpen] = useState(false);
  const [full, setFull] = useState(false);
  const [screenError, setScreenError] = useState('');
  const root = useRef<HTMLDivElement>(null);
  const nameButton = useRef<HTMLButtonElement>(null);
  const completed = phase === 'completed';
  const finishing = phase === 'finishing';
  useEffect(() => {
    const change = () => setFull(document.fullscreenElement === root.current);
    document.addEventListener('fullscreenchange', change);
    return () => document.removeEventListener('fullscreenchange', change);
  }, []);
  useEffect(() => { if (completed) setPanelOpen(true); }, [completed]);
  async function toggleFull() {
    setScreenError('');
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (root.current?.requestFullscreen) await root.current.requestFullscreen();
      else setScreenError('Полный экран недоступен в этом браузере.');
    } catch { setScreenError('Не удалось открыть полный экран. Попробуйте ещё раз.'); }
  }
  return <div ref={root} className={`rcc-root ${providerOwnsControls ? 'rcc-native-provider' : ''} ${panelOpen ? '' : 'rcc-wide'}`}>
    <main className="rcc-stage" aria-label="Видеовстреча">
      {!providerOwnsControls && <header className="rcc-header">
        {onClose && <button className="rcc-tool rcc-tool-quiet" aria-label="Вернуться к интервью" disabled={closeDisabled || finishing} onClick={onClose}><ArrowLeft size={20}/></button>}
        <button ref={nameButton} className="rcc-person-button" onClick={() => setProfileOpen(true)} aria-haspopup="dialog" aria-label={`Профиль: ${person.name}`}><span>{person.name}</span><ChevronDown size={16}/></button>
        {recording && <span className="rcc-recording"><i/> Запись</span>}
      </header>}
      {!providerOwnsControls && stageToolbar && <div className="rcc-stage-toolbar">{stageToolbar}</div>}
      <div className="rcc-video-stage">
        <div className="rcc-remote">{mediaSlot}</div>
        {selfPreview && !completed && <div className="rcc-self-preview">{selfPreview}<span className="rcc-self-label">Вы</span></div>}
        {completed && <div className="rcc-finished"><div><Check size={25}/></div><h2>{completedLabel}</h2>{onResume && <button onClick={onResume}><RotateCcw size={16}/> Вернуться</button>}</div>}
      </div>
      {!providerOwnsControls && (notice || screenError) && <div className="rcc-call-notice" role="status">{notice || screenError}</div>}
      {!providerOwnsControls && <footer className="rcc-call-controls" aria-label="Управление встречей">
        <div className="rcc-device-controls">
          {mediaControls && !completed && <>
            <button className="rcc-tool" aria-label={mediaControls.micOn ? 'Выключить микрофон' : 'Включить микрофон'} title={mediaControls.micOn ? 'Выключить микрофон' : 'Включить микрофон'} aria-pressed={mediaControls.micOn} disabled={mediaControls.busy || finishing || mediaControls.canMic === false} onClick={mediaControls.toggleMic}>{mediaControls.micOn ? <Mic/> : <MicOff/>}</button>
            <button className="rcc-tool" aria-label={mediaControls.cameraOn ? 'Выключить камеру' : 'Включить камеру'} title={mediaControls.cameraOn ? 'Выключить камеру' : 'Включить камеру'} aria-pressed={mediaControls.cameraOn} disabled={mediaControls.busy || finishing || mediaControls.canCamera === false} onClick={mediaControls.toggleCamera}>{mediaControls.cameraOn ? <Video/> : <VideoOff/>}</button>
            {mediaControls.canShare !== false && <button className={`rcc-tool ${mediaControls.screenOn ? 'rcc-tool-active' : ''}`} aria-label={mediaControls.screenOn ? 'Остановить показ экрана' : 'Показать экран'} title={mediaControls.screenOn ? 'Остановить показ экрана' : 'Показать экран'} aria-pressed={mediaControls.screenOn} disabled={mediaControls.busy || finishing} onClick={mediaControls.toggleScreen}><MonitorUp/></button>}
          </>}
          {durationLabel && <span className="rcc-duration">{durationLabel}</span>}
        </div>
        {!completed && <button className="rcc-hangup" aria-label={finishLabel || (phase === 'preview' ? 'Завершить просмотр' : 'Завершить интервью')} title={finishLabel || (phase === 'preview' ? 'Завершить просмотр' : 'Завершить интервью')} disabled={finishing || finishDisabled} onClick={onFinish}><PhoneOff size={21}/></button>}
        <div className="rcc-view-controls">
          <button className="rcc-tool rcc-tool-quiet" aria-label={panelOpen ? 'Скрыть панель' : 'Показать панель'} title={panelOpen ? 'Скрыть панель' : 'Показать панель'} aria-expanded={panelOpen} aria-controls="rcc-panel" onClick={() => setPanelOpen(!panelOpen)}>{panelOpen ? <PanelRightClose/> : <PanelRightOpen/>}</button>
          <button className="rcc-tool rcc-tool-quiet" aria-label={full ? 'Выйти из полноэкранного режима' : 'На весь экран'} title={full ? 'Выйти из полноэкранного режима' : 'На весь экран'} onClick={() => void toggleFull()}>{full ? <Minimize2/> : <Maximize2/>}</button>
        </div>
      </footer>}
    </main>
    <aside id="rcc-panel" className="rcc-sidebar" inert={!panelOpen} aria-hidden={!panelOpen}>
      {providerOwnsControls && resumeContent && <button ref={nameButton} className="rcc-native-profile rcc-person-button" onClick={() => setProfileOpen(true)} aria-haspopup="dialog" aria-label={`Профиль: ${person.name}`}><span>{person.name}</span><ChevronDown size={16}/></button>}
      {providerOwnsControls && notice && <div className="rcc-native-notice" role="status">{notice}</div>}
      <OptionalCallPanel label="Материалы интервью">
        {panelContent ?? <RecruiterInterviewPanel person={person} draft={draft} onChange={onChange}
          completed={completed} writable={writable} transcript={transcript} notesContent={notesContent}
          saveError={saveError} onExport={onExport} container={root}/>}
      </OptionalCallPanel>
    </aside>
    <Dialog.Root open={profileOpen} onOpenChange={setProfileOpen}><Dialog.Portal container={root.current}>
      <Dialog.Overlay className="rcc-resume-overlay"/>
      <Dialog.Content className="rcc-resume-sheet" onCloseAutoFocus={event => { event.preventDefault(); nameButton.current?.focus(); }}>
        <header className="rcc-resume-header"><div><span className="rcc-resume-label">Профиль</span><Dialog.Title>{person.name}</Dialog.Title></div><Dialog.Close className="rcc-resume-close" aria-label="Закрыть профиль"><X size={21}/></Dialog.Close></header>
        <Dialog.Description className="rcc-sr-only">Просмотр оригинального файла резюме</Dialog.Description>
        <div className="rcc-resume-body"><OptionalCallPanel label="Резюме">{resumeContent ?? <OriginalResumeViewer scope={person.id} document={person.originalResume}/>}</OptionalCallPanel></div>
      </Dialog.Content>
    </Dialog.Portal></Dialog.Root>
  </div>;
}
