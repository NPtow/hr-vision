import { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs';
import { SourceScreeningReview, type RecordingChapter } from '../interview-review/InterviewReview';
import { request } from './api';
import './dsa-candidates.css';
import { PeoplePanel } from '../employer-workspace/PeoplePanel';
import { IntakeDetail } from '../employer-workspace/CandidateIntake';
import type { useIntake } from '../employer-workspace/intake';

type DsaCandidate = {
  id: string; name: string; initials: string; role: string; interviewRole: string; interviewDate: string;
  score: number | null; duration: number | null; photo: string | null; video: string | null;
  shortChapters: RecordingChapter[]; fullChapters: RecordingChapter[]; shortSummary: string;
  profile: { label: string; value: string }[]; cv: string; conclusion: string[]; questions: string[];
  sourceStatus: string; sourceUpdatedAt: string | null;
};
type DsaSelection = { source: string; company: string; role: string; selection: string; candidates: DsaCandidate[]; fetchedAt: number };

export function DsaCandidates({ token, onAdd, intake, intakeSelected, onIntakeSelect }: { token: string; onBack: () => void; onAdd: () => void; intake: ReturnType<typeof useIntake>; intakeSelected: string; onIntakeSelect: (id: string) => void }) {
  const [selection, setSelection] = useState<DsaSelection | null>(null);
  const [selected, setSelected] = useState('');
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
  const added = intake.people.find(p => p.id === intakeSelected);
  const rows = [...(selection?.candidates || []).map(c => ({ id: c.id, name: c.name, photo: c.photo, meta: c.role, status: c.sourceStatus || 'Без решения' })), ...intake.people.map(p => ({ id: p.id, name: p.name, meta: p.role || 'Новый кандидат', status: p.archived ? 'В архиве' : 'Добавлен вручную', added: true, archived: p.archived }))];
  return <PeoplePanel title={`ДСА Инжиниринг · ${selection?.role || 'Менеджер по работе с клиентами'}`} rows={rows} selected={added?.id || person?.id || ''} onAdd={onAdd} onSelect={id => { if (intake.people.some(p => p.id === id)) onIntakeSelect(id); else { onIntakeSelect(''); setSelected(id); } }} tools={<Button variant="ghost" size="icon" disabled={loading} onClick={() => { void load(); }} aria-label="Обновить кандидатов ДСА"><RefreshCw size={17}/></Button>}>
    {error && <div className="ew-inline-error" role="alert"><span>{error}</span><Button variant="outline" disabled={loading} onClick={() => { void load(); }}>Попробовать снова</Button></div>}
    {intake.error && <p className="ew-inline-error" role="alert">{intake.error}</p>}
    {loading && !selection && <p role="status">Загружаем кандидатов…</p>}
    {added ? <IntakeDetail key={added.id} token={token} person={added} onArchived={() => { void intake.refresh(); }}/>: person && selection ? <div key={person.id} className="dsa-profile ew-dsa-profile">
      <header className="ew-person-heading"><div><span className="ew-status">{person.sourceStatus || 'Без решения'}</span><h2>{person.name}</h2><p>{person.role}</p></div><a className="ew-source-button" href={selection.source} target="_blank" rel="noreferrer">Исходная панель ↗</a></header>
      <Tabs defaultValue="interview" className="ew-profile-tabs"><TabsList aria-label="Материалы кандидата ДСА"><TabsTrigger value="interview">Интервью</TabsTrigger><TabsTrigger value="profile">Профиль</TabsTrigger><TabsTrigger value="cv">Резюме</TabsTrigger></TabsList>
        <TabsContent value="interview"><SourceScreeningReview sourceId={'dsa-' + person.id} name={person.name} chapters={person.shortChapters} fullChapters={person.fullChapters} shortPlaylist media={person.video ? { src: person.video } : undefined}/><section className="dsa-recruiter"><div className="dsa-section-title"><h2>Заключение рекрутера</h2>{person.score !== null && <span>Оценка <strong>{person.score} / 5</strong></span>}</div>{person.conclusion.map((p, i) => <p key={i}>{p}</p>)}</section>{!!person.questions.length && <details className="dsa-questions"><summary>Что уточнить на встрече</summary><ul>{person.questions.map(q => <li key={q}>{q}</li>)}</ul></details>}</TabsContent>
        <TabsContent value="profile"><dl className="dsa-facts">{person.profile.map(p => <div key={p.label}><dt>{p.label}</dt><dd>{p.value}</dd></div>)}</dl><p className="ew-form-note">Из панели ДСА · интервью {person.interviewDate}</p></TabsContent>
        <TabsContent value="cv">{person.cv ? <pre className="dsa-cv">{person.cv}</pre> : <p>Резюме не добавлено.</p>}</TabsContent>
      </Tabs>
    </div> : undefined}
  </PeoplePanel>;
}
