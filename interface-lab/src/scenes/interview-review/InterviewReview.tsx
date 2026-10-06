import { useEffect, useId, useMemo, useRef, useState, type SyntheticEvent } from 'react';
import { AlertCircle, FileText, ListVideo, MessageSquareText, Video, VideoOff } from 'lucide-react';
import type { Candidate } from '../agency-product/model';
import { useInterviewMedia, type InterviewMedia } from './InterviewMediaContext';
import './interview-review.css';

export type { InterviewMedia } from './InterviewMediaContext';
export type InterviewReviewProps = {
  person: Candidate;
  initialEvidenceIndex?: number;
  compact?: boolean;
  media?: InterviewMedia;
};

export type RecordingChapter = {
  title: string;
  detail: string;
  fragment: string;
  source: string;
  seconds: number | null;
  endSeconds?: number | null;
  isQuote?: boolean;
};

type RecordingReviewProps = {
  sourceId: string;
  name: string;
  chapters: RecordingChapter[];
  kind: 'screening' | 'meeting';
  initialEvidenceIndex?: number;
  compact?: boolean;
  media?: InterviewMedia;
  summary?: string;
  unknown?: string;
  transcriptText?: string;
  emptyMessage?: string;
  fullChapters?: RecordingChapter[];
  shortPlaylist?: boolean;
};

/** Parse a source's mm:ss or hh:mm:ss. A missing timestamp is never a seekable chapter. */
export function evidenceTime(source: string): number | null {
  const timestamp = source.match(/(?:^|[^\d:])(\d{1,2}:\d{2}(?::\d{2})?)(?![\d:])/);
  if (!timestamp) return null;
  const pieces = timestamp[1].split(':').map(Number);
  if (pieces.slice(1).some(value => value > 59)) return null;
  return pieces.reduce((seconds, value) => seconds * 60 + value, 0);
}

