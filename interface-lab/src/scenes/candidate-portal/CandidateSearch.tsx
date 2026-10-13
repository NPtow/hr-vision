import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { AnimatePresence, LayoutGroup, MotionConfig, motion, useReducedMotion } from 'motion/react';
import { ArrowRight, ArrowUpRight, CalendarDays, Check, CheckCheck, ChevronDown, Clock3, Play, Video } from 'lucide-react';
import type { CandidateApplication, CandidateOffer, CandidateState, CandidateWorkflowCommand } from './api';
import { timeZone } from './api';
import { applicationAfterInterest, applicationGroup, applicationHistory, applicationOffer, applicationRevision, applicationStatus, canReplyToMeeting, currentApplicationMeeting, meetingInterval, validDate, workspaceDateTime, type ApplicationHistoryItem } from './workspace-model';
import { applicationCardTone, applicationMeetingIsJoinable, orderedSearchApplications } from './candidate-search-model';
import sandboxGirl from '../../assets/candidate/sandbox-girl.png';
import './candidate-search.css';

export type CandidateSearchProps = {
  candidate: CandidateState;
  busy: boolean;
  selectedId: string;
  onSelect: (id: string) => void;
  onDecision: (applicationId: string, decision: 'accepted' | 'declined') => Promise<boolean>;
  onMeetingReply: (application: CandidateApplication, action: 'confirm' | 'decline') => Promise<boolean>;
  onWorkflow: (command: CandidateWorkflowCommand) => Promise<boolean>;
  onJoin: (applicationId: string, meetingId?: string) => void;
  onBookInterview: () => void;
  onInterview: () => void;
  onResumeSearch: () => Promise<boolean>;
};

function ActionButton({ children, onClick, disabled, secondary = false }: { children: ReactNode; onClick: () => void; disabled?: boolean; secondary?: boolean }) {
  return <button type="button" className={`cps-button${secondary ? ' cps-button-secondary' : ''}`} disabled={disabled} onClick={onClick}>{children}</button>;
}

function OfferTerms({ offer }: { offer: CandidateOffer }) {
  const start = validDate(/^\d{4}-\d{2}-\d{2}$/.test(offer.startDate) ? `${offer.startDate}T12:00:00` : offer.startDate);
  const startLabel = start ? new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }).format(start) : offer.startDate || 'Не указана';
  return <section className="cps-offer" aria-label="Условия оффера"><div className="cps-detail-label"><h3>Предложение о работе</h3><span>Версия {offer.version}</span></div><dl>
    <div><dt>Должность</dt><dd>{offer.role || 'Не указана'}</dd></div>
    <div><dt>Вознаграждение</dt><dd>{offer.compensation || 'Не указано'}</dd></div>
    <div><dt>Формат работы</dt><dd>{offer.format || 'Не указан'}</dd></div>
    <div><dt>Дата выхода</dt><dd>{startLabel}</dd></div>
    <div className="cps-offer-expectations"><dt>Ожидания и условия</dt><dd>{offer.expectations || 'Не указаны'}</dd></div>
  </dl></section>;
}

