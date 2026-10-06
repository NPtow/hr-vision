import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, ChevronRight, RefreshCw } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs';
import { SourceScreeningReview, type RecordingChapter } from '../interview-review/InterviewReview';
import { request } from './api';
import './dsa-candidates.css';

type DsaCandidate = {
  id: string; name: string; initials: string; role: string; interviewRole: string; interviewDate: string;
  score: number | null; duration: number | null; photo: string | null; video: string | null;
  shortChapters: RecordingChapter[]; fullChapters: RecordingChapter[]; shortSummary: string;
  profile: { label: string; value: string }[]; cv: string; conclusion: string[]; questions: string[];
  sourceStatus: string; sourceUpdatedAt: string | null;
};
type DsaSelection = { source: string; company: string; role: string; selection: string; candidates: DsaCandidate[]; fetchedAt: number };

function Avatar({ person }: { person: DsaCandidate }) {
  const [failed, setFailed] = useState(false);
  return <span className="cj-avatar dsa-avatar">{person.photo && !failed ? <img src={person.photo} alt="" onError={() => setFailed(true)}/> : person.initials}</span>;
}

export function DsaCandidates({ token, onBack }: { token: string; onBack: () => void }) {
  const [selection, setSelection] = useState<DsaSelection | null>(null);
  const [selected, setSelected] = useState('');
  const [mobileDetail, setMobileDetail] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const sequence = useRef(0);
  const load = useCallback(async () => {
    const seq = ++sequence.current;
    setLoading(true); setError('');
    try {
      const data = await request<DsaSelection>('dsa-candidates', token);
      if (sequence.current !== seq) return;
      setSelection(data);
      setSelected(id => data.candidates.some(c => c.id === id) ? id : data.candidates[0]?.id || '');
    } catch (e) { if (sequence.current === seq) setError((e as Error).message); }
    finally { if (sequence.current === seq) setLoading(false); }
  }, [token]);
  useEffect(() => { void load(); return () => { sequence.current++; }; }, [load]);
  const person = selection?.candidates.find(c => c.id === selected);
  return <section className="dsa-selection">
    <div className="cj-context"><button onClick={onBack}>Задачи найма</button><ChevronRight size={14}/><strong>ДСА Инжиниринг · {selection?.role || 'Подборка кандидатов'}</strong><Button variant="ghost" size="sm" disabled={loading} onClick={() => { void load(); }} aria-label="Обновить кандидатов ДСА"><RefreshCw size={15}/></Button></div>
    {error && <div className="dsa-load-error" role="alert"><span>{error}</span><Button variant="outline" disabled={loading} onClick={() => { void load(); }}>Попробовать снова</Button></div>}
    {loading && !selection && <main className="cj-main"><p role="status">Загружаем кандидатов из панели ДСА…</p></main>}
    {selection && !selection.candidates.length && <main className="cj-empty"><h1>В панели ДСА пока нет доступных кандидатов</h1></main>}
    {person && selection && <div className={`cj-shortlist dsa-shortlist${mobileDetail ? ' cj-mobile-detail' : ''}`}>
      <aside className="cj-people"><h2>Кандидаты ДСА <span>{selection.candidates.length}</span></h2>
        {selection.candidates.map(c => <button type="button" key={c.id} className="cj-person" aria-pressed={c.id === person.id} onClick={() => { setSelected(c.id); setMobileDetail(true); }}>
          <div><Avatar person={c}/><strong>{c.name}</strong></div><p>{c.role}</p><small>В ДСА: {c.sourceStatus || 'Решение не указано'}</small>
        </button>)}
        <a className="dsa-source-link" href={selection.source} target="_blank" rel="noreferrer">Открыть исходную панель ↗</a>
      </aside>
      <main className="cj-profile dsa-profile" key={person.id}>
        <button type="button" className="cj-mobile-back" onClick={() => setMobileDetail(false)}><ArrowLeft size={15}/>К кандидатам</button>
        <div className="cj-section-heading"><div><h1>{person.name}</h1><p>{person.role}</p><small>Интервью {person.interviewDate} · ДСА Инжиниринг</small></div><span className="cj-badge">В ДСА: {person.sourceStatus || 'Без решения'}</span></div>
        <Tabs defaultValue="interview">
          <TabsList aria-label="Материалы кандидата ДСА"><TabsTrigger value="interview">Интервью</TabsTrigger><TabsTrigger value="profile">Профиль</TabsTrigger><TabsTrigger value="cv">Резюме</TabsTrigger></TabsList>
          <TabsContent value="interview">
            <SourceScreeningReview sourceId={'dsa-' + person.id} name={person.name} chapters={person.shortChapters} fullChapters={person.fullChapters} shortPlaylist media={person.video ? { src: person.video } : undefined}/>
            <section className="dsa-recruiter"><div className="dsa-section-title"><h2>Заключение рекрутера</h2>{person.score !== null && <span>Оценка в ДСА <strong>{person.score} / 5</strong></span>}</div>{person.conclusion.map((p, i) => <p key={i}>{p}</p>)}</section>
            {!!person.questions.length && <details className="dsa-questions"><summary>Что уточнить на встрече · {person.questions.length}</summary><ul>{person.questions.map(q => <li key={q}>{q}</li>)}</ul></details>}
          </TabsContent>
          <TabsContent value="profile"><dl className="dsa-facts">{person.profile.map(p => <div key={p.label}><dt>{p.label}</dt><dd>{p.value}</dd></div>)}</dl><p className="cj-source-note">Профиль из панели ДСА на момент интервью {person.interviewDate}.</p></TabsContent>
          <TabsContent value="cv">{person.cv ? <pre className="dsa-cv">{person.cv}</pre> : <p>Резюме в исходной панели не добавлено.</p>}</TabsContent>
        </Tabs>
      </main>
    </div>}
  </section>;
}
