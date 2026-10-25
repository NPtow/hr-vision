import { useCallback, useEffect, useState } from 'react';
import { MoreHorizontal } from 'lucide-react';
import { DropdownMenu } from 'radix-ui';
import { Button } from '../../components/ui/button';
import { EmployerScreening } from '../connected-journey/EmployerScreening';
import { Materials } from '../connected-journey/MeetingMaterials';
import { MeetingPanel } from '../connected-journey/MeetingPanel';
import type { Journey, Person, SourceProfile } from '../connected-journey/api';
import { CandidateStageCard, MeetingFeedback, StageDocuments, StageNotes } from './CandidateStageCard';
import { CandidateNextStep, CandidateStageRejection } from './CandidateNextStep';
import { CandidateProfilePanel } from './CandidateProfilePanel';
import { CandidateOfferForm } from './CandidateStageForms';
import { MeetingScheduler } from './MeetingScheduler';
import type { CandidateOfferValues, CandidateStage, CandidateStageKind, CandidateStageNotes } from './candidate-stage-types';
import './candidate-stage-workspace.css';

type NextStage = Exclude<CandidateStageKind, 'interview' | 'test'>;
type Editor = {
  kind: NextStage; stage?: CandidateStage; requestId: string; expectedRevision: number;
  knownIds: string[]; offerVersion?: number; offerStageId?: string; reschedule?: boolean;
};
const queryStage = () => {
  const query = new URLSearchParams(location.search);
  return query.get('stageId') || (query.get('meetingId') ? `meeting:${query.get('meetingId')}` : '');
};

