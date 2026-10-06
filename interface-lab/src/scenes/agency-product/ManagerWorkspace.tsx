import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, CalendarDays, Check, ChevronRight, CircleHelp, Clock3, FileText, Monitor, Search, Users, Video, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog';
import { InterviewReview } from '../interview-review/InterviewReview';
import { candidates as initialCandidates, type Candidate, type CandidateId, type JourneyProps } from './model';
import { canOpenView, usePipelineState, type CandidatePipelineState, type PipelineView } from './pipeline-state';
import './pipeline-workspace.css';

const roleName = 'Менеджер по работе с клиентами';
const feedbackQuestions = [
  { key: 'example', empty: 'noExample', title: 'Какой конкретный пример показал, что человек справится с задачей?', placeholder: 'Что рассказал кандидат, что сделал сам и какой получил результат.' },
  { key: 'doubts', empty: 'noDoubts', title: 'Какие сомнения остались после встречи?', placeholder: 'Риски, противоречия или то, что не убедило.' },
  { key: 'check', empty: 'noCheck', title: 'Что ещё нужно проверить перед решением?', placeholder: 'Уточняющий вопрос, кейс или проверка рекомендации.' },
] as const;
const viewLabels: Record<PipelineView, string> = { tasks: 'Задачи найма', shortlist: 'Подборка', meetings: 'Встречи', meeting: 'Встреча', feedback: 'Фидбек', review: 'Разбор встречи', pool: 'Пул для оффера', offer: 'Оффер', complete: 'Оффер принят' };
const todayMoscow = () => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/Moscow' }).format(new Date());
function dateLabel(date: string, time?: string) {
  if (!date) return 'Время не выбрано';
  return `${new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' }).format(new Date(`${date}T12:00:00`))}${time ? ` · ${time} МСК` : ''}`;
}
function validDate(value: string) { return /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T12:00:00Z`)) && new Date(`${value}T12:00:00Z`).toISOString().slice(0, 10) === value; }
function statusLabel(flow: CandidatePipelineState) {
  if (flow.offer.status === 'accepted') return 'Оффер принят';
  if (flow.offer.status === 'sent') return 'Ожидаем ответ на оффер';
  if (flow.offer.status === 'declined') return 'Отказ от оффера';
  if (flow.decision === 'declined') return 'Не продолжаем';
  if (flow.decision === 'pool') return 'В пуле для оффера';
  if (flow.feedback.savedAt) return 'Разбор встречи готов';
  if (flow.meeting.status === 'completed') return 'Нужен фидбек';
  if (flow.meeting.status === 'confirmed') return 'Встреча подтверждена · пример';
  if (flow.meeting.status === 'pending') return 'Ждём подтверждения времени';
  if (flow.meeting.status === 'cancelled') return 'Встреча отменена';
  return 'На рассмотрении';
}

export function ManagerWorkspace({ state, update, notify }: JourneyProps) {
  const { pipeline, setPipeline, storageError } = usePipelineState();
  const { view, selected, byCandidate } = pipeline;
  const sharedProfile = state.profileSaved && state.shareConfirmed;
  const people = initialCandidates.map(c => c.id === 'anna' && sharedProfile ? { ...c, name: state.profile.name, salary: state.profile.expectations, availability: state.profile.availability } : c);
  const person = people.find(c => c.id === selected)!;
  const flow = byCandidate[selected];
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [mobileDetail, setMobileDetail] = useState(false);
  const [dialog, setDialog] = useState<'schedule' | 'decline' | null>(null);
  const [schedule, setSchedule] = useState({ date: '', time: '' });
  const [declineReason, setDeclineReason] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [roomOpen, setRoomOpen] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const activeOffer = people.find(c => ['sent', 'accepted'].includes(byCandidate[c.id].offer.status));
  const accepted = people.find(c => byCandidate[c.id].offer.status === 'accepted');
  const pool = people.filter(c => byCandidate[c.id].decision === 'pool');
  const eligible = pool.filter(c => byCandidate[c.id].offer.status === 'draft');
  const meetings = people.filter(c => byCandidate[c.id].meeting.status !== 'none' && byCandidate[c.id].meeting.status !== 'cancelled').sort((a, b) => {
    const first = byCandidate[a.id].meeting;
    const second = byCandidate[b.id].meeting;
    return `${first.date}T${first.time}`.localeCompare(`${second.date}T${second.time}`);
  });
  const meetingDates = [...new Set(meetings.map(c => byCandidate[c.id].meeting.date))];
  const listed = people.filter(c => c.name.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru')) && (filter === 'all' || (filter === 'meetings' ? ['pending', 'confirmed', 'completed'].includes(byCandidate[c.id].meeting.status) : byCandidate[c.id].decision === filter)));
  const hasPool = pool.length > 0;
  const canOffer = Boolean(activeOffer || eligible.length);
  useEffect(() => { heading.current?.focus(); setErrors({}); setRoomOpen(false); }, [view, selected]);
  function changeCandidate(id: CandidateId, nextView: PipelineView = 'shortlist') {
    setPipeline(previous => ({ ...previous, selected: id, view: canOpenView(previous, nextView, id) ? nextView : 'shortlist' }));
    setMobileDetail(true);
  }
  function go(nextView: PipelineView) {
    if (canOpenView(pipeline, nextView, selected)) setPipeline(previous => ({ ...previous, view: nextView }));
  }
  function patchFlow(patch: Partial<CandidatePipelineState>, id: CandidateId = selected) {
    setPipeline(previous => ({ ...previous, byCandidate: { ...previous.byCandidate, [id]: { ...previous.byCandidate[id], ...patch } } }));
  }
  function scheduleMeeting() {
    setSchedule(flow.meeting.status === 'none' ? { date: '', time: '' } : { date: flow.meeting.date, time: flow.meeting.time });
    setErrors({}); setDialog('schedule');
  }
  function saveSchedule() {
    const nextErrors: Record<string, string> = {};
    if (!validDate(schedule.date)) nextErrors.date = 'Выберите дату встречи.';
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(schedule.time)) nextErrors.time = 'Укажите время по Москве.';
    if (!Object.keys(nextErrors).length && Date.parse(`${schedule.date}T${schedule.time}:00+03:00`) <= Date.now()) nextErrors.date = 'Встреча должна быть в будущем.';
    setErrors(nextErrors); if (Object.keys(nextErrors).length) return;
    setPipeline(previous => ({ ...previous, view: 'meeting', byCandidate: { ...previous.byCandidate, [selected]: { ...previous.byCandidate[selected], meeting: { status: 'pending', date: schedule.date, time: schedule.time, simulation: false } } } }));
    update({ decisions: { ...state.decisions, [selected]: { kind: 'invite' } } });
    setDialog(null); notify(`${person.name}: время встречи сохранено.`);
  }
  function confirmMeetingExample() {
    patchFlow({ meeting: { ...flow.meeting, status: 'confirmed', simulation: true } });
    notify('Показано подтверждение в UI-примере. Кандидату ничего не отправлялось.');
  }
  function finishMeetingExample() {
    setPipeline(previous => ({ ...previous, view: 'feedback', byCandidate: { ...previous.byCandidate, [selected]: { ...previous.byCandidate[selected], meeting: { ...previous.byCandidate[selected].meeting, status: 'completed', simulation: true } } } }));
    setRoomOpen(false);
  }
  function saveFeedback() {
    const nextErrors: Record<string, string> = {};
    feedbackQuestions.forEach(q => { if (!flow.feedback[q.empty] && !flow.feedback[q.key].trim()) nextErrors[q.key] = 'Добавьте ответ или отметьте «Нет данных».'; });
    setErrors(nextErrors); if (Object.keys(nextErrors).length) return;
    setPipeline(previous => ({ ...previous, view: 'review', byCandidate: { ...previous.byCandidate, [selected]: { ...previous.byCandidate[selected], feedback: { ...previous.byCandidate[selected].feedback, savedAt: new Date().toISOString() } } } }));
    notify('Ваш фидбек сохранён в этом браузере.');
  }
  function addToPool() {
    if (!flow.feedback.savedAt || flow.meeting.status !== 'completed' || flow.offer.status === 'declined' || accepted) return;
    patchFlow({ decision: 'pool', declineReason: '' }); notify(`${person.name} в пуле для оффера. Можно продолжить рассмотрение или подготовить предложение.`);
  }
  function saveDecline() {
    if (!declineReason.trim()) { setErrors({ decline: 'Укажите причину, чтобы уточнить следующую подборку.' }); return; }
    patchFlow({ decision: 'declined', declineReason: declineReason.trim(), ...(['pending', 'confirmed'].includes(flow.meeting.status) ? { meeting: { ...flow.meeting, status: 'cancelled' as const } } : {}) });
    update({ decisions: { ...state.decisions, [selected]: { kind: 'decline', reason: declineReason.trim() } } });
    setDialog(null); notify('Решение сохранено. Автоматических сообщений кандидату нет.');
  }
  function enterOffer(id?: CandidateId) {
    const candidate = activeOffer || (id ? eligible.find(c => c.id === id) : eligible.find(c => c.id === selected)) || eligible[0];
    if (candidate) changeCandidate(candidate.id, byCandidate[candidate.id].offer.status === 'accepted' ? 'complete' : 'offer');
  }
  function editOffer(key: keyof CandidatePipelineState['offer'], value: string) { patchFlow({ offer: { ...flow.offer, [key]: value, prepared: false } }); setErrors(previous => ({ ...previous, [key]: '' })); }
  function prepareOffer() {
    if (activeOffer || flow.decision !== 'pool' || flow.offer.status !== 'draft') return;
    const nextErrors: Record<string, string> = {};
    for (const key of ['role', 'compensation', 'format', 'expectations'] as const) if (!flow.offer[key].trim()) nextErrors[key] = 'Заполните это условие предложения.';
    if (!validDate(flow.offer.startDate)) nextErrors.startDate = 'Укажите предполагаемую дату начала.';
    else if (flow.offer.startDate < todayMoscow()) nextErrors.startDate = 'Дата начала не должна быть в прошлом.';
    setErrors(nextErrors); if (Object.keys(nextErrors).length) return;
    patchFlow({ offer: { ...flow.offer, prepared: true } });
  }
  function sendOfferExample() {
    if (activeOffer || !flow.offer.prepared || flow.offer.status !== 'draft') return;
    patchFlow({ offer: { ...flow.offer, status: 'sent', sentAt: new Date().toISOString() } });
    notify('Показано ожидание ответа. Это UI-пример: письмо и уведомление не отправлялись.');
  }
  function simulateOfferAnswer(answer: 'accepted' | 'declined') {
    if (flow.offer.status !== 'sent') return;
    setPipeline(previous => ({ ...previous, view: answer === 'accepted' ? 'complete' : 'offer', byCandidate: { ...previous.byCandidate, [selected]: { ...previous.byCandidate[selected], offer: { ...previous.byCandidate[selected].offer, status: answer } } } }));
  }
  function declineButton() { setErrors({}); setDeclineReason(flow.declineReason); setDialog('decline'); }
  function nextCandidateAction() {
    if (flow.offer.status !== 'draft') return <Button onClick={() => changeCandidate(selected, flow.offer.status === 'accepted' ? 'complete' : 'offer')}>Открыть оффер<ArrowRight size={15}/></Button>;
    if (flow.decision === 'declined') return <Button variant="outline" onClick={() => patchFlow({ decision: 'review', declineReason: '' })}>Вернуть к рассмотрению</Button>;
    if (flow.feedback.savedAt) return <Button onClick={() => go('review')}>Разбор встречи<ArrowRight size={15}/></Button>;
    if (flow.meeting.status === 'completed') return <Button onClick={() => go('feedback')}>Оставить фидбек<ArrowRight size={15}/></Button>;
    if (['pending', 'confirmed'].includes(flow.meeting.status)) return <Button onClick={() => go('meeting')}>Открыть встречу<ArrowRight size={15}/></Button>;
    return <Button onClick={scheduleMeeting}><CalendarDays size={16}/>Назначить встречу</Button>;
  }
  function compactMeeting(candidate: Candidate, showDate = true) {
    const meeting = byCandidate[candidate.id].meeting;
    return <button className="hp-meeting-chip" key={candidate.id} aria-current={view === 'meeting' && selected === candidate.id ? 'true' : undefined} onClick={() => changeCandidate(candidate.id, meeting.status === 'completed' ? byCandidate[candidate.id].feedback.savedAt ? 'review' : 'feedback' : 'meeting')}><CalendarDays size={15}/><span><strong>{candidate.name}</strong><small>{meeting.status === 'completed' ? byCandidate[candidate.id].feedback.savedAt ? 'Разбор встречи' : 'Нужен фидбек' : `${showDate ? dateLabel(meeting.date, meeting.time) : `${meeting.time} МСК`} · ${meeting.status === 'pending' ? 'Ждём подтверждения' : 'Подтверждено · пример'}`}</small></span><ChevronRight size={15}/></button>;
  }
  function othersDisclosure() {
    return <details className="hp-others"><summary>Остальные кандидаты · {people.filter(c => c.id !== selected).length}</summary><div>{people.filter(c => c.id !== selected).map(c => <button key={c.id} onClick={() => changeCandidate(c.id)}><span>{c.name}<small>{statusLabel(byCandidate[c.id])}</small></span><ChevronRight size={16}/></button>)}</div></details>;
  }
  const progress = [
    { view: 'shortlist' as const, text: 'Подборка', available: true },
    { view: 'meetings' as const, text: `Встречи · ${meetings.length}`, available: meetings.length > 0 || ['meetings', 'meeting'].includes(view) },
    { view: (flow.feedback.savedAt ? 'review' : 'feedback') as PipelineView, text: 'Фидбек и разбор', available: flow.meeting.status === 'completed' },
    { view: 'pool' as const, text: `Пул${pool.length ? ` · ${pool.length}` : ''}`, available: hasPool },
    { view: 'offer' as const, text: 'Оффер', available: canOffer },
  ];
  return <div className="hp-workspace">
    {storageError && <p className="hp-alert" role="alert">Браузер не даёт сохранить изменения. При обновлении они могут потеряться.</p>}
    {view === 'tasks' ? <main className="hp-task-home">
      <div className="hp-home-heading"><span className="hp-eyebrow">Сфера · ваша команда</span><h1 ref={heading} tabIndex={-1}>Задачи найма</h1></div>
      <article className="hp-task-card"><div><span className="hp-eyebrow">Клиентский отдел</span><h2>Вернуть клиентов к повторным покупкам</h2><p>{roleName}</p><small>{accepted ? `Оффер принят · ${accepted.name}` : activeOffer ? `Ожидаем ответ · ${activeOffer.name}` : `${people.length} кандидата${meetings.length ? ` · встреч: ${meetings.length}` : ''}${pool.length ? ` · в пуле: ${pool.length}` : ''}`}</small></div><Button onClick={() => go('shortlist')}>Открыть подборку<ArrowRight size={16}/></Button></article>
    </main> : <>
      <header className="hp-context"><nav aria-label="Где вы находитесь"><button onClick={() => go('tasks')}>Задачи найма</button><ChevronRight size={13}/><h1 ref={heading} tabIndex={-1}>{roleName}</h1></nav><details className="hp-brief"><summary>Задача и условия</summary><div><strong>Вернуть клиентов к повторным покупкам</strong><p>Разобраться, почему клиенты перестали покупать, и выстроить регулярную работу с базой.</p><p>Москва · гибрид · 150–180 тыс. ₽ на руки + бонус</p><p>Результат: план развития базы, повторные контакты и измеримый результат в CRM. Конкретные KPI согласуем в оффере.</p></div></details></header>
      <nav className="hp-progress" aria-label="Этапы найма">{progress.filter(item => item.available).map(item => <button key={item.text} aria-current={view === item.view || (item.view === 'meetings' && view === 'meeting') || (item.text === 'Фидбек и разбор' && ['feedback', 'review'].includes(view)) || (item.text === 'Оффер' && view === 'complete') ? 'step' : undefined} onClick={() => item.view === 'offer' ? enterOffer() : go(item.view)}>{item.text}</button>)}</nav>
      {view === 'shortlist' && <>
        {(meetings.length > 0 || hasPool || accepted) && <div className="hp-flow-strip">{accepted ? <button className="hp-meeting-chip" onClick={() => changeCandidate(accepted.id, 'complete')}><Check size={16}/><span><strong>Оффер принят · {accepted.name}</strong><small>Дальнейшие предложения остановлены</small></span><ChevronRight size={15}/></button> : <>{meetings.map(c => compactMeeting(c))}{hasPool && <Button variant="outline" onClick={() => go('pool')}><Users size={16}/>Пул для оффера · {pool.length}</Button>}</>}</div>}
        <main className={`hp-shortlist ${mobileDetail ? 'hp-detail-open' : ''}`}>
          <aside className="hp-candidates" aria-label="Кандидаты"><label className="hp-search"><Search size={16}/><Input aria-label="Найти кандидата" placeholder="Найти кандидата" value={query} onChange={e => setQuery(e.target.value)}/></label><div className="hp-filters" role="group" aria-label="Статус рассмотрения">{[['all', 'Все'], ['meetings', 'Встречи'], ['pool', 'В пуле']].map(([value, label]) => <button key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}</div><div className="hp-candidate-list">{listed.map(c => <button className="hp-candidate" key={c.id} aria-pressed={selected === c.id} aria-label={`Открыть: ${c.name}`} onClick={() => changeCandidate(c.id)}><div className="hp-candidate-name"><span className={`ag-avatar ${c.id}`}>{c.initials}</span><span><strong>{c.name}</strong><small>{c.experience} · {c.city}</small></span><ChevronRight size={15}/></div><p>{c.summary}</p><div><span>{c.salary}</span><small>{statusLabel(byCandidate[c.id])}</small></div></button>)}</div>{!listed.length && <div className="hp-empty"><p>Нет кандидатов по этому фильтру</p><Button variant="outline" onClick={() => { setQuery(''); setFilter('all'); }}>Сбросить фильтры</Button></div>}</aside>
          <section className="hp-profile" aria-label={`Карточка: ${person.name}`} key={selected}>
            <button className="hp-mobile-back" onClick={() => setMobileDetail(false)}><ArrowLeft size={15}/>К кандидатам</button>
            <div className="hp-profile-heading"><div><h2>{person.name}</h2><p>{person.role} · {person.city}</p></div><span className="hp-status">{statusLabel(flow)}</span></div>
            {flow.decision === 'declined' && <div className="hp-note"><strong>Не продолжаем по этой задаче</strong><p>{flow.declineReason}</p></div>}
            {['pending', 'confirmed', 'completed'].includes(flow.meeting.status) && <div className="hp-current-meeting"><CalendarDays size={18}/><span><strong>{flow.meeting.status === 'completed' ? 'Встреча · завершение показано в примере' : dateLabel(flow.meeting.date, flow.meeting.time)}</strong><small>{statusLabel(flow)}</small></span></div>}
            <Tabs defaultValue="interview" className="hp-profile-tabs"><TabsList aria-label="Материалы кандидата"><TabsTrigger value="interview">Интервью</TabsTrigger><TabsTrigger value="experience">Опыт</TabsTrigger><TabsTrigger value="conditions">Условия</TabsTrigger></TabsList><TabsContent value="interview"><InterviewReview key={person.id} person={person}/></TabsContent><TabsContent value="experience"><div className="hp-profile-body"><h3>Опыт работы</h3><p>Сведения из профиля кандидата. Независимая проверка ещё не проведена.</p>{person.history.map(job => <article className="hp-history" key={job.company}><strong>{job.company}</strong><span>{job.role}</span><small>{job.years}</small></article>)}<h3>Мотивация</h3><p>{person.motivation}</p></div></TabsContent><TabsContent value="conditions"><div className="hp-profile-body"><dl className="hp-facts"><div><dt>Доход</dt><dd>{person.salary}</dd></div><div><dt>Готовность к переходу</dt><dd>{person.availability}</dd></div><div><dt>Формат</dt><dd>Гибрид · обсуждается</dd></div></dl><p>Условия и доступность нужно подтвердить. Кандидат может рассматривать другие предложения.</p></div></TabsContent></Tabs>
            <footer className="hp-profile-actions">{nextCandidateAction()}{flow.decision !== 'declined' && flow.offer.status === 'draft' && !accepted && <Button variant="ghost" onClick={declineButton}>Не продолжаем</Button>}{flow.decision === 'pool' && <Button variant="outline" onClick={() => go('pool')}>К пулу кандидатов<ArrowRight size={15}/></Button>}</footer>
          </section>
        </main>
      </>}
      {view === 'meetings' && <main className="hp-stage hp-agenda">
        <div className="hp-stage-heading"><h2>Встречи</h2><Button variant="outline" onClick={() => go('shortlist')}>Выбрать кандидата</Button></div>
        {meetingDates.map(date => <section className="hp-agenda-day" key={date} aria-label={dateLabel(date)}><h3>{dateLabel(date)}</h3>{meetings.filter(c => byCandidate[c.id].meeting.date === date).map(c => <div key={c.id}>{compactMeeting(c, false)}</div>)}</section>)}
        {!meetings.length && <p className="hp-muted">Активных встреч нет. Выберите кандидата, чтобы назначить время.</p>}
      </main>}
      {view === 'meeting' && <main className="hp-stage"><button className="hp-back" onClick={() => go('meetings')}><ArrowLeft size={15}/>Все встречи{meetings.length > 0 ? ` · ${meetings.length}` : ''}</button>
        {meetings.length > 1 && <nav className="hp-meeting-switcher" aria-label="Встречи с кандидатами">{meetings.map(c => compactMeeting(c))}</nav>}<div className="hp-stage-heading"><div><span className="hp-eyebrow">{person.name}</span><h2>{roomOpen ? 'Комната встречи' : 'Встреча'}</h2></div><span className="hp-status">{statusLabel(flow)}</span></div>
        {roomOpen ? <><div className="hp-room"><Monitor size={38}/><h3>Видеокомната пока не подключена</h3><p>Здесь будет встреча с {person.name}. Провайдер видеосвязи ещё не выбран; камера, микрофон и запись сейчас не запускаются.</p><div className="hp-room-people"><span>Вы</span><span>{person.name}</span></div></div><Button variant="outline" onClick={() => setRoomOpen(false)}>Вернуться к встрече</Button><div className="hp-scenario"><span className="hp-eyebrow">Просмотр сценария</span><p>Переход ниже показывает следующий экран. Он не подтверждает, что разговор состоялся.</p><Button onClick={finishMeetingExample}>Показать завершение встречи<ArrowRight size={15}/></Button></div></> : <>
          <article className="hp-meeting-card"><CalendarDays size={24}/><div><h3>{dateLabel(flow.meeting.date, flow.meeting.time)}</h3><p>{flow.meeting.status === 'pending' ? '' : flow.meeting.status === 'confirmed' ? '' : flow.meeting.status === 'cancelled' ? 'Встреча отменена. Можно выбрать новое время.' : 'Показано завершение сценария. Фактическая встреча и запись не подтверждены.'}</p></div></article>
          <p className="hp-muted">Приглашения не отправляются: встреча сохранена только в этом браузере.</p>
          <div className="hp-actions">{flow.meeting.status === 'confirmed' && <Button onClick={() => setRoomOpen(true)}><Video size={16}/>Открыть комнату</Button>}{flow.meeting.status === 'completed' && <Button onClick={() => go(flow.feedback.savedAt ? 'review' : 'feedback')}>{flow.feedback.savedAt ? 'Разбор встречи' : 'Оставить фидбек'}<ArrowRight size={15}/></Button>}{flow.meeting.status !== 'completed' && <Button variant="outline" onClick={scheduleMeeting}>{flow.meeting.status === 'cancelled' ? 'Выбрать новое время' : 'Перенести встречу'}</Button>}{['pending', 'confirmed'].includes(flow.meeting.status) && <Button variant="ghost" onClick={() => { patchFlow({ meeting: { ...flow.meeting, status: 'cancelled' } }); notify('Встреча отменена в этом браузере.'); }}>Отменить встречу</Button>}</div>
          {flow.meeting.status === 'pending' && <details className="hp-scenario"><summary>Просмотр сценария</summary><p>Чтобы пройти дальнейший интерфейс, покажите ответ кандидата. Реального подтверждения это не создаёт.</p><Button variant="outline" onClick={confirmMeetingExample}>Смоделировать подтверждение</Button></details>}
          <details className="hp-brief-section"><summary>Что проверить на встрече</summary><p>{person.unknown}</p><p>Свяжите опыт кандидата с задачей: вернуть клиентов к повторным покупкам.</p></details>
        </>}
      </main>}
      {view === 'feedback' && <main className="hp-stage hp-feedback"><button className="hp-back" onClick={() => go('shortlist')}><ArrowLeft size={15}/>К подборке · черновик сохранится</button><div className="hp-stage-heading"><div><span className="hp-eyebrow">После вашей встречи · {person.name}</span><h2>Как прошла встреча?</h2></div><span className="hp-status">{flow.feedback.savedAt ? 'Сохранённый фидбек' : 'Черновик'}</span></div><p>Этот обязательный фидбек помогает улучшать качество подбора. Отделите конкретные примеры от впечатления. Если данных нет, так и отметьте.</p><div className="hp-note"><CircleHelp size={17}/><p>Вы просматриваете сценарий: реальная встреча ещё не проводилась. Ответы ниже сохраняются только в браузере.</p></div><form onSubmit={e => { e.preventDefault(); saveFeedback(); }} noValidate>{feedbackQuestions.map((q, index) => <div className="hp-feedback-field" key={q.key}><label htmlFor={`feedback-${q.key}`}><span>{index + 1}</span>{q.title}</label><Textarea id={`feedback-${q.key}`} value={flow.feedback[q.key]} disabled={flow.feedback[q.empty]} placeholder={q.placeholder} aria-invalid={!!errors[q.key]} aria-describedby={errors[q.key] ? `feedback-${q.key}-error` : undefined} onChange={e => { patchFlow({ feedback: { ...flow.feedback, [q.key]: e.target.value, savedAt: '' } }); setErrors(previous => ({ ...previous, [q.key]: '' })); }}/><label className="hp-checkbox"><input type="checkbox" checked={flow.feedback[q.empty]} onChange={e => { patchFlow({ feedback: { ...flow.feedback, [q.empty]: e.target.checked, savedAt: '' } }); setErrors(previous => ({ ...previous, [q.key]: '' })); }}/>Нет данных по этому вопросу</label>{errors[q.key] && <p id={`feedback-${q.key}-error`} className="hp-error" role="alert">{errors[q.key]}</p>}</div>)}<div className="hp-actions"><Button type="submit">Сохранить фидбек<ArrowRight size={15}/></Button><span className="hp-muted">Черновик сохраняется автоматически</span></div></form></main>}
      {view === 'review' && <main className="hp-stage"><button className="hp-back" onClick={() => go('shortlist')}><ArrowLeft size={15}/>К подборке</button><div className="hp-stage-heading"><div><span className="hp-eyebrow">{person.name}</span><h2>Разбор вашей встречи</h2></div><span className="hp-status">Ваш фидбек сохранён</span></div><div className="hp-review-layout"><section><h3>Ваши наблюдения</h3>{feedbackQuestions.map(q => <article className="hp-observation" key={q.key}><span>{q.key === 'example' ? 'Основание для решения' : q.key === 'doubts' ? 'Сомнения' : 'Что проверить'}</span><p>{flow.feedback[q.empty] ? 'Вы отметили: нет данных.' : flow.feedback[q.key]}</p><small>Источник: ваш фидбек · {new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' }).format(new Date(flow.feedback.savedAt))}</small></article>)}<Button variant="ghost" onClick={() => go('feedback')}>Уточнить фидбек</Button></section><aside className="hp-meeting-evidence"><Video size={25}/><h3>Запись этой встречи</h3><p>Записи и расшифровки пока нет. Видеосвязь не подключена.</p><hr/><h3>Оценка по интервью</h3><p>Не рассчитана: для анализа нужны фактические материалы встречи и проверенные критерии. Ваши наблюдения показаны отдельно, без придуманного балла.</p></aside></div><details className="hp-brief-section"><summary>Первое HR-интервью · отдельный материал</summary><p>Оно относится к первому знакомству с кандидатом, а не к вашей встрече.</p><Button variant="outline" onClick={() => go('shortlist')}>Вернуться к HR-интервью</Button></details>{flow.decision === 'pool' && <div className="hp-success"><Check size={18}/><span>{person.name} в пуле для оффера. Остальных можно рассмотреть в своём темпе.</span></div>}{flow.decision === 'declined' && <div className="hp-note"><p>Не продолжаем: {flow.declineReason}</p></div>}<div className="hp-actions">{!accepted && flow.offer.status === 'draft' && (flow.decision === 'pool' ? <Button onClick={() => go('pool')}>Перейти к офферу<ArrowRight size={15}/></Button> : <Button onClick={addToPool}><Check size={16}/>Добавить в пул для оффера</Button>)}{flow.decision !== 'declined' && flow.offer.status === 'draft' && !accepted && <Button variant="ghost" onClick={declineButton}>Не продолжаем</Button>}<Button variant="outline" onClick={() => go('shortlist')}>Продолжить рассмотрение</Button></div></main>}
      {view === 'pool' && <main className="hp-stage"><button className="hp-back" onClick={() => go('shortlist')}><ArrowLeft size={15}/>К подборке</button><div className="hp-stage-heading"><div><span className="hp-eyebrow">Решение по кандидатам</span><h2>Пул для оффера</h2></div><span className="hp-status">{pool.length} в пуле</span></div><p>Предложения идут по одному. Если человек откажется, вы выберете следующего. Если примет, дальнейшие офферы остановятся.</p><div className="hp-pool-list">{pool.map(c => <article key={c.id}><span className={`ag-avatar ${c.id}`}>{c.initials}</span><div><strong>{c.name}</strong><small>{statusLabel(byCandidate[c.id])}</small></div><Button variant="outline" onClick={() => changeCandidate(c.id, 'review')}>Разбор встречи</Button>{byCandidate[c.id].offer.status === 'draft' && !activeOffer && <button className="hp-text-button" onClick={() => patchFlow({ decision: 'review' }, c.id)}>Убрать из пула</button>}</article>)}</div>{!pool.length && <p>Пул пуст. Вернитесь к подборке и добавьте кандидата после встречи и фидбека.</p>}<div className="hp-actions">{canOffer && <Button onClick={() => enterOffer()}>{activeOffer ? 'Открыть текущий оффер' : 'Перейти к офферу'}<ArrowRight size={15}/></Button>}<Button variant="outline" onClick={() => go('shortlist')}>Продолжить рассмотрение</Button></div>{othersDisclosure()}</main>}
      {view === 'offer' && <main className="hp-stage hp-offer"><button className="hp-back" onClick={() => go('pool')}><ArrowLeft size={15}/>К пулу кандидатов</button><div className="hp-stage-heading"><div><span className="hp-eyebrow">{viewLabels[view]}</span><h2>{flow.offer.status === 'sent' ? 'Ожидаем ответ' : flow.offer.status === 'declined' ? 'Кандидат отказался' : flow.offer.prepared ? 'Проверьте предложение' : 'Подготовить оффер'}</h2></div><span className="hp-status">{flow.offer.status === 'sent' ? 'Отправка смоделирована' : flow.offer.status === 'declined' ? 'Ответ смоделирован' : flow.offer.prepared ? 'Предпросмотр' : 'Черновик'}</span></div>
        <div className="hp-recipient"><span className={`ag-avatar ${person.id}`}>{person.initials}</span><div><small>Получатель</small><strong>{person.name}</strong></div></div>
        {flow.offer.status === 'draft' && !flow.offer.prepared ? <><div className="hp-recipient-options" role="group" aria-label="Выбрать получателя оффера">{eligible.map(c => <button aria-pressed={selected === c.id} key={c.id} onClick={() => changeCandidate(c.id, 'offer')}>{c.name}</button>)}</div><form noValidate onSubmit={e => { e.preventDefault(); prepareOffer(); }}><div className="hp-offer-fields">{[{ key: 'role', label: 'Роль', placeholder: 'Название роли' }, { key: 'compensation', label: 'Вознаграждение', placeholder: 'Сумма на руки, период и условия бонуса' }, { key: 'format', label: 'Формат и место работы', placeholder: 'Город, удалённо или гибрид' }, { key: 'startDate', label: 'Предполагаемая дата начала', placeholder: '' }].map(field => <label key={field.key} htmlFor={`offer-${field.key}`}>{field.label}<Input id={`offer-${field.key}`} type={field.key === 'startDate' ? 'date' : 'text'} min={field.key === 'startDate' ? todayMoscow() : undefined} value={flow.offer[field.key as 'role' | 'compensation' | 'format' | 'startDate']} placeholder={field.placeholder} aria-invalid={!!errors[field.key]} onChange={e => editOffer(field.key as keyof CandidatePipelineState['offer'], e.target.value)}/>{errors[field.key] && <span className="hp-error" role="alert">{errors[field.key]}</span>}</label>)}<label className="hp-field-full" htmlFor="offer-expectations">Результат работы и KPI<Textarea id="offer-expectations" value={flow.offer.expectations} placeholder="Какой результат ожидаете, как и когда его оцените" aria-invalid={!!errors.expectations} onChange={e => editOffer('expectations', e.target.value)}/>{errors.expectations && <span className="hp-error" role="alert">{errors.expectations}</span>}</label></div><p className="hp-muted">Проверьте условия с кандидатом. Значения из описания задачи не означают согласия сторон.</p><Button type="submit">Посмотреть оффер<ArrowRight size={15}/></Button></form></> : <>
          <article className="hp-offer-preview"><span className="hp-eyebrow">Сфера · предложение о работе</span><h3>{person.name}, приглашаем в команду</h3><dl className="hp-facts"><div><dt>Роль</dt><dd>{flow.offer.role}</dd></div><div><dt>Вознаграждение</dt><dd>{flow.offer.compensation}</dd></div><div><dt>Формат</dt><dd>{flow.offer.format}</dd></div><div><dt>Начало</dt><dd>{dateLabel(flow.offer.startDate)}</dd></div><div><dt>Результат и KPI</dt><dd>{flow.offer.expectations}</dd></div></dl></article>
          {flow.offer.status === 'draft' && <><p className="hp-delivery-note"><CircleHelp size={16}/>В примере: письмо не отправляется. Кнопка показывает дальнейшее состояние интерфейса.</p><div className="hp-actions"><Button onClick={sendOfferExample}>Отправить оффер</Button><Button variant="outline" onClick={() => patchFlow({ offer: { ...flow.offer, prepared: false } })}>Изменить условия</Button></div></>}
          {flow.offer.status === 'sent' && <><div className="hp-note"><Clock3 size={18}/><div><strong>Ждём ответ от {person.name}</strong><p>В примере: письмо не отправлялось. Пока этот оффер активен, отправка другому человеку недоступна.</p></div></div><Button variant="outline" onClick={() => go('tasks')}>Вернуться к задаче</Button><details className="hp-scenario"><summary>Просмотр сценария · ответ на оффер</summary><p>Ни одна кнопка ниже не означает реальный ответ кандидата.</p><div className="hp-actions"><Button variant="outline" onClick={() => simulateOfferAnswer('declined')}>Смоделировать отказ</Button><Button onClick={() => simulateOfferAnswer('accepted')}>Смоделировать принятие</Button></div></details></>}
          {flow.offer.status === 'declined' && <><div className="hp-note"><X size={18}/><p>Показан отказ кандидата. Повторно ему не отправляем. Следующий оффер подготовите вы; автоматической отправки нет.</p></div><div className="hp-actions">{eligible.length ? <Button onClick={() => enterOffer()}>Выбрать следующего кандидата<ArrowRight size={15}/></Button> : <Button onClick={() => go('shortlist')}>Вернуться к подборке<ArrowRight size={15}/></Button>}</div></>}
        </>}{othersDisclosure()}
      </main>}
      {view === 'complete' && accepted && <main className="hp-stage hp-complete"><span className="hp-complete-icon"><Check size={30}/></span><span className="hp-eyebrow">Итог UI-сценария</span><h2>Оффер принят</h2><p className="hp-complete-person">{accepted.name}</p><p>Предложение выбрано. Дальнейшие офферы по этой задаче остановлены. Остальные кандидаты сохранены в пуле.</p><p className="hp-muted">Принятие смоделировано. Реального ответа, оформления и выхода на работу пока нет.</p><Button onClick={() => go('tasks')}>Вернуться к задачам найма<ArrowRight size={15}/></Button>{othersDisclosure()}</main>}
    </>}
    <Dialog open={dialog !== null} onOpenChange={open => { if (!open) setDialog(null); }}><DialogContent><DialogHeader><DialogTitle>{dialog === 'schedule' ? (flow.meeting.status === 'none' ? 'Назначить встречу' : 'Выбрать время встречи') : 'Почему не продолжаем?'}</DialogTitle><DialogDescription>{person.name}{dialog === 'schedule' ? ' · встреча на нашей платформе. Время указывается по Москве.' : ' · причина поможет уточнить подбор.'}</DialogDescription></DialogHeader>{dialog === 'schedule' ? <form className="hp-dialog-form" noValidate onSubmit={e => { e.preventDefault(); saveSchedule(); }}><label htmlFor="meeting-date">Дата<Input id="meeting-date" type="date" min={todayMoscow()} value={schedule.date} aria-invalid={!!errors.date} onChange={e => { setSchedule(previous => ({ ...previous, date: e.target.value })); setErrors({}); }}/>{errors.date && <span className="hp-error" role="alert">{errors.date}</span>}</label><label htmlFor="meeting-time">Время · МСК<Input id="meeting-time" type="time" value={schedule.time} aria-invalid={!!errors.time} onChange={e => { setSchedule(previous => ({ ...previous, time: e.target.value })); setErrors({}); }}/>{errors.time && <span className="hp-error" role="alert">{errors.time}</span>}</label><p className="hp-muted">После сохранения встреча сразу появится в задаче. Подтверждение кандидата будет отдельным шагом.</p><DialogFooter><Button type="button" variant="outline" onClick={() => setDialog(null)}>Отмена</Button><Button type="submit">Сохранить встречу</Button></DialogFooter></form> : <><label htmlFor="decline-reason">Причина<Textarea id="decline-reason" value={declineReason} onChange={e => { setDeclineReason(e.target.value); setErrors({}); }} aria-invalid={!!errors.decline}/></label>{errors.decline && <p className="hp-error" role="alert">{errors.decline}</p>}<DialogFooter><Button variant="outline" onClick={() => setDialog(null)}>Отмена</Button><Button onClick={saveDecline}>Сохранить решение</Button></DialogFooter></>}</DialogContent></Dialog>
  </div>;
}
