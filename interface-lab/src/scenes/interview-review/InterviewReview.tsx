import { useEffect, useId, useMemo, useRef, useState, type ReactNode, type SyntheticEvent } from 'react';
import { AlertCircle, FileText, ListVideo, MessageSquareText, Video, VideoOff, Play, Pause, Maximize, VolumeX } from 'lucide-react';
import type { Candidate } from '../agency-product/model';
import { useInterviewMedia, type InterviewMedia } from './InterviewMediaContext';
import './interview-review.css';
import playAsset from '../employer-workspace/assets/play.svg';
import candidatePlayAsset from '../employer-workspace/assets/candidate-profile/play.svg';
import candidateVolumeAsset from '../employer-workspace/assets/candidate-profile/volume.svg';
import candidateRepeatAsset from '../employer-workspace/assets/candidate-profile/repeat.svg';
import { groupTranscriptTurns, parseMeetingTranscript, transcriptInRange } from '../connected-journey/transcript';
import { CandidateInterviewPlayer } from './CandidateInterviewPlayer';
import type { InterviewAssistantRequest } from './interview-assistant-api';
import type { AssessmentPresentation } from '../interview-assessment/types';

export type { InterviewMedia } from './InterviewMediaContext';
export type InterviewReviewProps = {
  person: Candidate;
  appearance?: 'panel';
  initialEvidenceIndex?: number;
  compact?: boolean;
  media?: InterviewMedia;
};

export type RecordingChapter = {
  /** Parsed speech is not a thematic chapter, even when reused by legacy views. */
  kind?: 'transcript';
  title: string;
  detail: string;
  fragment: string;
  source: string;
  seconds: number | null;
  endSeconds?: number | null;
  isQuote?: boolean;
  speaker?: string;
};

