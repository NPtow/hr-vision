import { useEffect, useState } from 'react';
import { MoreHorizontal } from 'lucide-react';
import { DropdownMenu } from 'radix-ui';
import { Button } from '../../components/ui/button';
import { EmployerScreening } from '../connected-journey/EmployerScreening';
import { Materials } from '../connected-journey/MeetingMaterials';
import { MeetingPanel } from '../connected-journey/MeetingPanel';
import type { Journey, Person, SourceProfile } from '../connected-journey/api';
import { CandidateStageCard } from './CandidateStageCard';
import { CandidateOfferForm, CandidateTestForm } from './CandidateStageForms';
import { MeetingScheduler } from './MeetingScheduler';
import type { CandidateOfferValues, CandidateStage, CandidateStageKind, CandidateStageNotes, CandidateTestValues } from './candidate-stage-types';
import './candidate-stage-workspace.css';

type NextStage = Exclude<CandidateStageKind, 'interview'>;
type Editor = {
  kind: NextStage; stage?: CandidateStage; requestId: string; expectedRevision: number;
  knownIds: string[]; offerVersion?: number; offerStageId?: string; reschedule?: boolean;
};
const queryStage = () => {
  const query = new URLSearchParams(location.search);
  return query.get('stageId') || (query.get('meetingId') ? `meeting:${query.get('meetingId')}` : '');
};

