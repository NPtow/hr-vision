import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUpRight } from 'lucide-react';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import { EmployerCandidateProfile } from '../employer-workspace/EmployerCandidateProfile';
import type { EmployerCandidateResume } from '../employer-workspace/employer-candidate-profile-types';
import { SourceScreeningReview } from '../interview-review/InterviewReview';
import { parseMeetingTranscript } from '../connected-journey/transcript';
import type { AssessmentPresentation } from '../interview-assessment/types';
import type { InterviewAnswer, InterviewSearch, InterviewAssistantRequest } from '../interview-review/interview-assistant-api';
import './public-candidate-profile.css';

type Profile = { id: string; name: string; role: string; resume: { name: string; mime: string; size: number } | null };
type Materials = {
  recordings: { id: string; url: string; expires?: number }[];
  transcripts: { id: string; assetId?: string; text: string }[];
  videoStatus: string; transcriptStatus: string;
};
class ShareError extends Error {
  constructor(message: string, public status = 0) { super(message); }
}

// No account cookies, persistent browser storage or tokens in request URLs.
async function request<T>(token: string, suffix: string, signal: AbortSignal, values: Record<string, unknown> = {}, timeoutMs = 45_000): Promise<T> {
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), timeoutMs);
  const abort = () => timeout.abort();
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) abort();
  try {
    const response = await fetch(`/api/hr/public-profile${suffix}`, {
      method: 'POST', credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...values, token }), signal: timeout.signal,
    });
    const body = await response.json();
    if (!response.ok) throw new ShareError(body.error || 'Не удалось открыть профиль.', response.status);
    return body as T;
  } catch (cause) {
    if (cause instanceof ShareError) throw cause;
    throw new ShareError('Не удалось загрузить. Попробуйте ещё раз.');
  } finally {
    clearTimeout(timer); signal.removeEventListener('abort', abort);
  }
}

export function PublicCandidateProfile() {
  const [token, setToken] = useState(() => new URLSearchParams(location.hash.slice(1)).get('profile') || '');
  useEffect(() => {
    const change = () => setToken(new URLSearchParams(location.hash.slice(1)).get('profile') || '');
    window.addEventListener('hashchange', change);
    const title = document.title;
    const tags = [{ name: 'robots', content: 'noindex, nofollow, noarchive' }, { name: 'referrer', content: 'no-referrer' }].map(attributes => {
      const tag = document.createElement('meta'); Object.assign(tag, attributes); document.head.appendChild(tag); return tag;
    });
    document.title = 'Профиль кандидата · HR Vision';
    return () => { window.removeEventListener('hashchange', change); tags.forEach(tag => tag.remove()); document.title = title; };
  }, []);
  return <main className="public-candidate-page">
    <header className="public-candidate-header"><img src={logo} alt="HR Vision"/>
      <a href="https://mashadasha.tech/" referrerPolicy="no-referrer">На сайт HR Vision<ArrowUpRight size={18}/></a>
    </header>
    <PublicProfileCard key={token} token={token}/>
  </main>;
}

