import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, Check, MoreHorizontal, RotateCcw, Video } from 'lucide-react';
import { Dialog, DropdownMenu } from 'radix-ui';
import { Button } from '../../components/ui/button';
import { EmployerShell } from '../employer-workspace/EmployerShell';
import { Avatar } from '../employer-workspace/PeoplePanel';
import { CandidateStageCard } from '../employer-workspace/CandidateStageCard';
import { CandidateOfferForm } from '../employer-workspace/CandidateStageForms';
import { MeetingRecordingReview } from '../interview-review/InterviewReview';
import type { CandidateStage, CandidateOfferValues } from '../employer-workspace/candidate-stage-types';
import { makeTaskStages, taskPersonName, taskStageIds, taskVacancyTitle } from './task-state-fixtures';
import { CompletedMeeting, completedVariants, makeCompletedDraft, type CompletedMeetingDraft, type CompletedVariant } from './CompletedMeeting';
import '../agency-product/theme.css';
import '../agency-product/product.css';
import '../connected-journey/connected.css';
import '../employer-workspace/employer.css';
import '../employer-workspace/candidate-stage-workspace.css';
import './employer-task-states.css';

export type TaskScreen = 'completed' | 'scheduled' | 'next-step' | 'offer';
const screens: { id: TaskScreen; label: string }[] = [
  { id: 'completed', label: 'Встреча' }, { id: 'scheduled', label: 'Предстоящая встреча' },
  { id: 'next-step', label: 'Следующий шаг' }, { id: 'offer', label: 'Оффер' },
];
const formatDate = (start: string) => new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' }).format(new Date(start));
const selectInitial = (screen: TaskScreen) => screen === 'scheduled' ? taskStageIds.upcoming : screen === 'offer' ? taskStageIds.offer : taskStageIds.completedSecond;
const initialStages = (screen: TaskScreen) => makeTaskStages().filter(stage => screen === 'scheduled'
  ? stage.id !== taskStageIds.completedSecond && stage.kind !== 'offer'
  : stage.id !== taskStageIds.upcoming && (screen === 'next-step' ? stage.kind !== 'offer' : true));
type Editor = 'next-step' | 'schedule' | 'offer' | 'reject' | null;

/** Local review scene: all mutations are in React state, with no service/API access. */
export function EmployerTaskStates({ initialScreen = 'completed', initialVariant = 'journal' }: { initialScreen?: TaskScreen; initialVariant?: CompletedVariant }) {
  const [screen, setScreen] = useState(initialScreen);
  const [run, setRun] = useState(0);
  useEffect(() => { setScreen(initialScreen); setRun(value => value + 1); }, [initialScreen]);
  return <TaskScene key={`${screen}:${run}:${initialVariant}`} initialVariant={initialVariant} screen={screen} onScreen={setScreen} onReset={() => setRun(value => value + 1)}/>;
}