function ApplicationActions({ application, props }: { application: CandidateApplication; props: CandidateSearchProps }) {
  const { busy, onDecision, onMeetingReply, onWorkflow, onJoin } = props;
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [offerReply, setOfferReply] = useState<'accepted' | 'declined' | null>(null);
  const locked = useRef(false);
  const requests = useRef(new Map<string, string>());
  const disabled = busy || pending;
  const offer = applicationOffer(application);
  const meeting = currentApplicationMeeting(application);
  const status = applicationStatus(application);
  const history = applicationGroup(application) === 'history';
  const meetingReplyAvailable = canReplyToMeeting(application);

  async function run(action: () => Promise<boolean>, after?: () => void) {
    if (locked.current || busy) return;
    locked.current = true; setPending(true); setError('');
    try { if (await action()) after?.(); else setError('Изменения не сохранены. Проверьте сообщение об ошибке и попробуйте снова.'); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось сохранить ответ. Попробуйте снова.'); }
    finally { locked.current = false; setPending(false); }
  }
  function workflow(command: CandidateWorkflowCommand) {
    const exact = { ...command, expectedRevision: applicationRevision(application) };
    const key = JSON.stringify(exact);
    const requestId = requests.current.get(key) || crypto.randomUUID();
    requests.current.set(key, requestId);
    return run(async () => {
      const saved = await onWorkflow({ ...exact, requestId });
      if (saved) requests.current.delete(key);
      return saved;
    }, () => setOfferReply(null));
  }
  function meetingReply(action: 'confirm' | 'decline') {
    if (!canReplyToMeeting(application)) { setError('Это время уже недоступно. Обновите кабинет, чтобы увидеть новое предложение.'); return; }
    void run(() => onMeetingReply(application, action));
  }

  let actions: ReactNode;
  if (history) actions = <p className={`cps-result${status.positive ? ' cps-result-positive' : ''}`}><CheckCheck size={17}/>{status.positive ? 'Ответ сохранён' : 'Процесс сохранён в истории'}</p>;
  else if (offer?.status === 'sent') actions = offerReply ? <div className="cps-offer-confirm" role="group" aria-label="Подтверждение ответа на оффер"><h4>{offerReply === 'accepted' ? 'Принять оффер на этих условиях?' : 'Отклонить предложение?'}</h4><p>{offer.role} · {application.vacancy.company}. Работодатель получит ваш ответ на версию {offer.version}.</p><div className="cps-actions"><ActionButton disabled={disabled} onClick={() => void workflow({ applicationId: application.id, action: 'offer.reply', version: offer.version, value: offerReply })}>{offerReply === 'accepted' ? 'Да, принимаю' : 'Да, отклоняю'}<Check size={16}/></ActionButton><ActionButton secondary disabled={disabled} onClick={() => setOfferReply(null)}>Вернуться к условиям</ActionButton></div></div>
    : <div className="cps-actions"><ActionButton disabled={disabled} onClick={() => setOfferReply('accepted')}>Принять оффер<ArrowUpRight size={16}/></ActionButton><ActionButton secondary disabled={disabled} onClick={() => setOfferReply('declined')}>Отклонить</ActionButton></div>;
  else if (application.status === 'pending') actions = <div className="cps-actions"><ActionButton disabled={disabled} onClick={() => void run(() => onDecision(application.id, 'accepted'))}>Хочу участвовать<ArrowRight size={16}/></ActionButton><ActionButton secondary disabled={disabled} onClick={() => void run(() => onDecision(application.id, 'declined'))}>Не подходит</ActionButton></div>;
  else if (meeting?.status === 'pending') actions = meetingReplyAvailable
    ? <div className="cps-actions"><ActionButton disabled={disabled} onClick={() => meetingReply('confirm')}>Подтвердить время<Check size={16}/></ActionButton><ActionButton secondary disabled={disabled} onClick={() => meetingReply('decline')}>Время не подходит</ActionButton></div>
    : <p className="cps-waiting"><Clock3 size={16}/>Ожидаем новое время от команды</p>;
  else if (meeting && ['confirmed', 'live'].includes(meeting.status)) actions = <>
    {props.candidate.videoConfigured && applicationMeetingIsJoinable(application) ? <div className="cps-actions"><ActionButton disabled={disabled} onClick={() => onJoin(application.id, meeting.id)}>Войти во встречу<Video size={16}/></ActionButton>{meeting.status === 'confirmed' && meetingReplyAvailable && <ActionButton secondary disabled={disabled} onClick={() => meetingReply('decline')}>Время не подходит</ActionButton>}</div>
      : <><p className="cps-waiting">{!props.candidate.videoConfigured ? 'Видеосвязь пока недоступна. Время встречи сохранено.' : (validDate(meeting.start)?.getTime() ?? 0) > Date.now() ? 'Вход откроется за 15 минут до встречи.' : 'Время встречи закончилось.'}</p>{meeting.status === 'confirmed' && meetingReplyAvailable && <div className="cps-actions"><ActionButton secondary disabled={disabled} onClick={() => meetingReply('decline')}>Время не подходит</ActionButton></div>}</>}
  </>;
  else if (meeting?.status === 'completed' && applicationAfterInterest(application) === 'pending') actions = <div className="cps-actions"><ActionButton disabled={disabled} onClick={() => void workflow({ applicationId: application.id, action: 'after', meetingId: meeting.id, value: 'yes' })}>Хочу продолжить<ArrowRight size={16}/></ActionButton><ActionButton secondary disabled={disabled} onClick={() => void workflow({ applicationId: application.id, action: 'after', meetingId: meeting.id, value: 'no' })}>Не продолжать</ActionButton></div>;
  else actions = <p className="cps-waiting"><Clock3 size={16}/>Ожидаем следующий шаг от команды</p>;

  return <section className="cps-next"><span className="cps-eyebrow">{history ? 'Результат' : status.attention ? 'Ваш следующий шаг' : 'Сейчас'}</span><h3>{status.label}</h3><p className="cps-next-description">{status.detail}</p>
    {!history && meeting && ['pending', 'confirmed', 'live', 'completed'].includes(meeting.status) && <div className="cps-meeting"><CalendarDays size={19}/><div><strong>{meetingInterval(meeting)}</strong><span>{timeZone()}</span></div></div>}
    {actions}{pending && <p className="cps-feedback" role="status">Сохраняем ответ…</p>}{error && <p className="cps-error" role="alert">{error}</p>}
  </section>;
}

function History({ application, onJoin }: { application: CandidateApplication; onJoin: CandidateSearchProps['onJoin'] }) {
  const items = applicationHistory(application);
  if (!items.length) return null;
  const dated = items.filter(item => validDate(item.at));
  const snapshots = items.filter(item => !validDate(item.at));
  const event = (item: ApplicationHistoryItem) => <li key={item.id} className={`cps-history-${item.tone}`}><span className="cps-history-dot"/><div><p>{item.label}</p>{item.detail && <span>{item.detail}</span>}{item.at && validDate(item.at) && <time dateTime={item.at}>{workspaceDateTime(item.at)}</time>}{item.materials && item.meetingId && <button type="button" onClick={() => onJoin(application.id, item.meetingId)}><Video size={13}/>Материалы встречи</button>}</div></li>;
  return <details className="cps-history"><summary><span>История процесса</span><ChevronDown size={15}/></summary>{dated.length > 0 && <div className="cps-history-section"><h4>События</h4><ol>{dated.map(event)}</ol></div>}{snapshots.length > 0 && <div className="cps-history-section cps-history-snapshots"><h4>Состояние процесса</h4><ol>{snapshots.map(event)}</ol></div>}</details>;
}

function cardAction(application: CandidateApplication, videoConfigured: boolean) {
  const status = applicationStatus(application);
  if (applicationGroup(application) === 'history') return { label: status.label, join: false };
  if (applicationOffer(application)?.status === 'sent') return { label: 'Посмотреть оффер', join: false };
  if (application.status === 'pending') return { label: 'Рассмотреть вакансию', join: false };
  const meeting = currentApplicationMeeting(application);
  if (meeting && ['confirmed', 'live'].includes(meeting.status)) {
    const ready = applicationMeetingIsJoinable(application) && videoConfigured;
    return { label: ready ? 'Ко встрече' : 'Посмотреть встречу', join: ready };
  }
  if (meeting?.status === 'pending' && canReplyToMeeting(application)) return { label: 'Посмотреть время', join: false };
  if (meeting?.status === 'completed' && applicationAfterInterest(application) === 'pending') return { label: 'Как прошла встреча?', join: false };
  return { label: status.label, join: false };
}

function ApplicationCard({ application, props, scope }: { application: CandidateApplication; props: CandidateSearchProps; scope: string }) {
  const reduced = useReducedMotion();
  const titleButton = useRef<HTMLButtonElement>(null);
  const offer = applicationOffer(application);
  const open = props.selectedId === application.id;
  const tone = applicationCardTone(application);
  const action = cardAction(application, props.candidate.videoConfigured);
  const contentId = `${scope}-${application.id}`;
  const titleId = `${contentId}-title`;
  const statusId = `${contentId}-status`;
  const actionKey = `${application.id}:${applicationRevision(application)}:${application.status}:${offer?.id}:${offer?.version}:${offer?.status}:${application.archivedAt || ''}`;
  const toggle = () => props.onSelect(open ? '' : application.id);
  return <motion.article layout={reduced ? false : 'position'} initial={{ opacity: 0, y: reduced ? 0 : 8 }} animate={{ opacity: 1, y: 0 }} className={`cps-card cps-tone-${tone}${open ? ' is-open' : ''}`} aria-labelledby={titleId}>
    <div className="cps-card-summary">
      <h2 id={titleId}><button ref={titleButton} type="button" className="cps-card-trigger" aria-expanded={open} aria-controls={open ? contentId : undefined} aria-describedby={statusId} onClick={toggle}>{application.vacancy.title}<ChevronDown size={18} aria-hidden="true"/></button></h2>
      <p className="cps-company-name">{application.vacancy.company}</p>
      <p className="cps-salary">{application.vacancy.salary?.trim() || 'Зарплата не указана'}</p>
      <span id={statusId} className="cps-sr-only">{applicationStatus(application).label}</span>
      {!open && <button type="button" className="cps-card-action" aria-expanded={action.join ? undefined : false} onClick={() => {
        if (action.join) props.onJoin(application.id, currentApplicationMeeting(application)?.id);
        else { props.onSelect(application.id); titleButton.current?.focus({ preventScroll: true }); }
      }}>{action.label}<ArrowRight size={17} aria-hidden="true"/></button>}
    </div>
    <AnimatePresence initial={false}>{open && <motion.div id={contentId} key="details" className="cps-card-reveal" initial={{ height: reduced ? 'auto' : 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: reduced ? 'auto' : 0, opacity: 0 }} transition={{ duration: reduced ? 0 : .22 }}><div className="cps-card-body">
      <section className="cps-about"><h3>О работе</h3>{(application.vacancy.city || application.vacancy.remote) && <p className="cps-location">{[application.vacancy.city, application.vacancy.remote ? 'Удалённо' : ''].filter(Boolean).join(' · ')}</p>}<p>{application.vacancy.description || 'Описание вакансии пока не добавлено.'}</p></section>
      {offer && <OfferTerms offer={offer}/>}
      <ApplicationActions key={actionKey} application={application} props={props}/>
      <History application={application} onJoin={props.onJoin}/>
    </div></motion.div>}</AnimatePresence>
  </motion.article>;
}

function Paused({ props }: { props: CandidateSearchProps }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const locked = useRef(false);
  async function resume() {
    if (locked.current || props.busy) return;
    locked.current = true; setPending(true); setError('');
    try { if (!await props.onResumeSearch()) setError('Не удалось возобновить поиск. Попробуйте снова.'); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось возобновить поиск.'); }
    finally { locked.current = false; setPending(false); }
  }
  return <header className="cps-heading cps-paused"><h1>Зарабатываю в классной компании.</h1><ActionButton disabled={pending || props.busy} onClick={() => void resume()}>{pending ? 'Возобновляем…' : 'Снова искать работу'}<Play size={15}/></ActionButton>{error && <p className="cps-error" role="alert">{error}</p>}</header>;
}

const headings = ['Посмотрим, что нашлось.', 'А что, если это оно?', 'Следующая глава — здесь.', 'Ваша следующая работа?', 'Кажется, есть кое-что интересное.'];
function entryHeading(candidateId: string) {
  let previous = '';
  try { previous = sessionStorage.getItem(`hrv-search-heading:${candidateId}`) || ''; } catch { /* Storage can be unavailable in private browsing. */ }
  const choices = headings.filter(heading => heading !== previous);
  return choices[Math.floor(Math.random() * choices.length)];
}

function SearchFeed({ props, scope }: { props: CandidateSearchProps; scope: string }) {
  const { candidate } = props;
  const [heading] = useState(() => entryHeading(candidate.id));
  const guideKey = `hrv-search-glow-guide:v1:${candidate.id}`;
  const [showGuide, setShowGuide] = useState(() => {
    try { return localStorage.getItem(guideKey) !== 'seen'; } catch { return true; }
  });
  const firstCard = useRef<HTMLDivElement>(null);
  const items = orderedSearchApplications(candidate.applications);
  const paused = candidate.searching === false;
  useEffect(() => {
    try { sessionStorage.setItem(`hrv-search-heading:${candidate.id}`, heading); } catch { /* The heading still works without storage. */ }
  }, [candidate.id, heading]);
  function dismissGuide() {
    setShowGuide(false);
    try { localStorage.setItem(guideKey, 'seen'); } catch { /* Keep dismissal for this visit. */ }
    firstCard.current?.querySelector<HTMLButtonElement>('.cps-card-trigger')?.focus({ preventScroll: true });
  }
  return <>
    {paused ? <Paused props={props}/> : <header className="cps-heading"><h1>{items.length ? heading : 'Подбираем то, что вам подойдёт.'}</h1></header>}
    {showGuide && !paused && items.length > 0 && <aside className="cps-glow-guide" aria-label="Как читать карточки">
      <p>Свечение подскажет, что происходит.</p>
      <ul><li><i className="cps-guide-new" aria-hidden="true"/>Новое</li><li><i className="cps-guide-action" aria-hidden="true"/>Ваш ход</li><li><i className="cps-guide-progress" aria-hidden="true"/>Ждём команду</li><li><i className="cps-guide-closed" aria-hidden="true"/>Завершено</li></ul>
      <button type="button" onClick={dismissGuide}>Понятно<Check size={16} aria-hidden="true"/></button>
    </aside>}
    {items.length > 0 && <div className="cps-grid" ref={firstCard} aria-label="Предложения работы">{items.map(application => <ApplicationCard key={application.id} application={application} props={props} scope={scope}/>)}</div>}
  </>;
}

export function CandidateSearch(props: CandidateSearchProps) {
  const { candidate, busy } = props;
  const scope = useId();
  const reduced = useReducedMotion();
  const paused = candidate.searching === false;
  if (!candidate.interview && !paused) return <section className="cps-search cps-interview-start" aria-labelledby={`${scope}-interview-title`}>
    <h1 id={`${scope}-interview-title`}>Начнём со знакомства</h1>
    <button type="button" className="cps-interview-choice" disabled={busy} onClick={props.onBookInterview}>
      <CalendarDays size={32} aria-hidden="true"/>
      <span>Выбрать время интервью</span>
    </button>
  </section>;
  if (candidate.interview && candidate.interview.status !== 'completed') return <section className="cps-search cps-interview-wait" aria-labelledby={`${scope}-waiting-title`}>
    <h1 id={`${scope}-waiting-title`}>{candidate.interview.status === 'live' ? 'Пора знакомиться' : 'Пупупу… ждём встречи'}</h1>
    <img className="cps-waiting-character" src={sandboxGirl} width="1374" height="1145" alt="Девочка играет в песочнице"/>
    <p className="cps-waiting-time"><time dateTime={candidate.interview.start}>{meetingInterval(candidate.interview)}</time></p>
    <button type="button" className="cps-waiting-join" disabled={busy} onClick={props.onInterview}><Video size={18} aria-hidden="true"/>Ко встрече</button>
  </section>;
  return <MotionConfig reducedMotion="user" transition={{ duration: reduced ? 0 : .24, ease: [.2, .75, .25, 1] }}><LayoutGroup id={`${scope}-search`}><section className="cps-search cps-feed">
    <SearchFeed key={candidate.id} props={props} scope={scope}/>
  </section></LayoutGroup></MotionConfig>;
}
