import { MessageCircle, RefreshCw } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs';
import { SourceScreeningReview } from '../interview-review/InterviewReview';
import { candidateStatus, type CandidateId, type Journey, type Person } from './api';
import './dsa-candidates.css';
import { Avatar, PeoplePanel } from '../employer-workspace/PeoplePanel';
import { IntakeDetail } from '../employer-workspace/CandidateIntake';
import type { useIntake } from '../employer-workspace/intake';

export function DsaCandidates({ journey, selected, onSelect, onOpen, onAdd, intake, intakeSelected, onIntakeSelect }: {
  journey: Journey; selected?: Person; onSelect: (id: CandidateId) => void;
  onOpen: (page: 'book' | 'meeting' | 'review' | 'chat') => void;
  onAdd: () => void; intake: ReturnType<typeof useIntake>; intakeSelected: string; onIntakeSelect: (id: string) => void;
}) {
  const { state, token, busy } = journey;
  const people = Object.values(state!.candidates) as Person[];
  const person = selected?.sourceProfile;
  const added = intake.people.find(p => p.id === intakeSelected);
  const activeMeeting = selected?.meeting && ['pending', 'confirmed', 'live'].includes(selected.meeting.status);
  const rows = [...people.map(c => ({ id: c.id, name: c.name, photo: c.photo, meta: c.role || '', status: candidateStatus(c) })), ...intake.people.map(p => ({ id: p.id, name: p.name, meta: p.role || 'Новый кандидат', status: p.archived ? 'В архиве' : 'Добавлен вручную', added: true, archived: p.archived }))];
  return <PeoplePanel title={state!.vacancy.role} rows={rows} selected={added?.id || selected?.id || ''} onAdd={onAdd} onSelect={id => { if (intake.people.some(p => p.id === id)) onIntakeSelect(id); else { onIntakeSelect(''); onSelect(id as CandidateId); } }} tools={<Button variant="ghost" size="icon" disabled={busy} onClick={() => { void journey.refresh(); }} aria-label="Обновить кандидатов ДСА"><RefreshCw size={17}/></Button>}>
    {intake.error && <p className="ew-inline-error" role="alert">{intake.error}</p>}
    {added ? <IntakeDetail key={added.id} token={token} person={added} onArchived={() => { void intake.refresh(); }}/>: person && selected ? <div key={person.id} className="dsa-profile ew-dsa-profile">
      <header className="ew-person-heading"><Avatar name={person.name} photo={person.photo}/><div className="ew-person-name"><h2>{person.name}</h2><p>{person.interviewRole || state!.vacancy.role}</p><small>{person.role}</small></div><span className="ew-status">{candidateStatus(selected)}</span><Button variant="ghost" size="icon" onClick={() => onOpen('chat')} aria-label={`Написать ${person.name}`}><MessageCircle size={18}/></Button></header>
      <Tabs defaultValue="interview" className="ew-profile-tabs">
        <div className="ew-profile-toolbar"><TabsList aria-label="Материалы кандидата ДСА"><TabsTrigger value="interview">Интервью</TabsTrigger><TabsTrigger value="cv">Резюме</TabsTrigger></TabsList><div className="ew-person-actions">{activeMeeting ? <Button onClick={() => onOpen('meeting')}>Открыть встречу</Button> : selected.meeting?.status === 'completed' ? <Button onClick={() => onOpen('review')}>{selected.feedback ? 'Разбор встречи' : 'Оставить фидбек'}</Button> : <Button disabled={busy || !!state!.closedBy} onClick={() => onOpen('book')}>Назначить встречу</Button>}</div></div>
        <TabsContent value="interview"><SourceScreeningReview appearance="panel" sourceId={'dsa-' + person.id} name={person.name} chapters={person.shortChapters} fullChapters={person.fullChapters} shortPlaylist media={person.video ? { src: person.video } : undefined} recruiterContent={<><span className="ir-eyebrow">Мнение рекрутера{person.score !== null ? ` · ${person.score} / 5` : ''}</span>{person.conclusion.map((p, i) => <p key={i}>{p}</p>)}{!!person.questions.length && <details className="ew-recruiter-questions"><summary>На встрече · {person.questions.length}</summary><ul>{person.questions.map(q => <li key={q}>{q}</li>)}</ul></details>}</>}/></TabsContent>
        <TabsContent value="cv"><dl className="dsa-facts">{person.profile.map(p => <div key={p.label}><dt>{p.label}</dt><dd>{p.value}</dd></div>)}</dl>{person.cv ? <pre className="dsa-cv">{person.cv}</pre> : <p>Резюме не добавлено.</p>}<p className="ew-form-note">Из панели ДСА · интервью {person.interviewDate}</p></TabsContent>
      </Tabs>
    </div> : undefined}
  </PeoplePanel>;
}