function TaskScene({ screen, onScreen, onReset, initialVariant }: { screen: TaskScreen; onScreen: (screen: TaskScreen) => void; onReset: () => void; initialVariant: CompletedVariant }) {
  const [variant, setVariant] = useState<CompletedVariant>(initialVariant);
  const [meetingDrafts, setMeetingDrafts] = useState<Record<string, CompletedMeetingDraft>>({});
  const [stages, setStages] = useState(() => initialStages(screen));
  const [selectedId, setSelectedId] = useState<string>(selectInitial(screen));
  const [editor, setEditor] = useState<Editor>(screen === 'next-step' ? 'next-step' : null);
  const [editingOfferId, setEditingOfferId] = useState<string | null>(null);
  const [reschedulingId, setReschedulingId] = useState<string | null>(null);
  const [slot, setSlot] = useState('');
  const [callOpen, setCallOpen] = useState(false);
  const [rejected, setRejected] = useState(false);
  const [reason, setReason] = useState('');
  const [notice, setNotice] = useState('');
  const [updated, setUpdated] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const nextAction = useRef<HTMLButtonElement>(null);
  const surface = useRef<HTMLDivElement>(null);
  const selected = stages.find(stage => stage.id === selectedId)!;
  const offer = [...stages].reverse().find(stage => stage.kind === 'offer' && stage.status !== 'superseded');
  const activeMeeting = stages.find(stage => stage.kind === 'meeting' && ['pending', 'confirmed', 'live'].includes(stage.status));
  const closed = stages.some(stage => stage.kind === 'offer' && stage.status === 'accepted');
  const locked = closed || rejected;
  const editingOffer = stages.find(stage => stage.id === editingOfferId)?.offer;
  useEffect(() => { if (editor === 'next-step') nextAction.current?.focus({ preventScroll: true }); else if (editor) heading.current?.focus({ preventScroll: true }); }, [editor]);

  const updateStage = (id: string, update: Partial<CandidateStage>) => setStages(items => items.map(stage => stage.id === id ? { ...stage, ...update } : stage));
  const viewStage = (id: string) => { setSelectedId(id); setEditor(null); setNotice(''); };
  const next = () => { if (locked) return; setEditor('next-step'); setNotice(''); };
  const back = () => { setEditor(null); requestAnimationFrame(() => surface.current?.querySelector<HTMLButtonElement>('.esc-add-stage')?.focus({ preventScroll: true })); };
  const openSchedule = (stageId?: string) => { if (locked) return; setReschedulingId(stageId || null); setSlot(''); setEditor('schedule'); };
  function schedule() {
    if (!slot || locked) return;
    const template = makeTaskStages().find(stage => stage.id === taskStageIds.upcoming)!;
    const id = `local-meeting-${stages.length}`;
    const stage: CandidateStage = { ...template, id, date: slot, status: 'pending', meetingId: id,
      meeting: { ...template.meeting!, id, start: slot, end: new Date(Date.parse(slot) + 1800000).toISOString(), status: 'pending' } };
    setStages(items => [...items.map(item => item.id === reschedulingId && item.meeting ? { ...item, status: 'cancelled', meeting: { ...item.meeting, status: 'cancelled' as const } } : item), stage]);
    setSelectedId(id); setEditor(null);
  }
  function finishMeeting() {
    if (!selected.meeting) return;
    updateStage(selected.id, { status: 'completed', meeting: { ...selected.meeting, status: 'completed', testSkip: true }, notes: '', feedback: null });
    setCallOpen(false); setEditor('next-step'); setNotice('Встреча завершена в макете. Запись не создавалась.');
  }
  async function saveOffer(values: CandidateOfferValues) {
    if (activeMeeting || locked) return false;
    const base = makeTaskStages().find(stage => stage.kind === 'offer')!;
    const id = `local-offer-${stages.length}`;
    const status = editingOffer?.status === 'sent' ? 'sent' : 'draft';
    const version = (editingOffer?.version || 0) + 1;
    const nextOffer: CandidateStage = { ...base, id, date: new Date().toISOString(), status, documents: values.documents.map((document, index) => ({ ...document, id: document.id || `local-doc-${index}` })),
      offer: { ...values, id, version, status, createdAt: new Date().toISOString() } };
    setStages(items => [...items.map(stage => stage.id === editingOfferId && stage.offer ? { ...stage, status: 'superseded', offer: { ...stage.offer, status: 'superseded' as const } } : stage), nextOffer]);
    setSelectedId(id); setEditor(null); setEditingOfferId(null);
    setNotice(status === 'sent' ? 'Новая версия отправлена — симуляция в макете.' : 'Черновик сохранён в макете.');
    return true;
  }
  function setOfferStatus(status: 'sent' | 'accepted' | 'declined') {
    if (!offer?.offer) return;
    updateStage(offer.id, { status, offer: { ...offer.offer, status } }); setSelectedId(offer.id); setEditor(null);
  }
  function reject() {
    setStages(items => items.map(stage => stage.meeting && ['pending', 'confirmed'].includes(stage.status)
      ? { ...stage, status: 'cancelled', meeting: { ...stage.meeting, status: 'cancelled' } }
      : stage.offer && ['draft', 'sent'].includes(stage.status) ? { ...stage, status: 'withdrawn', offer: { ...stage.offer, status: 'withdrawn' } } : stage));
    setRejected(true); setEditor(null); setNotice('Отказ сохранён только в макете. Кандидату ничего не отправлено.');
  }
  function decisionView() {
    return <section className="ets-decision" aria-label="Следующий шаг">
      <button ref={nextAction} onClick={() => activeMeeting ? viewStage(activeMeeting.id) : openSchedule()}>{activeMeeting ? 'Открыть предстоящую встречу' : 'Назначить встречу'}</button>
      <button disabled={!!activeMeeting} title={activeMeeting ? 'Сначала завершите или отмените встречу' : undefined} onClick={() => { if (offer) viewStage(offer.id); else { setEditingOfferId(null); setEditor('offer'); } }}>{offer ? 'Открыть оффер' : 'Сделать оффер'}</button>
      <button onClick={() => setEditor('reject')}>Отказать</button>
    </section>;
  }
  function scheduleView() {
    return <section className="ets-next esc-form" aria-labelledby="ets-schedule-heading"><div className="ets-view-heading"><h3 ref={heading} tabIndex={-1} id="ets-schedule-heading">{reschedulingId ? 'Перенести встречу' : 'Назначить встречу'}</h3><Button variant="ghost" onClick={next}><ArrowLeft size={16}/>Назад</Button></div>
      <p>Свободное время кандидата · МСК · 30 минут</p>
      {['2026-10-27', '2026-10-28'].map(day => <fieldset className="ets-slots" key={day}><legend>{new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' }).format(new Date(day + 'T12:00:00'))}</legend>
        {['13:00', '14:00', '16:30'].map(time => { const value = `${day}T${time}:00+03:00`; return <label key={value}><input type="radio" name="meeting-slot" value={value} checked={slot === value} onChange={() => setSlot(value)}/><span>{time}</span></label>; })}</fieldset>)}
      <div className="esc-form-actions"><Button disabled={!slot} onClick={schedule}>Предложить время</Button><Button variant="ghost" onClick={back}>Отмена</Button></div>
    </section>;
  }
  const editorNode = editor === 'next-step' ? decisionView() : editor === 'schedule' ? scheduleView() : editor === 'offer' ? <CandidateOfferForm
    initialValues={editingOffer || { role: taskVacancyTitle, compensation: '120 000 ₽ + премия', format: 'Офис', startDate: '2026-10-26', expectations: 'Освоить продукт и CRM, начать самостоятельную работу с клиентами.' }}
    onSubmit={saveOffer} onCancel={next}/> : editor === 'reject' ? <form className="esc-form" onSubmit={event => { event.preventDefault(); reject(); }}><div className="ets-view-heading"><h3 ref={heading} tabIndex={-1}>Отказ кандидату</h3><Button type="button" variant="ghost" onClick={next}><ArrowLeft size={16}/>Назад</Button></div><label>Причина отказа<textarea value={reason} onChange={event => setReason(event.target.value)} placeholder="Что не подошло для этой вакансии?"/></label><div className="esc-form-actions"><Button type="submit">Подтвердить отказ</Button><Button type="button" variant="ghost" onClick={next}>Вернуться к решению</Button></div></form> : undefined;

  function meetingView(stage: CandidateStage) {
    if (stage.status === 'completed') return <section className="cj-materials"><div className="cj-materials-toolbar"><span>Материалы этой встречи</span><Button variant="ghost" onClick={() => setUpdated(true)}>Обновить материалы</Button></div>
      {updated && <p role="status" className="esc-caption">К локальному макету запись не подключена.</p>}
      <MeetingRecordingReview sourceId={stage.id} name={taskPersonName} appearance="candidate" chapters={[]} emptyMessage="Запись к этому макету не подключена."/>
    </section>;
    if (stage.status === 'cancelled') return <section className="cj-meeting"><p>Предыдущее время освобождено.</p><Button onClick={() => openSchedule(stage.id)}>Выбрать новое время</Button></section>;
    return <section className="esc-materials ets-upcoming" aria-label="Предстоящая встреча">
      <div className="ets-upcoming-copy"><h3>Предстоящая встреча</h3><p><time dateTime={stage.date}>{formatDate(stage.date!)} МСК</time></p></div>
      <Button disabled={stage.status === 'pending'} title={stage.status === 'pending' ? 'Ждём подтверждения кандидата' : undefined} onClick={() => setCallOpen(true)}><Video size={16}/>Подключиться</Button>
    </section>;
  }
  return <div className="ets-preview">
    <div className="ets-labbar"><nav aria-label="Экраны макета">{screens.map(item => <button key={item.id} aria-current={screen === item.id ? 'page' : undefined} onClick={() => onScreen(item.id)}>{item.label}</button>)}</nav><button className="ets-reset" title="Сбросить макет" aria-label="Сбросить макет" onClick={onReset}><RotateCcw size={16}/></button></div>
    <div className="ets-lab-context"><span>{selected?.kind === 'meeting' && selected.status === 'completed' && !editor ? 'Встреча · варианты компоновки · видео и данные тестовые' : editor === 'next-step' || editor === 'schedule' || editor === 'reject' || screen === 'next-step' ? 'Следующий шаг · данные тестовые' : screen === 'scheduled' ? 'Предстоящая встреча · упрощённый макет' : 'Локальный макет · данные тестовые'}</span>
      {selected?.kind === 'meeting' && selected.status === 'completed' && !editor && <div className="cm-variant-picker" aria-label="Варианты прошедшей встречи">{completedVariants.map(item => <button key={item.id} type="button" aria-pressed={variant === item.id} onClick={() => setVariant(item.id)}>{item.label}</button>)}</div>}
      {selected?.kind === 'offer' && offer && selected.id === offer.id && ['draft', 'sent', 'accepted', 'declined'].includes(offer.status) && !editor && <label>Ответ кандидата <select aria-label="Ответ кандидата в макете" value={offer.status} onChange={event => setOfferStatus(event.target.value as 'sent' | 'accepted' | 'declined')}><option value="draft" disabled>Черновик</option><option value="sent">Ожидаем</option><option value="accepted">Принял</option><option value="declined">Отклонил</option></select></label>}
      {selected?.kind === 'meeting' && ['pending', 'confirmed'].includes(selected.status) && !editor && <label>Состояние встречи <select aria-label="Состояние встречи в макете" value={selected.status} onChange={event => { const status = event.target.value as 'pending' | 'confirmed'; updateStage(selected.id, { status, meeting: { ...selected.meeting!, status } }); }}><option value="pending">Ждём подтверждения</option><option value="confirmed">Подтверждена</option></select></label>}
    </div>
    <div className="agency-product agency-product-theme cj-app ew-root ets-product"><EmployerShell page="shortlist" candidateContext="vacancy" scope="sfera" vacancy={taskVacancyTitle} name="Никита" hasOffers
      onNavigate={() => setNotice('В этой сцене открыта история кандидата по вакансии.')} onAccount={() => setNotice('Аккаунт в макете: Никита.')}
      candidateNavigation={<div className="esc-candidate-nav" aria-label="Кандидаты вакансии"><button className="esc-candidate-switch" aria-pressed="true" onClick={() => setNotice('Открыт тестовый кандидат Анна.')}><Avatar name={taskPersonName}/></button><span><Avatar name="Михаил Соколов"/></span><span><Avatar name="Елена Волкова"/></span></div>}>
      <main className="esc-workspace" ref={surface}>
        {closed && <div className="ets-outcome" role="status"><Check size={18}/><span>Оффер принят. Вакансия закрыта.</span></div>}
        {rejected && <div className="ets-outcome" role="status"><span>Работа с кандидатом завершена.{reason && ` Причина: ${reason}`}</span></div>}
        <CandidateStageCard person={{ id: 'task-preview-anna', name: taskPersonName }} stages={stages} selectedStageId={selectedId} workflowRevision={1}
          onSelectStage={viewStage} compactUpcomingMeetings onOpenNextStep={locked ? undefined : next} editor={editorNode}
          onEditStage={locked ? undefined : stage => { if (activeMeeting) { setNotice('Сначала завершите или отмените назначенную встречу.'); return; } setEditingOfferId(stage.id); setEditor('offer'); }}
          onSaveStageNotes={locked ? undefined : async (id, values) => { updateStage(id, { notes: values.notes }); return true; }}
          renderInterview={stage => <MeetingRecordingReview sourceId={stage.id} name={taskPersonName} appearance="candidate" chapters={[]} emptyMessage="Запись интервью к этому макету не подключена."/>}
          renderMeeting={meetingView}
          renderCompletedMeeting={stage => <CompletedMeeting key={stage.id} variant={variant} stageId={stage.id} date={stage.date || undefined} onNext={locked ? undefined : next} draft={meetingDrafts[stage.id] || makeCompletedDraft()} onChange={draft => setMeetingDrafts(items => ({ ...items, [stage.id]: draft }))}/>}
          renderOffer={stage => stage.status === 'draft' && !locked ? <div className="esc-stage-actions"><Button disabled={!!activeMeeting} onClick={() => { if (!activeMeeting) setOfferStatus('sent'); }}>Отправить оффер</Button>{activeMeeting && <p className="esc-caption">Сначала завершите или отмените назначенную встречу.</p>}</div> : null}
          headerActions={<DropdownMenu.Root><DropdownMenu.Trigger asChild><Button variant="ghost" size="icon" aria-label="Действия с кандидатом"><MoreHorizontal size={20}/></Button></DropdownMenu.Trigger><DropdownMenu.Portal><DropdownMenu.Content className="esc-menu" align="end" sideOffset={8}>{!locked && <DropdownMenu.Item className="esc-menu-item" onSelect={() => setEditor('reject')}>Отказать</DropdownMenu.Item>}<DropdownMenu.Item className="esc-menu-item" onSelect={() => setNotice('В этом макете внешние сообщения не отправляются.')}>Открыть чат</DropdownMenu.Item></DropdownMenu.Content></DropdownMenu.Portal></DropdownMenu.Root>}/>
      </main>
    </EmployerShell></div>
    <p className="ets-lab-status" role="status">{notice || 'Макет: изменения сохраняются до переключения экрана или сброса. Отправок нет.'}</p>
    <Dialog.Root open={callOpen} onOpenChange={setCallOpen}><Dialog.Portal><Dialog.Overlay className="ets-call-overlay"/><Dialog.Content className="ets-call-dialog"><Dialog.Title>Встреча с Анной</Dialog.Title><Dialog.Description>Симуляция перехода после звонка. Камера, микрофон и запись не включаются.</Dialog.Description><div className="ets-call-avatar">А</div><div className="ets-call-actions"><Dialog.Close asChild><Button variant="secondary">Вернуться</Button></Dialog.Close><Button onClick={finishMeeting}>Завершить встречу</Button></div></Dialog.Content></Dialog.Portal></Dialog.Root>
  </div>;
}