function formatTime(seconds: number) {
  const value = Math.floor(Math.max(0, seconds));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor(value / 60) % 60;
  const rest = String(value % 60).padStart(2, '0');
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${rest}` : `${String(minutes).padStart(2, '0')}:${rest}`;
}

/** Initial HR interview only. Do not reuse its materials as a manager meeting recording. */
export function InterviewReview(props: InterviewReviewProps) {
  const localMedia = useInterviewMedia(props.person.id);
  const media = props.media ?? localMedia;
  // A candidate/source change must stop and discard the previous player's state.
  return <RecordingReview key={`${props.person.id}:${media?.src || 'missing'}`}
    sourceId={props.person.id} name={props.person.name} kind="screening"
    chapters={props.person.evidence.map(e => ({ ...e, seconds: evidenceTime(e.source) }))}
    summary={props.person.summary} unknown={props.person.unknown}
    initialEvidenceIndex={props.initialEvidenceIndex} compact={props.compact} media={media}/>;
}

/** Only this meeting's server-supplied materials. It never reads screening fixture media. */
export function MeetingRecordingReview(props: Omit<RecordingReviewProps, 'kind' | 'summary' | 'unknown'>) {
  return <RecordingReview key={`${props.sourceId}:${props.media?.src || 'missing'}`} {...props} kind="meeting"/>;
}

/** Existing HR materials retain their actual cuts, chapters and source text. */
export function SourceScreeningReview(props: Omit<RecordingReviewProps, 'kind'>) {
  return <RecordingReview key={`${props.sourceId}:${props.media?.src || 'missing'}`} {...props} kind="screening"/>;
}

function RecordingReview({ sourceId, name, chapters: evidence, kind, summary, unknown, transcriptText,
  emptyMessage, initialEvidenceIndex, compact = false, media, fullChapters, shortPlaylist = false }: RecordingReviewProps) {
  const headingId = useId();
  const player = useRef<HTMLVideoElement>(null);
  const meeting = kind === 'meeting';
  const [mode, setMode] = useState<'short' | 'full'>(meeting ? 'full' : 'short');
  const requestedIndex = Number.isInteger(initialEvidenceIndex) ? initialEvidenceIndex! : 0;
  const safeIndex = Math.max(0, Math.min(requestedIndex, evidence.length - 1));
  const [selected, setSelected] = useState(safeIndex);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [failed, setFailed] = useState(false);
  const [notice, setNotice] = useState('');
  const pendingSeek = useRef<number | null>(null);
  const chapters = useMemo(() => (mode === 'full' && fullChapters ? fullChapters : evidence).map((item, index) => ({ ...item, index })), [evidence, fullChapters, mode]);
  const chronological = useMemo(() => chapters.filter(chapter => chapter.seconds !== null).sort((a, b) => a.seconds! - b.seconds!), [chapters]);
  const activeChapter = [...chronological].reverse().find(chapter => currentTime >= chapter.seconds!);
  const excerpt = chapters[selected];
  const playlistIndex = useRef(0);
  const shortMode = mode === 'short' && shortPlaylist && !!chapters.length;

  useEffect(() => {
    setSelected(safeIndex);
    const seconds = chapters[safeIndex]?.seconds;
    if (initialEvidenceIndex !== undefined && media && seconds !== null && seconds !== undefined) seek(seconds);
  }, [initialEvidenceIndex, safeIndex]);
  useEffect(() => {
    const video = player.current;
    // Prevent overlapping playback when another visible candidate/player starts.
    const pauseOther = (event: Event) => { if ((event as CustomEvent).detail !== video) video?.pause(); };
    window.addEventListener('hr-vision-interview-play', pauseOther);
    return () => { video?.pause(); window.removeEventListener('hr-vision-interview-play', pauseOther); };
  }, []);

  function seek(seconds: number, video = player.current) {
    if (!video || !media || failed) return;
    if (video.readyState < 1) {
      pendingSeek.current = seconds;
      setNotice('Запись загружается. Текст фрагмента уже доступен.');
      return;
    }
    if (!Number.isFinite(video.duration) || seconds >= video.duration) {
      video.pause();
      pendingSeek.current = null;
      setNotice(`Таймкод ${formatTime(seconds)} находится за пределами этой записи. Показан текст фрагмента.`);
      return;
    }
    try {
      video.currentTime = seconds;
      setCurrentTime(seconds);
      setNotice(`В записи выбран момент ${formatTime(seconds)}. Нажмите воспроизведение в плеере.`);
      pendingSeek.current = null;
    } catch {
      setNotice('Перейти к этому моменту пока не удалось. Текст фрагмента доступен ниже.');
    }
  }

  function chooseChapter(index: number) {
    const chapter = chapters[index];
    if (!chapter) return;
    setSelected(index);
    playlistIndex.current = index;
    if (!media) {
      setNotice('Показан текст фрагмента. Видеозапись не подключена.');
    } else if (failed) {
      setNotice('Показан текст фрагмента. Видеозапись недоступна.');
    } else if (chapter.seconds === null) {
      setNotice('Показан текст фрагмента. У источника нет таймкода.');
    } else {
      seek(chapter.seconds);
    }
  }

  function onTimeUpdate(event: SyntheticEvent<HTMLVideoElement>) {
    const seconds = event.currentTarget.currentTime;
    if (shortMode && !event.currentTarget.paused) {
      const current = chapters[playlistIndex.current];
      if (current?.endSeconds && seconds >= current.endSeconds) {
        const next = chapters[playlistIndex.current + 1];
        if (next?.seconds !== null && next?.seconds !== undefined) {
          playlistIndex.current++;
          setSelected(playlistIndex.current);
          seek(next.seconds, event.currentTarget);
          return;
        }
        event.currentTarget.pause();
        setNotice('Короткая версия завершена. Можно посмотреть полное интервью.');
      }
    }
    setCurrentTime(seconds);
    const current = [...chronological].reverse().find(chapter => seconds >= chapter.seconds!);
    if (current) setSelected(current.index);
  }

  function changeMode(next: 'short' | 'full') {
    setMode(next);
    const list = next === 'full' && fullChapters ? fullChapters : evidence;
    const index = next === 'short' ? 0 : list.reduce((last, c, i) => c.seconds !== null && c.seconds <= currentTime ? i : last, 0);
    setSelected(index); playlistIndex.current = index;
    if (next === 'short' && shortPlaylist && list[0]?.seconds !== null && list[0]?.seconds !== undefined) seek(list[0].seconds);
  }

  return <section className={`ir-review${compact ? ' ir-review-compact' : ''}`} aria-labelledby={headingId} data-candidate={sourceId} data-recording-kind={kind}>
    <header className="ir-heading"><div>{meeting && <span className="ir-eyebrow">{name}</span>}<h3 id={headingId}>{meeting ? 'Запись вашей встречи' : 'Интервью с рекрутером'}</h3></div><span className="ir-heading-icon"><Video size={19} aria-hidden="true"/></span></header>

    {!meeting && <div className="ir-mode" role="group" aria-label="Режим просмотра интервью">
      <button type="button" aria-pressed={mode === 'short'} onClick={() => changeMode('short')}><ListVideo size={15} aria-hidden="true"/>{meeting ? 'Фрагменты разговора' : 'Короткая версия'}</button>
      <button type="button" aria-pressed={mode === 'full'} onClick={() => changeMode('full')}><Video size={15} aria-hidden="true"/>Полное интервью</button>
    </div>}

    <div className={`ir-player${media ? ' ir-player-connected' : ''}`}>
      {media ? <>
        <video ref={player} src={media.src} controls playsInline preload="metadata" aria-label={media.localPreview ? 'Локальная тестовая видеозапись' : `Видеозапись: ${name}`} onLoadedMetadata={event => {
          setLoaded(true); setFailed(false); setDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0);
          if (pendingSeek.current !== null) seek(pendingSeek.current, event.currentTarget);
        }} onDurationChange={event => setDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0)} onTimeUpdate={onTimeUpdate} onPlay={() => {
          setPlaying(true);
          if (shortMode && player.current) {
            const seconds = player.current.currentTime;
            const index = chapters.findIndex(c => c.seconds !== null && c.endSeconds && seconds >= c.seconds && seconds < c.endSeconds);
            playlistIndex.current = index >= 0 ? index : 0;
            if (index < 0 && chapters[0].seconds !== null) seek(chapters[0].seconds);
          }
          window.dispatchEvent(new CustomEvent('hr-vision-interview-play', { detail: player.current }));
        }} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onError={() => {
          setFailed(true); setLoaded(false); setPlaying(false); pendingSeek.current = null;
          setNotice('Не удалось открыть видеозапись. Доступные текстовые материалы показаны ниже.');
        }}>Ваш браузер не поддерживает воспроизведение видео.</video>
        {failed && <div className="ir-media-error" role="alert"><AlertCircle size={24} aria-hidden="true"/><strong>Не удалось открыть запись</strong><p>Проверьте доступность файла и поддержку его формата.</p><button type="button" onClick={() => { setFailed(false); setNotice(''); player.current?.load(); }}>Попробовать снова</button></div>}
      </> : <div className="ir-media-missing"><VideoOff size={31} strokeWidth={1.4} aria-hidden="true"/><strong>{meeting ? 'Записи встречи пока нет' : 'Видеозапись не подключена'}</strong><p>{emptyMessage || 'Ключевые моменты и текст интервью доступны ниже.'}</p></div>}
    </div>

    <div className="ir-timeline" aria-label="Таймлайн видеозаписи">
      <input type="range" aria-label="Позиция видеозаписи" min={0} max={duration || 1} step={0.1}
        value={Math.min(currentTime, duration || 1)} disabled={!loaded || failed || !duration}
        aria-valuetext={formatTime(currentTime)} onChange={event => seek(Math.min(Number(event.target.value), Math.max(0, duration - 0.01)))}/>
      <div className="ir-timeline-times"><span>{formatTime(currentTime)}</span><span>{duration ? formatTime(duration) : 'Длительность появится с записью'}</span></div>
      {!!duration && !failed && <div className="ir-timeline-markers" aria-label="Моменты на таймлайне">{chronological.filter(c => c.seconds! < duration).filter((c, i, all) => i === 0 || Math.floor(c.seconds! / (duration / 5)) > Math.floor(all[i - 1].seconds! / (duration / 5))).map(c => <button type="button" key={c.index} style={{ left: `clamp(22px, ${c.seconds! / duration * 100}%, calc(100% - 22px))` }} onClick={() => chooseChapter(c.index)} aria-label={`На таймлайне: ${c.title}, ${formatTime(c.seconds!)}`} title={`${formatTime(c.seconds!)} · ${c.title}`}/>)}</div>}
    </div>

    {meeting && <div className="ir-player-caption"><span>Таймкоды из расшифровки этой встречи</span>{media && loaded && !failed && <span className="ir-time">{playing ? 'Воспроизведение' : 'Позиция'} · {formatTime(currentTime)}</span>}</div>}
    {media?.localPreview && <p className="ir-local-source">Локальная проверка: {media.label || 'видеофайл'}. Содержание файла может не совпадать с текстом примера.</p>}
    {!media?.localPreview && media?.label && <p className="ir-source-label">{media.label}</p>}

    <div className="ir-materials">
      <div className="ir-chapters"><h4>{meeting ? 'Расшифровка по времени' : mode === 'short' ? 'Ключевые моменты' : 'Главы интервью'}</h4>{chapters.length ? <ol>{chapters.map(chapter => <li key={`${sourceId}-${chapter.index}`}><button type="button" aria-pressed={selected === chapter.index} aria-current={loaded && !failed && activeChapter?.index === chapter.index ? 'true' : undefined} onClick={() => chooseChapter(chapter.index)} aria-label={`${media && !failed && chapter.seconds !== null ? 'Открыть момент' : 'Показать текст'}: ${chapter.title}${chapter.seconds === null ? '' : `, ${formatTime(chapter.seconds)}`}`}><span className="ir-chapter-time">{chapter.seconds === null ? <FileText size={14} aria-hidden="true"/> : formatTime(chapter.seconds)}</span><span><strong>{chapter.title}</strong>{mode === 'full' && <small>{chapter.detail}</small>}</span></button></li>)}</ol> : <p className="ir-muted">{meeting ? 'Фрагменты появятся, когда будет готова расшифровка с таймкодами.' : 'Ключевые моменты ещё не подготовлены.'}</p>}</div>

      {excerpt && <article className="ir-excerpt" aria-label="Выбранный фрагмент интервью"><span className="ir-eyebrow">{meeting ? 'Автоматическая расшифровка' : excerpt.isQuote === false ? 'Описание главы из панели ДСА' : 'Со слов кандидата'}</span><h4>{excerpt.title}</h4>{excerpt.isQuote === false ? <p>{excerpt.fragment}</p> : <blockquote>«{excerpt.fragment}»</blockquote>}<p className="ir-source"><FileText size={13} aria-hidden="true"/>{excerpt.source}</p>{excerpt.isQuote !== false && <p className="ir-source-boundary">{meeting ? 'Распознавание может ошибаться. Сверяйте важные формулировки с записью.' : 'Слова кандидата. Независимого подтверждения результата пока нет.'}</p>}</article>}
    </div>
    <p className="ir-seek-status" role="status" aria-live="polite">{notice}</p>

    {transcriptText && <details className="ir-transcript-full"><summary>Вся расшифровка</summary><pre>{transcriptText}</pre></details>}
    {summary && <div className="ir-recruiter"><span className="ir-eyebrow">Заключение рекрутера</span><p>{summary}</p><small>Рекомендация из текущей карточки. Результаты кандидата требуют проверки.</small></div>}
    {unknown && <div className="ir-questions"><MessageSquareText size={18} aria-hidden="true"/><div><h4>Что уточнить на встрече</h4><p>{unknown}</p></div></div>}
  </section>;
}