/** The application and canonical stage IDs stay fixed throughout each edit. */
export function CandidateStageWorkspace({ journey, person, source, entry, onChat, onCallChange, callActive }: {
  journey: Journey; person: Person; source: SourceProfile; entry?: 'meeting' | 'review' | 'offer';
  onChat: () => void; onCallChange: (open: boolean) => void; callActive: boolean;
}) {
  const allStages = person.stages || [];
  const stages = allStages.filter(stage => stage.kind !== 'test');
  const archivedStageIds = [...(person.archivedStageIds || []), ...allStages.filter(stage => stage.kind === 'test').map(stage => stage.id)];
  const visibleStage = (id: string) => archivedStageIds.includes(id) ? stages.at(-1)?.id || '' : id;
  const [selectedId, setSelectedId] = useState(() => visibleStage(queryStage()) ||
    (entry === 'meeting' || entry === 'review' ? [...stages].reverse().find(stage => stage.kind === 'meeting')?.id :
      entry === 'offer' ? [...stages].reverse().find(stage => stage.kind === 'offer')?.id : undefined) || stages.at(-1)?.id || '');
  const [editor, setEditor] = useState<Editor | null>(null);
  const [view, setView] = useState<'stage' | 'next' | 'reject' | 'notes'>('stage');
  const [completedMeeting, setCompletedMeeting] = useState<string | null>(null);
  const [pendingStage, setPendingStage] = useState<{ kind: NextStage; knownIds: string[]; existingId?: string } | null>(null);
  const [profileOpen, setProfileOpen] = useState(() => new URLSearchParams(location.search).get('view') === 'profile');
  const locked = !!journey.state!.closedBy || person.decision === 'declined' || person.offer?.status === 'accepted';
  const selectedStage = stages.find(stage => stage.id === selectedId);
  const activeMeeting = person.meeting && ['pending', 'confirmed', 'live'].includes(person.meeting.status);
  const handleCallChange = useCallback((open: boolean) => {
    if (open && person.meeting?.id) setCompletedMeeting(person.meeting.id);
    onCallChange(open);
  }, [onCallChange, person.meeting?.id]);

  function selectStage(id: string, replace = false) {
    if (callActive) return;
    setPendingStage(null); setCompletedMeeting(null); setSelectedId(id); setEditor(null); setView('stage'); setProfileOpen(false); journey.setError('');
    const url = new URL(location.href);
    url.searchParams.set('applicationId', person.id);
    url.searchParams.set('pool', 'selected'); url.searchParams.set('stageId', id);
    url.searchParams.delete('meetingId'); url.searchParams.delete('view');
    history[replace ? 'replaceState' : 'pushState'](history.state, '', url);
  }
  function openProfile() {
    if (journey.busy || callActive || profileOpen) return;
    setPendingStage(null); setCompletedMeeting(null); setEditor(null); setView('stage'); setProfileOpen(true); journey.setError('');
    const url = new URL(location.href);
    url.searchParams.set('applicationId', person.id); url.searchParams.set('pool', 'selected');
    if (selectedId) url.searchParams.set('stageId', selectedId);
    url.searchParams.set('view', 'profile');
    history.pushState(history.state, '', url);
  }
  // Old test links retain application identity; only known archived IDs may fall back.
  useEffect(() => {
    const requested = queryStage();
    if (!requested || !archivedStageIds.includes(requested)) return;
    const next = stages.at(-1)?.id || '';
    setSelectedId(next);
    const url = new URL(location.href);
    if (next) url.searchParams.set('stageId', next); else url.searchParams.delete('stageId');
    url.searchParams.delete('meetingId');
    history.replaceState(history.state, '', url);
  }, [person.id, allStages, person.archivedStageIds]);
  useEffect(() => {
    const restore = () => { if (!callActive) { setCompletedMeeting(null); setSelectedId(visibleStage(queryStage()) || stages.at(-1)?.id || ''); setEditor(null); setView('stage'); setProfileOpen(new URLSearchParams(location.search).get('view') === 'profile'); } };
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, [allStages, person.archivedStageIds, callActive]);
  useEffect(() => {
    const pending = pendingStage;
    if (!pending) return;
    const next = [...stages].reverse().find(stage => stage.kind === pending.kind && !pending.knownIds.includes(stage.id)) ||
      (pending.existingId ? stages.find(stage => stage.id === pending.existingId) : undefined);
    if (next) { setPendingStage(null); selectStage(next.id, true); }
  }, [stages, pendingStage]);

  // Remember calls opened here: Zoom may refresh the canonical state before its
  // reconcile callback fires. Wait for completion and for the overlay to release
  // navigation. Historical meetings never auto-open this view.
  useEffect(() => {
    if (!completedMeeting || callActive || locked) return;
    if (!stages.some(stage => stage.meetingId === completedMeeting && stage.status === 'completed')) return;
    setCompletedMeeting(null); setEditor(null); setProfileOpen(false); setView('next');
  }, [completedMeeting, callActive, locked, stages]);

  function openNextStep() {
    if (journey.busy || callActive || locked) return;
    setEditor(null); setProfileOpen(false); setPendingStage(null); setView('next'); journey.setError('');
  }
  function openRejection() {
    if (journey.busy || callActive || locked) return;
    if (activeMeeting) {
      selectStage(`meeting:${person.meeting!.id}`);
      journey.setError('Сначала завершите или отмените назначенную встречу.'); return;
    }
    setEditor(null); setProfileOpen(false); setView('reject'); journey.setError('');
  }

  function openEditor(kind: NextStage, stage?: CandidateStage, reschedule = false) {
    if (journey.busy || callActive || locked) return;
    if (activeMeeting && !reschedule) {
      selectStage(`meeting:${person.meeting!.id}`);
      if (kind === 'offer') journey.setError('Сначала завершите или отмените назначенную встречу.');
      return;
    }
    if (kind === 'offer' && !stage && person.offer) { selectStage(`offer:${person.offer.id}`); return; }
    if (stage?.kind === 'offer' && stage.offer?.id !== person.offer?.id) return;
    journey.setError(''); setPendingStage(null); setProfileOpen(false); setView('stage');
    setEditor({ kind, stage, reschedule, requestId: crypto.randomUUID(), expectedRevision: person.workflowRevision || 0,
      offerVersion: person.offer?.version, offerStageId: person.offer?.id ? `offer:${person.offer.id}` : undefined, knownIds: stages.map(item => item.id) });
  }
  function saved(current: Editor, existingId?: string) {
    setPendingStage({ kind: current.kind, knownIds: current.knownIds, existingId });
    setEditor(null);
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
    return journey.act('stage.update', person.id, { stageId, notes: values.notes, expectedRevision: values.expectedRevision });
  }
  function meetingMaterials(stage: CandidateStage) {
    const meeting = stage.meeting || person.meetings?.find(item => item.id === stage.meetingId);
    if (!meeting) return <p className="esc-empty">Материалы этой встречи пока недоступны.</p>;
    const historical = { ...person, meeting, feedback: stage.feedback };
    if (meeting.status === 'completed') return <div className="esc-interview esc-meeting-review" data-stage-id={stage.id}><Materials journey={journey} person={historical} appearance="candidate" enableComments/></div>;
    if (meeting.id !== person.meeting?.id || meeting.status === 'cancelled') return null;
    return <MeetingPanel key={meeting.id} compact journey={journey} person={historical} onCallChange={handleCallChange}
      onComplete={() => setCompletedMeeting(meeting.id)}
      onReschedule={() => openEditor('meeting', stage, true)}/>;
  }
  function offerActions(stage: CandidateStage) {
    const offer = stage.offer;
    if (!offer || offer.id !== person.offer?.id || offer.status !== 'draft' || locked) return null;
    const active = Object.values(journey.state!.candidates).find(candidate => candidate?.id !== person.id && candidate?.offer?.status === 'sent');
    return <div className="esc-stage-actions"><Button disabled={journey.busy || !!active || !!activeMeeting || person.afterInterest === 'no'}
      onClick={() => { void journey.act('offer.send', person.id, { version: offer.version, expectedRevision: person.workflowRevision || 0 }); }}>Отправить оффер</Button>
      {active && <p className="esc-caption">Ждём ответ на оффер от {active.name}.</p>}
      {person.afterInterest === 'no' && <p className="esc-caption">Кандидат не хочет продолжать. Обсудите это в чате.</p>}
    </div>;
  }
  const cancel = () => { setEditor(null); setView('next'); journey.setError(''); };
  let form;
  if (view === 'next') form = <CandidateNextStep busy={journey.busy || locked} onMeeting={() => openEditor('meeting')} onOffer={() => openEditor('offer')} onReject={openRejection}/>;
  if (view === 'reject') form = <CandidateStageRejection key={person.id} journey={journey} person={person} onCancel={cancel} onSaved={() => selectStage(selectedId)}/>;
  if (view === 'notes' && selectedStage) form = <section className="esc-meeting-notes" aria-label="Заметки и фидбек">
    <StageDocuments stage={selectedStage}/>
    <StageNotes key={selectedStage.id} stage={selectedStage} workflowRevision={person.workflowRevision || 0} busy={journey.busy} onSave={locked ? undefined : saveNotes}/>
    <MeetingFeedback stage={selectedStage}/>
    {!selectedStage.feedback && <p className="esc-caption">Фидбек по этой встрече ещё не сохранён.</p>}
    <Button variant="ghost" onClick={() => selectStage(selectedId)}>К встрече</Button>
  </section>;
  if (editor?.kind === 'meeting') form = <MeetingScheduler key={editor.requestId} embedded newMeeting={!editor.reschedule}
    command={{ requestId: editor.requestId, expectedRevision: editor.expectedRevision }} journey={journey} person={person}
    onSelect={() => {}} onBack={cancel} onBooked={() => saved(editor, editor.stage?.id)}/>;
  if (editor?.kind === 'offer') form = <CandidateOfferForm key={editor.requestId}
    initialValues={editor.stage?.offer ? { ...editor.stage.offer, documents: editor.stage.documents } : {
      role: journey.state!.vacancy.role, compensation: journey.state!.vacancy.salary,
      format: journey.state!.vacancy.format, expectations: journey.state!.vacancy.expectations,
    }} onSubmit={saveOffer} onCancel={cancel} busy={journey.busy} error={journey.error}/>;

  if (!profileOpen && selectedId && !stages.some(stage => stage.id === selectedId)) return <main className="esc-workspace"><section className="esc-card esc-unavailable">
    <h2>Этап недоступен</h2><p>Этого этапа нет в истории выбранного кандидата.</p>
    {stages.length > 0 && <Button onClick={() => selectStage(stages.at(-1)!.id, true)}>Открыть текущий этап</Button>}
  </section></main>;

  return <main className="esc-workspace"><CandidateStageCard
    person={{ id: person.id, name: person.name, photo: person.photo }}
    stages={stages} selectedStageId={selectedId} workflowRevision={person.workflowRevision || 0}
    onSelectStage={selectStage} onOpenNextStep={locked ? undefined : openNextStep} compactUpcomingMeetings
    onEditStage={locked ? undefined : stage => { if (stage.kind === 'offer') openEditor(stage.kind, stage); }}
    onSaveStageNotes={locked ? undefined : saveNotes} busy={journey.busy || callActive} error={editor ? undefined : journey.error} editor={form}
    profileOpen={profileOpen} onOpenProfile={openProfile} onCloseProfile={() => selectStage(selectedId)}
    profile={<CandidateProfilePanel key={person.id} journey={journey} person={person} source={source}/>}
    renderInterview={() => <EmployerScreening journey={journey} person={person} source={source} appearance="candidate" enableComments/>}
    renderMeeting={meetingMaterials} renderCompletedMeeting={meetingMaterials} renderOffer={offerActions}
    headerActions={<DropdownMenu.Root><DropdownMenu.Trigger asChild><Button variant="ghost" size="icon" aria-label="Действия с кандидатом" disabled={journey.busy}><MoreHorizontal size={20}/></Button></DropdownMenu.Trigger>
      <DropdownMenu.Portal><DropdownMenu.Content className="esc-menu" align="end" sideOffset={8}>
        <DropdownMenu.Item className="esc-menu-item" onSelect={onChat}>Открыть чат</DropdownMenu.Item>
        {!locked && selectedStage?.meetingId === person.meeting?.id && ['pending', 'confirmed'].includes(selectedStage?.status || '') && <>
          <DropdownMenu.Item className="esc-menu-item" disabled={callActive} onSelect={() => openEditor('meeting', selectedStage, true)}>Перенести встречу</DropdownMenu.Item>
          <DropdownMenu.Item className="esc-menu-item" disabled={callActive} onSelect={() => { void journey.act('meeting.cancel', person.id, { meetingId: person.meeting!.id, expectedRevision: person.workflowRevision || 0 }); }}>Отменить встречу</DropdownMenu.Item>
        </>}
        {selectedStage?.kind === 'meeting' && selectedStage.status === 'completed' && <DropdownMenu.Item className="esc-menu-item" disabled={callActive} onSelect={() => { setEditor(null); setProfileOpen(false); setView('notes'); journey.setError(''); }}>Заметки и фидбек</DropdownMenu.Item>}
        {!locked && <DropdownMenu.Item className="esc-menu-item" disabled={callActive} onSelect={openRejection}>Отказать</DropdownMenu.Item>}
      </DropdownMenu.Content></DropdownMenu.Portal></DropdownMenu.Root>}
  /></main>;
}