function PublicProfileCard({ token }: { token: string }) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [materials, setMaterials] = useState<Materials | null>(null);
  const [error, setError] = useState('');
  const [materialError, setMaterialError] = useState('');
  const [closed, setClosed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const materialsRef = useRef<Materials | null>(null);
  const card = useRef<HTMLElement>(null);
  const [recordingId, setRecordingId] = useState('');
  const [assessment, setAssessment] = useState<AssessmentPresentation>();
  const [assessmentError, setAssessmentError] = useState('');
  const [assessmentLoaded, setAssessmentLoaded] = useState(false);
  const assistant = useMemo<InterviewAssistantRequest>(() => async (action, query, requestId, signal) => {
    const result = await request<InterviewAnswer | InterviewSearch>(token, '/assistant', signal, { action, query, requestId }, 150_000);
    if (result.mode !== action || (result.mode === 'chat' ? !Array.isArray(result.evidence) : !Array.isArray(result.matches))) throw new Error('Не удалось прочитать ответ. Попробуйте ещё раз.');
    return result;
  }, [token]);
  useEffect(() => {
    if (!/^[A-Za-z0-9_-]{43}$/.test(token)) return;
    const controller = new AbortController();
    void request<{ assessment: Omit<AssessmentPresentation, 'saveFeedback'> | null }>(token, '/assessment', controller.signal)
      .then(result => {
        if (controller.signal.aborted) return;
        setAssessmentError('');
        setAssessment(result.assessment ? { ...result.assessment, saveFeedback: async () => { throw new Error('Ответы по фрагментам в публичном просмотре отключены.'); } } : undefined);
      }).catch(cause => { if (!controller.signal.aborted) setAssessmentError((cause as Error).message); })
      .finally(() => { if (!controller.signal.aborted) setAssessmentLoaded(true); });
    return () => controller.abort();
  }, [token, attempt]);
  useEffect(() => {
    if (!/^[A-Za-z0-9_-]{43}$/.test(token)) { setClosed(true); setError('Ссылка недействительна.'); return; }
    const controller = new AbortController();
    let pending = false;
    let revoked = false;
    function fail(cause: unknown, media = false) {
      if (controller.signal.aborted) return;
      if (cause instanceof ShareError && [404, 410].includes(cause.status)) {
        revoked = true; setClosed(true); setProfile(null); setMaterials(null); materialsRef.current = null; setError('Ссылка закрыта.');
      } else if (media) setMaterialError((cause as Error).message);
      else setError((cause as Error).message);
    }
    async function refresh() {
      if (pending || revoked || controller.signal.aborted) return;
      pending = true;
      try {
        const next = await request<Profile>(token, '', controller.signal);
        if (controller.signal.aborted) return;
        setProfile(next); setError('');
        const current = materialsRef.current;
        const needsRefresh = !current || !current.recordings.length || !current.transcripts.length || current.recordings.some(item => item.expires && item.expires * 1000 < Date.now() + 120_000);
        if (needsRefresh) {
          try {
            const loaded = await request<Materials>(token, '/materials', controller.signal);
            if (!controller.signal.aborted) {
              // Storage URLs expire after five minutes. Refreshing the same
              // recording must retain the listener's position and playing state.
              const player = card.current?.querySelector('video');
              if (player && loaded.recordings.some(item => item.url !== player.getAttribute('src') && current?.recordings.some(old => old.id === item.id && old.url === player.getAttribute('src')))) {
                const at = player.currentTime, resume = !player.paused;
                player.addEventListener('loadedmetadata', () => {
                  if (!player.isConnected) return;
                  if (Number.isFinite(player.duration)) player.currentTime = Math.min(at, Math.max(0, player.duration - .01));
                  if (resume) void player.play().catch(() => { /* Native playback controls remain available. */ });
                }, { once: true, signal: controller.signal });
              }
              materialsRef.current = loaded; setMaterials(loaded); setMaterialError('');
            }
          } catch (cause) { fail(cause, true); }
        }
      } catch (cause) { fail(cause); }
      finally { pending = false; }
    }
    void refresh();
    const interval = setInterval(() => { if (document.visibilityState === 'visible') void refresh(); }, 60_000);
    const focus = () => { void refresh(); };
    window.addEventListener('focus', focus);
    return () => { controller.abort(); clearInterval(interval); window.removeEventListener('focus', focus); };
  }, [token, attempt]);
  const resume = useMemo<EmployerCandidateResume | undefined>(() => profile?.resume ? {
    id: profile.id, name: profile.resume.name,
    load: async signal => {
      const result = await request<{ mime: string; contentBase64: string }>(token, '/resume', signal);
      return new Blob([Uint8Array.from(atob(result.contentBase64), char => char.charCodeAt(0))], { type: result.mime });
    },
  } : undefined, [token, profile?.id, profile?.resume?.name]);
  const recording = materials?.recordings.find(item => item.id === recordingId) || materials?.recordings[0];
  const transcript = materials?.transcripts[0]?.text || '';
  const chapters = useMemo(() => parseMeetingTranscript(transcript), [transcript]);
  const retry = () => { materialsRef.current = null; setError(''); setMaterialError(''); setAttempt(value => value + 1); };
  if (!profile) return <section className="public-candidate-state" aria-live="polite">
    {error ? <><p>{error}</p>{!closed && <button type="button" onClick={retry}>Попробовать снова</button>}</> : <><span className="public-candidate-spinner" aria-hidden="true"/><p>Открываем профиль…</p></>}
  </section>;
  return <section ref={card} className="public-candidate-card" aria-label="Профиль кандидата">
    {materials ? <>
      {materials.recordings.length > 1 && <div className="public-candidate-recordings">{materials.recordings.map((item, index) =>
        <button type="button" key={item.id} aria-pressed={recording?.id === item.id} onClick={() => setRecordingId(item.id)}>Запись {index + 1}</button>)}</div>}
      <EmployerCandidateProfile person={profile} source={{}} resume={resume} workflowRevision={0}
        interview={header => <SourceScreeningReview sourceId={`${profile.id}:${recording?.id || 'transcript'}`} name={profile.name} header={header}
          appearance="candidate" hideFeedbackQuestions assessment={assessment}
          assessmentStatus={!assessmentLoaded ? 'loading' : !assessment ? 'unavailable' : undefined}
          assistant={transcript ? assistant : undefined} chapters={[]} fullChapters={chapters} transcriptText={transcript}
          transcriptSeekable={materials.recordings.length === 1 && materials.transcripts.length === 1}
          media={recording ? { src: recording.url, label: 'Интервью' } : undefined}
          emptyMessage="Запись пока недоступна."/>}/>
    </> : <div className="public-candidate-state"><h1>{profile.name}</h1>{!materialError && <><span className="public-candidate-spinner" aria-hidden="true"/><p>Открываем интервью…</p></>}</div>}
    {(error || materialError) && <div className="public-candidate-state" role="alert"><p>{error || materialError}</p><button type="button" onClick={retry}>Попробовать снова</button></div>}
    {assessmentError && <div className="public-candidate-state" role="alert"><p>{assessmentError}</p><button type="button" onClick={retry}>Повторить загрузку аналитики</button></div>}
  </section>;
}