/** The application and canonical stage IDs stay fixed throughout each edit. */
export function CandidateStageWorkspace({ journey, person, source, entry, onChat, onCallChange, callActive, embedded = false }: {
  journey: Journey; person: Person; source: SourceProfile; entry?: 'meeting' | 'review' | 'offer';
  onChat: () => void; onCallChange: (open: boolean) => void; callActive: boolean;
  embedded?: boolean;
}) {
  const Container = embedded ? 'div' : 'main';
  const stages = person.stages || [];
  const [selectedId, setSelectedId] = useState(() => queryStage() ||
    (entry === 'meeting' || entry === 'review' ? [...stages].reverse().find(stage => stage.kind === 'meeting')?.id :
      entry === 'offer' ? [...stages].reverse().find(stage => stage.kind === 'offer')?.id : undefined) || stages.at(-1)?.id || '');
  const [editor, setEditor] = useState<Editor | null>(null);
  const [pendingStage, setPendingStage] = useState<{ kind: NextStage; knownIds: string[]; existingId?: string } | null>(null);
  const [profileOpen, setProfileOpen] = useState(false);
  const locked = !!journey.state!.closedBy || person.decision === 'declined';

  function selectStage(id: string, replace = false) {
    if (callActive) return;
    setPendingStage(null); setSelectedId(id); setEditor(null); setProfileOpen(false); journey.setError('');
    const url = new URL(location.href);
    url.searchParams.set('applicationId', person.id);
    url.searchParams.set('pool', 'selected'); url.searchParams.set('stageId', id);
    url.searchParams.delete('meetingId');
    history[replace ? 'replaceState' : 'pushState'](history.state, '', url);
  }
  useEffect(() => {
    const restore = () => { if (!callActive) { setSelectedId(queryStage() || stages.at(-1)?.id || ''); setEditor(null); setProfileOpen(false); } };
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, [stages, callActive]);
  useEffect(() => {
    const pending = pendingStage;
    if (!pending) return;
    const next = [...stages].reverse().find(stage => stage.kind === pending.kind && !pending.knownIds.includes(stage.id)) ||
      (pending.existingId ? stages.find(stage => stage.id === pending.existingId) : undefined);
    if (next) { setPendingStage(null); selectStage(next.id, true); }
  }, [stages, pendingStage]);

  function openEditor(kind: NextStage, stage?: CandidateStage, reschedule = false) {
    if (journey.busy || callActive || locked) return;
    if (kind === 'meeting' && person.meeting && ['pending', 'confirmed', 'live'].includes(person.meeting.status) && !reschedule) {
      selectStage(`meeting:${person.meeting.id}`);
      journey.setError('Сначала завершите или отмените назначенную встречу. Перенести её можно здесь.');
      return;
    }
    if (stage?.kind === 'offer' && stage.offer?.id !== person.offer?.id) return;
    journey.setError(''); setPendingStage(null); setProfileOpen(false);
    setEditor({ kind, stage, reschedule, requestId: crypto.randomUUID(), expectedRevision: person.workflowRevision || 0,
      offerVersion: person.offer?.version, offerStageId: person.offer?.id ? `offer:${person.offer.id}` : undefined, knownIds: stages.map(item => item.id) });
  }
  function saved(current: Editor, existingId?: string) {
    setPendingStage({ kind: current.kind, knownIds: current.knownIds, existingId });
    setEditor(null);
  }
  async function saveTest(values: CandidateTestValues) {
    if (!editor) return false;
    const current = editor;
    const ok = await journey.act(current.stage ? 'test.update' : 'test.create', person.id, {
      ...values, ...(current.stage?.test ? { testId: current.stage.test.id } : {}),
      requestId: current.requestId, expectedRevision: current.expectedRevision,
    });
    if (ok) saved(current, current.stage?.id);
    return ok;
  }
  async function saveOffer(values: CandidateOfferValues) {
    if (!editor) return false;
    const current = editor;
    const ok = await journey.act('offer.save', person.id, { ...values, version: current.offerVersion ?? 0,
      requestId: current.requestId, expectedRevision: current.expectedRevision });
    if (ok) saved(current, current.stage?.id || current.offerStageId);
    return ok;
  }
  async function saveNotes(stageId: string, values: CandidateStageNotes) {
    const stage = stages.find(item => item.id === stageId);
    if (!stage) return false;
    return journey.act(stage.kind === 'test' ? 'test.update' : 'stage.update', person.id,
      stage.test ? { testId: stage.test.id, note: values.notes, result: values.result, status: values.status, expectedRevision: values.expectedRevision }
        : { stageId, notes: values.notes, expectedRevision: values.expectedRevision });
  }
  function meetingMaterials(stage: CandidateStage) {
    const meeting = stage.meeting || person.meetings?.find(item => item.id === stage.meetingId);
    if (!meeting) return <p className="esc-empty">Материалы этой встречи пока недоступны.</p>;
    const historical = { ...person, meeting, feedback: stage.feedback };
    if (meeting.status === 'completed') return <Materials journey={journey} person={historical}/>;
    if (meeting.id !== person.meeting?.id || meeting.status === 'cancelled') return null;
    return <MeetingPanel key={meeting.id} journey={journey} person={historical} onCallChange={onCallChange}
      onReschedule={() => openEditor('meeting', stage, true)}/>;
  }
  function offerActions(stage: CandidateStage) {
    const offer = stage.offer;
    if (!offer || offer.id !== person.offer?.id || offer.status !== 'draft' || locked) return null;
    const active = Object.values(journey.state!.candidates).find(candidate => candidate?.id !== person.id && candidate?.offer?.status === 'sent');
    return <div className="esc-stage-actions"><Button disabled={journey.busy || !!active || person.afterInterest === 'no'}
      onClick={() => { void journey.act('offer.send', person.id, { version: offer.version, expectedRevision: person.workflowRevision || 0 }); }}>Отправить оффер</Button>
      {active && <p className="esc-caption">Ждём ответ на оффер от {active.name}.</p>}
      {person.afterInterest === 'no' && <p className="esc-caption">Кандидат не хочет продолжать. Обсудите это в чате.</p>}
    </div>;
  }
  const cancel = () => { setEditor(null); journey.setError(''); };
  let form;
  if (editor?.kind === 'meeting') form = <MeetingScheduler key={editor.requestId} embedded newMeeting={!editor.reschedule}
    command={{ requestId: editor.requestId, expectedRevision: editor.expectedRevision }} journey={journey} person={person}
    onSelect={() => {}} onBack={cancel} onBooked={() => saved(editor, editor.stage?.id)}/>;
  if (editor?.kind === 'test') form = <CandidateTestForm key={editor.requestId} editing={!!editor.stage}
    initialValues={editor.stage?.test ? { ...editor.stage.test, dueAt: editor.stage.test.dueAt || '', documents: editor.stage.documents } : undefined}
    onSubmit={saveTest} onCancel={cancel} busy={journey.busy} error={journey.error}/>;
  if (editor?.kind === 'offer') form = <CandidateOfferForm key={editor.requestId}
    initialValues={editor.stage?.offer ? { ...editor.stage.offer, documents: editor.stage.documents } : {
      role: journey.state!.vacancy.role, compensation: journey.state!.vacancy.salary,
      format: journey.state!.vacancy.format, expectations: journey.state!.vacancy.expectations,
    }} onSubmit={saveOffer} onCancel={cancel} busy={journey.busy} error={journey.error}/>;

  if (profileOpen) form = <section className="esc-materials"><h3>Профиль</h3>{source.profile.map((fact, index) => <p key={index}><strong>{fact.label}</strong><br/>{fact.value}</p>)}{source.cv && <p>{source.cv}</p>}<Button variant="ghost" onClick={() => setProfileOpen(false)}>К этапам</Button></section>;

  if (selectedId && !stages.some(stage => stage.id === selectedId)) return <Container className="esc-workspace"><section className="esc-card esc-unavailable">
    <h2>Этап недоступен</h2><p>Этого этапа нет в истории выбранного кандидата.</p>
    {stages.length > 0 && <Button onClick={() => selectStage(stages.at(-1)!.id, true)}>Открыть текущий этап</Button>}
  </section></Container>;

  return <Container className="esc-workspace"><CandidateStageCard
    person={{ id: person.id, name: person.name, photo: person.photo, role: person.role, meta: [source.interviewRole || person.role, source.role].filter(Boolean).join(' · ') }}
    stages={stages} selectedStageId={selectedId} workflowRevision={person.workflowRevision || 0}
    onSelectStage={selectStage} onAddStage={locked ? undefined : kind => openEditor(kind)}
    onEditStage={locked ? undefined : stage => { if (stage.kind === 'offer' || stage.kind === 'test') openEditor(stage.kind, stage); }}
    onSaveStageNotes={locked ? undefined : saveNotes} busy={journey.busy || callActive} error={editor ? undefined : journey.error} editor={form}
    renderInterview={() => <><EmployerScreening journey={journey} person={person} source={source}/>
      {(source.cv || source.profile.length > 0) && <details className="esc-resume"><summary>Резюме</summary>
        {source.profile.map((fact, index) => <p key={index}><strong>{fact.label}</strong><br/>{fact.value}</p>)}{source.cv && <p>{source.cv}</p>}
      </details>}</>}
    renderMeeting={meetingMaterials} renderOffer={offerActions}
    headerActions={<DropdownMenu.Root><DropdownMenu.Trigger asChild><Button variant="ghost" size="icon" aria-label="Действия с кандидатом" disabled={journey.busy}><MoreHorizontal size={20}/></Button></DropdownMenu.Trigger>
      <DropdownMenu.Portal><DropdownMenu.Content className="esc-menu" align="end" sideOffset={8}>
        <DropdownMenu.Item className="esc-menu-item" onSelect={onChat}>Открыть чат</DropdownMenu.Item>
        {!stages.some(stage => stage.kind === 'interview') && (source.cv || source.profile.length > 0) && <DropdownMenu.Item className="esc-menu-item" onSelect={() => { setEditor(null); setProfileOpen(true); }}>Профиль</DropdownMenu.Item>}
        {!locked && <DropdownMenu.Item className="esc-menu-item" onSelect={() => { void journey.act('reject', person.id, { expectedRevision: person.workflowRevision || 0 }); }}>Отказать</DropdownMenu.Item>}
      </DropdownMenu.Content></DropdownMenu.Portal></DropdownMenu.Root>}
  /></Container>;
}
