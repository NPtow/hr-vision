import { createContext, useContext, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { FileVideo, RotateCcw } from 'lucide-react';
import { candidates, type CandidateId } from '../agency-product/model';
import './interview-review.css';

export type InterviewMedia = {
  src: string;
  label?: string;
  /** Local QA files are not evidence for the fictional candidate's statements. */
  localPreview?: boolean;
};

type MediaMap = Partial<Record<CandidateId, InterviewMedia>>;
type MediaContext = {
  media: MediaMap;
  attach: (id: CandidateId, file: File) => void;
  reset: () => void;
};
const InterviewMediaContext = createContext<MediaContext | null>(null);

/** Session-only object URLs. No file bytes leave the browser or enter storage. */
export function InterviewMediaProvider({ children }: { children: ReactNode }) {
  const [media, setMedia] = useState<MediaMap>({});
  const owned = useRef<Partial<Record<CandidateId, string>>>({});
  useEffect(() => () => {
    Object.values(owned.current).forEach(url => URL.revokeObjectURL(url));
    owned.current = {};
  }, []);

  function attach(id: CandidateId, file: File) {
    const previous = owned.current[id];
    const url = URL.createObjectURL(file);
    owned.current[id] = url;
    setMedia(value => ({ ...value, [id]: { src: url, label: file.name, localPreview: true } }));
    if (previous) URL.revokeObjectURL(previous);
  }
  function reset() {
    Object.values(owned.current).forEach(url => URL.revokeObjectURL(url));
    owned.current = {};
    setMedia({});
  }
  return <InterviewMediaContext.Provider value={{ media, attach, reset }}>{children}</InterviewMediaContext.Provider>;
}

export function useInterviewMedia(id: CandidateId): InterviewMedia | undefined {
  return useContext(InterviewMediaContext)?.media[id];
}

/** Keep this in lab chrome, outside the employer's product controls. */
export function LabMediaControl() {
  const context = useContext(InterviewMediaContext);
  const [person, setPerson] = useState<CandidateId>('anna');
  const [error, setError] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);
  const id = useId();
  if (!context) return null;
  const activeFile = context.media[person];
  const attachedCount = Object.keys(context.media).length;

  return <details className="ir-lab-media">
    <summary><FileVideo size={15} aria-hidden="true"/>Проверить плеер со своим видео{attachedCount > 0 && <span>{attachedCount}</span>}</summary>
    <div className="ir-lab-media-body">
      <p id={`${id}-explanation`}>Локальная проверка плеера. Файл остаётся в этом браузере и не отправляется на сервер. Он не подтверждает цитаты вымышленных кандидатов.</p>
      <div className="ir-lab-media-fields">
        <label htmlFor={`${id}-person`}>Карточка для проверки<select id={`${id}-person`} value={person} onChange={event => { setPerson(event.target.value as CandidateId); setError(''); }}>{candidates.map(candidate => <option key={candidate.id} value={candidate.id}>{candidate.name}</option>)}</select></label>
        <label htmlFor={`${id}-file`}>Локальный видеофайл<input ref={fileInput} id={`${id}-file`} type="file" accept="video/*,.mp4,.webm,.mov,.m4v,.ogv" aria-describedby={`${id}-explanation`} onChange={event => {
          const file = event.target.files?.[0];
          if (!file) return;
          if (!file.type.startsWith('video/') && !/\.(mp4|webm|mov|m4v|ogv)$/i.test(file.name)) {
            setError('Выберите видеофайл. Поддержка формата зависит от браузера.');
            event.target.value = '';
            return;
          }
          context.attach(person, file);
          setError('');
          event.target.value = '';
        }}/></label>
        <button type="button" disabled={!attachedCount} onClick={() => { context.reset(); setError(''); if (fileInput.current) fileInput.current.value = ''; }}><RotateCcw size={14} aria-hidden="true"/>Сбросить локальные файлы</button>
      </div>
      {activeFile && <p className="ir-lab-file" role="status">Выбран для проверки: {activeFile.label}</p>}
      {error && <p className="ir-lab-error" role="alert">{error}</p>}
    </div>
  </details>;
}