export type RecordingChapterContext = { sourceId: string; mode: 'short' | 'full'; index: number };
export type RecordingReviewProps = {
  sourceId: string;
  appearance?: 'panel' | 'candidate';
  recruiterContent?: ReactNode;
  /** Optional identity and document actions, composed by the authorised candidate view. */
  header?: ReactNode;
  name: string;
  chapters: RecordingChapter[];
  kind: 'screening' | 'meeting';
  initialEvidenceIndex?: number;
  compact?: boolean;
  media?: InterviewMedia;
  summary?: string;
  unknown?: string;
  transcriptText?: string;
  /** False when transcript timestamps have not been mapped to this recording. */
  transcriptSeekable?: boolean;
  emptyMessage?: string;
  fullChapters?: RecordingChapter[];
  shortPlaylist?: boolean;
  renderChapterComment?: (chapter: RecordingChapter, context: RecordingChapterContext) => ReactNode;
  renderTimeComment?: (seconds: number, onClose: () => void, openRequest?: number) => ReactNode;
  commentTimestamps?: { seconds: number; count: number }[];
  comments?: { id: string; seconds: number; text: string; createdAt: string }[];
  canComment?: boolean;
  /** Public profiles retain review modes but do not ask for fragment feedback. */
  hideFeedbackQuestions?: boolean;
  assistant?: InterviewAssistantRequest;
  /** Only an authenticated adapter may supply a ready assessment and persistence. */
  assessment?: AssessmentPresentation;
  assessmentStatus?: 'loading' | 'unavailable';
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

function TranscriptSpeech({ cues }: { cues: RecordingChapter[] }) {
  return <div className="ir-candidate-transcript-speech">{groupTranscriptTurns(cues).map((turn, index) => <div key={index}>
    {turn.speaker && <strong>{turn.speaker}</strong>}<p>{turn.fragments.join(' ')}</p>
  </div>)}</div>;
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
    initialEvidenceIndex={props.initialEvidenceIndex} compact={props.compact} media={media} appearance={props.appearance}/>;
}

/** Only this meeting's server-supplied materials. It never reads screening fixture media. */
export function MeetingRecordingReview(props: Omit<RecordingReviewProps, 'kind' | 'summary' | 'unknown'>) {
  if (props.appearance === 'candidate') return <CandidateInterviewPlayer key={props.sourceId} {...props} kind="meeting"/>;
  return <RecordingReview key={`${props.sourceId}:${props.media?.src || 'missing'}`} {...props} kind="meeting"/>;
}

/** Existing HR materials retain their actual cuts, chapters and source text. */
export function SourceScreeningReview(props: Omit<RecordingReviewProps, 'kind'>) {
  if (props.appearance === 'candidate') return <CandidateInterviewPlayer key={props.sourceId} {...props} kind="screening"/>;
  return <RecordingReview key={`${props.sourceId}:${props.media?.src || 'missing'}`} {...props} kind="screening"/>;
}

function RecordingReview({ sourceId, name, chapters: evidence, kind, summary, unknown, transcriptText,
  emptyMessage, initialEvidenceIndex, compact = false, media, fullChapters, shortPlaylist = false, appearance, recruiterContent,
  renderChapterComment, renderTimeComment, commentTimestamps }: RecordingReviewProps) {
  const headingId = useId();
  const player = useRef<HTMLVideoElement>(null);
  const chapterList = useRef<HTMLOListElement>(null);
  const meeting = kind === 'meeting';
  const [mode, setMode] = useState<'short' | 'full'>(meeting ? 'full' : 'short');
  useEffect(() => { chapterList.current?.scrollTo({ top: 0, behavior: 'instant' }); }, [mode, sourceId]);
  const requestedIndex = Number.isInteger(initialEvidenceIndex) ? initialEvidenceIndex! : 0;
  const safeIndex = Math.max(0, Math.min(requestedIndex, evidence.length - 1));
  const [selected, setSelected] = useState(safeIndex);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [commentTime, setCommentTime] = useState<number | null>(null);
  const timeCommentReturn = useRef<HTMLButtonElement | null>(null);
  const [fullscreen, setFullscreen] = useState(false);
  useEffect(() => {
    const update = () => setFullscreen(document.fullscreenElement === player.current);
    document.addEventListener('fullscreenchange', update);
    return () => document.removeEventListener('fullscreenchange', update);
  }, []);
  const [failed, setFailed] = useState(false);
  const [notice, setNotice] = useState('');
  const pendingSeek = useRef<number | null>(null);
  const pendingPlay = useRef(false);
  const candidateClip = useRef<{ start: number; end: number | null } | null>(null);
  const chapters = useMemo(() => (mode === 'full' && fullChapters ? fullChapters : evidence).map((item, index) => ({ ...item, index })), [evidence, fullChapters, mode]);
  const chronological = useMemo(() => chapters.filter(chapter => chapter.seconds !== null).sort((a, b) => a.seconds! - b.seconds!), [chapters]);
  const activeChapter = [...chronological].reverse().find(chapter => currentTime >= chapter.seconds!);
  const excerpt = chapters[selected];
  const playlistIndex = useRef(0);
  const shortMode = appearance !== 'candidate' && mode === 'short' && shortPlaylist && !!chapters.length;
  const transcriptParagraphs = useMemo(() => appearance === 'candidate' && transcriptText ? parseMeetingTranscript(transcriptText) : [], [appearance, transcriptText]);
  const excerptEnd = excerpt?.endSeconds != null && excerpt.seconds != null && excerpt.endSeconds > excerpt.seconds
    ? excerpt.endSeconds : chronological.find(chapter => excerpt?.seconds != null && chapter.seconds! > excerpt.seconds)?.seconds ?? null;
  const fragmentParagraphs = excerpt?.seconds != null ? transcriptInRange(transcriptParagraphs, excerpt.seconds, excerptEnd) : [];
  const quotedFragment = !fragmentParagraphs.length && excerpt?.isQuote === true ? excerpt.fragment.trim() : '';
  const hasFullTranscript = !!transcriptParagraphs.length || (!!transcriptText?.trim() && !/^\s*WEBVTT\b/.test(transcriptText));
  const additionalCommentTimes = (commentTimestamps || []).filter(item => Number.isFinite(item.seconds) && item.seconds >= 0 && item.count > 0
    && !chapters.some(chapter => chapter.seconds !== null && Math.abs(chapter.seconds - item.seconds) < 0.001));

  useEffect(() => {
    if (appearance !== 'candidate') return;
    setLoaded(false); setFailed(false); setPlaying(false); setCurrentTime(0); setDuration(0);
  }, [appearance, media?.src]);

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
      if (appearance !== 'candidate') setNotice('Запись загружается. Текст фрагмента уже доступен.');
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
      setNotice(appearance ? '' : `В записи выбран момент ${formatTime(seconds)}. Нажмите воспроизведение в плеере.`);
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
    if (appearance === 'candidate') {
      setNotice('');
      if (chapter.seconds === null) return;
      const nextStart = chronological.find(item => item.seconds! > chapter.seconds!)?.seconds;
      const end = chapter.endSeconds != null && chapter.endSeconds > chapter.seconds ? chapter.endSeconds : nextStart ?? null;
      candidateClip.current = { start: chapter.seconds, end };
      pendingSeek.current = chapter.seconds;
      pendingPlay.current = true;
      if (!media || failed) return;
      seek(chapter.seconds);
      const video = player.current;
      if (!video || video.readyState < 1) return;
      pendingPlay.current = false;
      if (Number.isFinite(video.duration) && chapter.seconds < video.duration) {
        void video.play().catch(playbackFailed);
      }
      return;
    }
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

  function playbackFailed(cause: unknown) {
    // Pausing or choosing another clip may cancel a pending play while the video buffers.
    if (appearance === 'candidate' && cause && typeof cause === 'object' && 'name' in cause && cause.name === 'AbortError') return;
    setNotice('Не удалось начать воспроизведение. Попробуйте ещё раз.');
  }

  function onTimeUpdate(event: SyntheticEvent<HTMLVideoElement>) {
    const seconds = event.currentTarget.currentTime;
    if (appearance === 'candidate') {
      const clip = candidateClip.current;
      if (clip?.end != null && !event.currentTarget.paused && seconds >= clip.end) {
        event.currentTarget.pause();
        event.currentTarget.currentTime = Math.min(clip.end, event.currentTarget.duration);
        setCurrentTime(event.currentTarget.currentTime);
        return;
      }
      setCurrentTime(seconds);
      return;
    }
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


  function togglePlayback() {
    const video = player.current;
    if (!video || !media || failed) return;
    if (!video.paused) video.pause();
    else {
      const clip = appearance === 'candidate' ? candidateClip.current : null;
      if (clip && (video.currentTime < clip.start || (clip.end != null && video.currentTime >= clip.end))) seek(clip.start);
      void video.play().catch(playbackFailed);
    }
  }
  const videoBlock = (<div className={`ir-player${media ? ' ir-player-connected' : ''}`}>
      {media ? <>
        <video ref={player} src={media.src} controls={!appearance || fullscreen} muted={muted} playsInline preload="metadata" aria-label={media.localPreview ? 'Локальная тестовая видеозапись' : `Видеозапись: ${name}`} onLoadedMetadata={event => {
          setLoaded(true); setFailed(false); setDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0);
          if (pendingSeek.current !== null) seek(pendingSeek.current, event.currentTarget);
          if (appearance === 'candidate' && pendingPlay.current) {
            pendingPlay.current = false;
            if (candidateClip.current && candidateClip.current.start < event.currentTarget.duration) {
              void event.currentTarget.play().catch(playbackFailed);
            }
          }
        }} onDurationChange={event => setDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0)} onTimeUpdate={onTimeUpdate} onPlay={() => {
          setPlaying(true);
          if (appearance === 'candidate') setNotice('');
          if (shortMode && player.current) {
            const seconds = player.current.currentTime;
            const index = chapters.findIndex(c => c.seconds !== null && c.endSeconds && seconds >= c.seconds && seconds < c.endSeconds);
            playlistIndex.current = index >= 0 ? index : 0;
            if (index < 0 && chapters[0].seconds !== null) seek(chapters[0].seconds);
          }
          window.dispatchEvent(new CustomEvent('hr-vision-interview-play', { detail: player.current }));
        }} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onError={() => {
          setFailed(true); setLoaded(false); setPlaying(false);
          pendingSeek.current = appearance === 'candidate' ? candidateClip.current?.start ?? pendingSeek.current : null;
          setNotice(appearance === 'candidate' ? '' : 'Не удалось открыть видеозапись. Доступные текстовые материалы показаны ниже.');
        }}>Ваш браузер не поддерживает воспроизведение видео.</video>
        {appearance === 'panel' && !playing && !failed && <button className="ir-big-play" onClick={togglePlayback} aria-label="Воспроизвести интервью"><img src={playAsset} alt=""/></button>}
        {failed && <div className="ir-media-error" role="alert"><AlertCircle size={24} aria-hidden="true"/><strong>Не удалось открыть запись</strong><p>Проверьте доступность файла и поддержку его формата.</p><button type="button" onClick={() => { setFailed(false); setNotice(''); player.current?.load(); }}>Попробовать снова</button></div>}
      </> : <div className="ir-media-missing"><VideoOff size={31} strokeWidth={1.4} aria-hidden="true"/><strong>{meeting ? 'Записи встречи пока нет' : 'Видеозапись не подключена'}</strong><p>{emptyMessage || 'Ключевые моменты и текст интервью доступны ниже.'}</p></div>}
    </div>);
  const chaptersBlock = (<div className="ir-chapters"><h4>{meeting ? 'Расшифровка по времени' : mode === 'short' ? 'Ключевые моменты' : 'Главы интервью'}</h4>{chapters.length ? <ol ref={chapterList} tabIndex={appearance === 'panel' ? 0 : undefined} aria-label={appearance === 'panel' ? 'Главы интервью' : undefined}>{chapters.map(chapter => <li key={`${sourceId}-${chapter.index}`}><button type="button" aria-pressed={selected === chapter.index} aria-current={loaded && !failed && activeChapter?.index === chapter.index ? 'true' : undefined} onClick={() => chooseChapter(chapter.index)} aria-label={`${media && !failed && chapter.seconds !== null ? 'Открыть момент' : 'Показать текст'}: ${chapter.title}${chapter.seconds === null ? '' : `, ${formatTime(chapter.seconds)}`}`}><span className="ir-chapter-time">{chapter.seconds === null ? <FileText size={14} aria-hidden="true"/> : formatTime(chapter.seconds)}</span><span><strong>{chapter.title}</strong>{mode === 'full' && <small>{chapter.detail}</small>}</span></button></li>)}</ol> : <p className="ir-muted">{meeting ? 'Фрагменты появятся, когда будет готова расшифровка с таймкодами.' : 'Ключевые моменты ещё не подготовлены.'}</p>}</div>);
  const excerptBlock = (excerpt && <article className="ir-excerpt" aria-label="Выбранный фрагмент интервью"><span className="ir-eyebrow">{meeting ? 'Автоматическая расшифровка' : excerpt.isQuote === false ? 'Описание главы из панели ДСА' : 'Со слов кандидата'}</span><h4>{excerpt.title}</h4>{excerpt.isQuote === false ? <p>{excerpt.fragment}</p> : <blockquote>«{excerpt.fragment}»</blockquote>}<p className="ir-source"><FileText size={13} aria-hidden="true"/>{excerpt.source}</p>{excerpt.isQuote !== false && <p className="ir-source-boundary">{meeting ? 'Распознавание может ошибаться. Сверяйте важные формулировки с записью.' : 'Слова кандидата. Независимого подтверждения результата пока нет.'}</p>}</article>);
  const recruiterBlock = recruiterContent || ((summary || unknown) && <><span className="ir-eyebrow">Мнение рекрутера</span>{summary && <p>{summary}</p>}{unknown && <><span className="ir-eyebrow">На встрече</span><p className="ir-recruiter-question">{unknown}</p></>}</>);

  if (appearance === 'candidate' && !media && !chapters.length && !hasFullTranscript && !additionalCommentTimes.length) return null;
  if (appearance === 'candidate') return <section className="ir-review ir-candidate-review" aria-label={meeting ? 'Запись встречи' : 'Интервью с рекрутером'} data-candidate={sourceId} data-recording-kind={kind}>
      {media && <div className="ir-candidate-main">
        <div className="ir-candidate-player">
          {videoBlock}
          <div className="ir-candidate-controls" aria-label="Управление видеозаписью">
            <div className="ir-candidate-track"><input type="range" aria-label="Позиция видеозаписи" min={0} max={duration || 1} step={0.1} value={Math.min(currentTime, duration || 1)} disabled={!loaded || failed || !duration} aria-valuetext={formatTime(currentTime)} onChange={event => { candidateClip.current = null; seek(Math.min(Number(event.target.value), Math.max(0, duration - 0.01))); }}/>
              {!!duration && !failed && <div className="ir-timeline-markers" aria-label="Моменты на таймлайне">{chronological.filter(chapter => chapter.seconds! < duration).map(chapter => <button type="button" key={chapter.index} style={{ left: `${chapter.seconds! / duration * 100}%` }} onClick={() => chooseChapter(chapter.index)} aria-label={`На таймлайне: ${chapter.title}, ${formatTime(chapter.seconds!)}`} title={`${formatTime(chapter.seconds!)} · ${chapter.title}`}/>)}</div>}
            </div>
            <div className="ir-candidate-control-row">
              <button type="button" aria-label={playing ? 'Пауза' : 'Воспроизвести'} disabled={!media || failed} onClick={togglePlayback}>{playing ? <Pause size={16}/> : <img src={candidatePlayAsset} alt=""/>}</button>
              <button type="button" aria-label={muted ? 'Включить звук' : 'Выключить звук'} disabled={!media || failed} onClick={() => setMuted(value => !value)}>{muted ? <VolumeX size={16}/> : <img src={candidateVolumeAsset} alt=""/>}</button>
              <span>{formatTime(currentTime)} / {duration ? formatTime(duration) : '—'}</span>
              <button className="ir-candidate-fullscreen" type="button" aria-label="Видео на весь экран" disabled={!media || failed} onClick={() => { const video = player.current; if (!video) return; if (video.requestFullscreen) void video.requestFullscreen().catch(() => setNotice('Полный экран недоступен.')); else (video as HTMLVideoElement & { webkitEnterFullscreen?: () => void }).webkitEnterFullscreen?.(); }}><Maximize size={16}/></button>
            </div>
          </div>
        </div>
        {notice && <p className="ir-candidate-error" role="alert">{notice}</p>}
      </div>}
    {!!(chapters.length || additionalCommentTimes.length || fragmentParagraphs.length || quotedFragment || hasFullTranscript) && <div className={`ir-candidate-materials${chapters.length || additionalCommentTimes.length ? ' ir-candidate-has-chapters' : ''}${fragmentParagraphs.length || quotedFragment || hasFullTranscript ? ' ir-candidate-has-transcript' : ''}`}>
      {!!(chapters.length || additionalCommentTimes.length) && <aside className="ir-candidate-chapters" aria-label="Ключевые моменты">
        {chapters.length ? <><h3>Ключевые моменты</h3><ol ref={chapterList}>{chapters.map(chapter => <li key={`${sourceId}:${mode}:${chapter.index}`} data-selected={selected === chapter.index ? 'true' : undefined}>
          <div className="ir-candidate-chapter-row"><button type="button" className="ir-candidate-chapter" aria-pressed={selected === chapter.index} onClick={() => chooseChapter(chapter.index)} aria-label={`${media && !failed && chapter.seconds !== null ? 'Открыть момент' : 'Показать текст'}: ${chapter.title}${chapter.seconds === null ? '' : `, ${formatTime(chapter.seconds)}`}`}><strong>{chapter.title}</strong>{chapter.seconds !== null && <span>{formatTime(chapter.seconds)}{chapter.endSeconds != null ? ` — ${formatTime(chapter.endSeconds)}` : ''}</span>}</button>
            {media && !failed && chapter.seconds !== null && selected === chapter.index && <button type="button" className="ir-candidate-repeat" aria-label={`Повторить: ${chapter.title}`} onClick={() => chooseChapter(chapter.index)}><img src={candidateRepeatAsset} alt=""/></button>}
          </div>
          {chapter.seconds !== null && renderChapterComment && <div className="ir-candidate-chapter-comment" hidden={selected !== chapter.index}>{renderChapterComment(chapter, { sourceId, mode, index: chapter.index })}</div>}
        </li>)}</ol></> : null}
        {renderTimeComment && !!additionalCommentTimes.length && <details className="ir-candidate-saved-comments">
          <summary>Комментарии · {additionalCommentTimes.reduce((sum, item) => sum + item.count, 0)}</summary>
          <div className="ir-candidate-comment-times" aria-label="Сохранённые комментарии">{additionalCommentTimes.map(item => <button className="ir-candidate-comment-button" key={item.seconds} type="button" aria-label={`Комментарии к ${formatTime(item.seconds)}, ${item.count}`} disabled={commentTime !== null} onClick={event => { timeCommentReturn.current = event.currentTarget; setCommentTime(item.seconds); candidateClip.current = null; seek(item.seconds); }}>{formatTime(item.seconds)} · {item.count}</button>)}</div>
          {commentTime !== null && <div key={`${sourceId}:${commentTime}`}>{renderTimeComment(commentTime, () => { setCommentTime(null); requestAnimationFrame(() => timeCommentReturn.current?.focus()); })}</div>}
        </details>}
      </aside>}
    {!!(fragmentParagraphs.length || quotedFragment || hasFullTranscript) && <section className="ir-candidate-transcript" aria-label="Расшифровка">
      {!!(fragmentParagraphs.length || quotedFragment) && <>
        <header className="ir-candidate-fragment-heading"><h3>Расшифровка</h3>{excerpt?.seconds != null && <span>{formatTime(excerpt.seconds)}{excerptEnd !== null ? ` — ${formatTime(excerptEnd)}` : ''}</span>}</header>
        <div aria-label="Текст выбранного фрагмента">{fragmentParagraphs.length ? <TranscriptSpeech cues={fragmentParagraphs}/> : <div className="ir-candidate-transcript-speech"><p>{quotedFragment}</p></div>}</div>
      </>}
      {hasFullTranscript && <details className="ir-candidate-full-transcript"><summary>Полная расшифровка</summary>
        {transcriptParagraphs.length ? <TranscriptSpeech cues={transcriptParagraphs}/> : <pre>{transcriptText}</pre>}
      </details>}
    </section>}
    </div>}
  </section>;

  if (appearance === 'panel') return <section className="ir-review ir-panel-review" aria-labelledby={headingId} data-candidate={sourceId} data-recording-kind={kind}>
    <div className="ir-panel-main">
      <header className="ir-panel-heading"><h3 id={headingId}>Интервью с рекрутером</h3><div className="ir-mode" role="group" aria-label="Режим просмотра интервью"><button type="button" aria-pressed={mode === 'short'} onClick={() => changeMode('short')}>Коротко</button><button type="button" aria-pressed={mode === 'full'} onClick={() => changeMode('full')}>Целиком</button></div></header>
      {videoBlock}
      <div className="ir-panel-controls" aria-label="Таймлайн видеозаписи">
        <button aria-label={playing ? 'Пауза' : 'Воспроизвести'} disabled={!media || failed} onClick={togglePlayback}>{playing ? <Pause size={16}/> : <Play size={16}/>}</button><span>{formatTime(currentTime)}</span>
        <div className="ir-panel-track"><input type="range" aria-label="Позиция видеозаписи" min={0} max={duration || 1} step={0.1} value={Math.min(currentTime, duration || 1)} disabled={!loaded || failed || !duration} aria-valuetext={formatTime(currentTime)} onChange={event => seek(Math.min(Number(event.target.value), Math.max(0, duration - 0.01)))}/>
          {!!duration && !failed && <div className="ir-timeline-markers" aria-label="Моменты на таймлайне">{chronological.filter(c => c.seconds! < duration).map(c => <button type="button" key={c.index} style={{ left: `${c.seconds! / duration * 100}%` }} onClick={() => chooseChapter(c.index)} aria-label={`На таймлайне: ${c.title}, ${formatTime(c.seconds!)}`} title={`${formatTime(c.seconds!)} · ${c.title}`}/>)}</div>}
        </div><span>{duration ? formatTime(duration) : '—'}</span><button aria-label="Видео на весь экран" disabled={!media || failed} onClick={() => { const video = player.current; if (!video) return; if (video.requestFullscreen) void video.requestFullscreen().catch(() => setNotice('Полный экран недоступен.')); else (video as HTMLVideoElement & { webkitEnterFullscreen?: () => void }).webkitEnterFullscreen?.(); }}><Maximize size={15}/></button>
      </div>
      {media?.localPreview && <div className="ir-panel-caption">Тестовая запись</div>}
      {excerptBlock}
      {transcriptText && <details className="ir-transcript-full"><summary>Расшифровка</summary><pre>{transcriptText}</pre></details>}
      <p className="ir-seek-status" role="status" aria-live="polite">{notice}</p>
    </div>
    <aside className="ir-panel-aside">{chaptersBlock}{recruiterBlock && <section className="ir-panel-recruiter">{recruiterBlock}</section>}</aside>
  </section>;

  return <section className={`ir-review${compact ? ' ir-review-compact' : ''}`} aria-labelledby={headingId} data-candidate={sourceId} data-recording-kind={kind}>
    <header className="ir-heading"><div>{meeting && <span className="ir-eyebrow">{name}</span>}<h3 id={headingId}>{meeting ? 'Запись вашей встречи' : 'Интервью с рекрутером'}</h3></div><span className="ir-heading-icon"><Video size={19} aria-hidden="true"/></span></header>

    {!meeting && <div className="ir-mode" role="group" aria-label="Режим просмотра интервью">
      <button type="button" aria-pressed={mode === 'short'} onClick={() => changeMode('short')}><ListVideo size={15} aria-hidden="true"/>{meeting ? 'Фрагменты разговора' : 'Короткая версия'}</button>
      <button type="button" aria-pressed={mode === 'full'} onClick={() => changeMode('full')}><Video size={15} aria-hidden="true"/>Полное интервью</button>
    </div>}

    {videoBlock}

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
      {chaptersBlock}

      {excerptBlock}
    </div>
    <p className="ir-seek-status" role="status" aria-live="polite">{notice}</p>

    {transcriptText && <details className="ir-transcript-full"><summary>Вся расшифровка</summary><pre>{transcriptText}</pre></details>}
    {summary && <div className="ir-recruiter"><span className="ir-eyebrow">Заключение рекрутера</span><p>{summary}</p><small>Рекомендация из текущей карточки. Результаты кандидата требуют проверки.</small></div>}
    {unknown && <div className="ir-questions"><MessageSquareText size={18} aria-hidden="true"/><div><h4>Что уточнить на встрече</h4><p>{unknown}</p></div></div>}
  </section>;
}
