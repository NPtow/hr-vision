import { useId, useRef, useState, type ReactNode } from 'react';
import { AnimatePresence, LayoutGroup, MotionConfig, motion, useReducedMotion } from 'motion/react';
import { ArrowRight, ArrowUpRight, BriefcaseBusiness, CalendarDays, Check, CheckCheck, ChevronDown, Clock3, MapPin, Pause, Play, RefreshCw, Video } from 'lucide-react';
import type { CandidateApplication, CandidateOffer, CandidateState, CandidateWorkflowCommand } from './api';
import { timeZone } from './api';
import { applicationAfterInterest, applicationGroup, applicationHistory, applicationOffer, applicationRevision, applicationStatus, canReplyToMeeting, currentApplicationMeeting, meetingInterval, validDate, workspaceDateTime, type ApplicationGroup, type ApplicationHistoryItem } from './workspace-model';
import sandboxGirl from '../../assets/candidate/sandbox-girl.png';
import './candidate-search.css';

export type CandidateSearchProps = {
  candidate: CandidateState;
  busy: boolean;
  filter: ApplicationGroup;
  setFilter: (filter: ApplicationGroup) => void;
  selectedId: string;
  onSelect: (id: string) => void;
  onDecision: (applicationId: string, decision: 'accepted' | 'declined') => Promise<boolean>;
  onMeetingReply: (application: CandidateApplication, action: 'confirm' | 'decline') => Promise<boolean>;
  onWorkflow: (command: CandidateWorkflowCommand) => Promise<boolean>;
  onJoin: (applicationId: string, meetingId?: string) => void;
  onRefresh: () => Promise<void>;
  onBookInterview: () => void;
  onInterview: () => void;
  onProfile: () => void;
  onResumeSearch: () => Promise<boolean>;
};

const filters: { value: ApplicationGroup; label: string }[] = [{ value: 'new', label: 'Новые' }, { value: 'process', label: 'В процессе' }, { value: 'history', label: 'История' }];
const initials = (name: string) => name.trim().split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join('').toUpperCase() || 'К';
const countLabel = (value: number) => `${value} ${{ one: 'предложение', few: 'предложения', many: 'предложений', other: 'предложений', zero: 'предложений', two: 'предложения' }[new Intl.PluralRules('ru').select(value)]}`;

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
    {props.candidate.videoConfigured ? <div className="cps-actions"><ActionButton disabled={disabled} onClick={() => onJoin(application.id, meeting.id)}>Войти во встречу<Video size={16}/></ActionButton>{meeting.status === 'confirmed' && meetingReplyAvailable && <ActionButton secondary disabled={disabled} onClick={() => meetingReply('decline')}>Время не подходит</ActionButton>}</div>
      : <><p className="cps-waiting">Видеосвязь пока недоступна. Время встречи сохранено.</p>{meeting.status === 'confirmed' && meetingReplyAvailable && <div className="cps-actions"><ActionButton secondary disabled={disabled} onClick={() => meetingReply('decline')}>Время не подходит</ActionButton></div>}</>}
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

function ApplicationCard({ application, props, scope }: { application: CandidateApplication; props: CandidateSearchProps; scope: string }) {
  const reduced = useReducedMotion();
  const status = applicationStatus(application);
  const offer = applicationOffer(application);
  const open = props.selectedId === application.id;
  const contentId = `${scope}-${application.id}`;
  const actionKey = `${application.id}:${applicationRevision(application)}:${application.status}:${offer?.id}:${offer?.version}:${offer?.status}:${application.archivedAt || ''}`;
  return <motion.article layout={reduced ? false : 'position'} className={`cps-card${open ? ' is-open' : ''}`}>
    <button type="button" className="cps-card-trigger" aria-expanded={open} aria-controls={contentId} onClick={() => props.onSelect(open ? '' : application.id)}>
      <span className="cps-card-top"><span className="cps-company-mark" aria-hidden="true">{initials(application.vacancy.company)}</span><ArrowUpRight size={19}/></span>
      <span className="cps-company-name">{application.vacancy.company}</span><span className="cps-role">{application.vacancy.title}</span>
      <span className="cps-facts">{application.vacancy.city && <span><MapPin size={14}/>{application.vacancy.city}</span>}{application.vacancy.remote && <span><BriefcaseBusiness size={14}/>Удалённо</span>}</span>
      <span className={`cps-status${status.attention ? ' cps-status-attention' : ''}${status.positive ? ' cps-status-positive' : ''}`}><span/>{status.label}</span>
    </button>
    <AnimatePresence initial={false}>{open && <motion.div id={contentId} key="details" className="cps-card-reveal" initial={{ height: reduced ? 'auto' : 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: reduced ? 'auto' : 0, opacity: 0 }} transition={{ duration: reduced ? 0 : .22 }}><div className="cps-card-body">
      <section className="cps-about"><h3>О работе</h3><p>{application.vacancy.description || 'Описание вакансии пока не добавлено.'}</p></section>
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
    try { if (await props.onResumeSearch()) props.setFilter('new'); else setError('Не удалось возобновить поиск. Попробуйте снова.'); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось возобновить поиск.'); }
    finally { locked.current = false; setPending(false); }
  }
  return <section className="cps-paused"><div className="cps-paused-art" aria-hidden="true"><span/><span/><span><Check size={34}/></span></div><span className="cps-eyebrow">В своём темпе</span><h1>Зарабатываю в классной компании.</h1><p>Можно выдохнуть. Поиск остановлен, все процессы сохранены в истории. Захотите перемен — начнём снова.</p><div className="cps-actions"><ActionButton disabled={pending || props.busy} onClick={() => void resume()}>{pending ? 'Возобновляем…' : 'Вернуться к поиску'}<Play size={15}/></ActionButton><ActionButton secondary disabled={pending || props.busy} onClick={() => props.setFilter('history')}>Посмотреть историю</ActionButton></div>{error && <p className="cps-error" role="alert">{error}</p>}</section>;
}

export function CandidateSearch(props: CandidateSearchProps) {
  const { candidate, filter, setFilter, busy, onRefresh, onProfile } = props;
  const scope = useId();
  const reduced = useReducedMotion();
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState('');
  const refreshLock = useRef(false);
  const items = candidate.applications.filter(application => applicationGroup(application) === filter);
  const paused = candidate.searching === false;
  async function refresh() {
    if (refreshLock.current || busy) return;
    refreshLock.current = true; setRefreshing(true); setRefreshError('');
    try { await onRefresh(); } catch (cause) { setRefreshError(cause instanceof Error ? cause.message : 'Не удалось обновить предложения.'); }
    finally { refreshLock.current = false; setRefreshing(false); }
  }
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
  return <MotionConfig reducedMotion="user" transition={{ duration: reduced ? 0 : .24, ease: [.2, .75, .25, 1] }}><LayoutGroup id={`${scope}-search`}><section className="cps-search">
    <div className="cps-tabs" role="tablist" aria-label="Вакансии по статусу">{filters.map((item, index) => <button type="button" key={item.value} role="tab" id={`${scope}-tab-${item.value}`} aria-selected={filter === item.value} aria-controls={`${scope}-results`} tabIndex={filter === item.value ? 0 : -1} onClick={() => setFilter(item.value)} onKeyDown={event => {
      let next = index;
      if (event.key === 'ArrowRight') next = (index + 1) % filters.length;
      else if (event.key === 'ArrowLeft') next = (index + filters.length - 1) % filters.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = filters.length - 1;
      else return;
      event.preventDefault(); setFilter(filters[next].value);
      (event.currentTarget.parentElement?.querySelectorAll('button')[next] as HTMLButtonElement | undefined)?.focus();
    }}><span>{item.label}</span><span className="cps-tab-count">{candidate.applications.filter(application => applicationGroup(application) === item.value).length}</span>{filter === item.value && <motion.span layoutId="search-tab" className="cps-tab-indicator"/>}</button>)}</div>
    <div id={`${scope}-results`} role="tabpanel" aria-labelledby={`${scope}-tab-${filter}`}>
      {paused && filter !== 'history' ? <Paused props={props}/> : <>
        <header className="cps-heading"><div><span className="cps-eyebrow">Поиск работы</span><h1>{filter === 'new' ? 'Место для нового.' : filter === 'process' ? 'На шаг ближе.' : 'Ваш пройденный путь.'}</h1><p>{items.length ? countLabel(items.length) : filter === 'new' ? 'Предложения появятся здесь.' : filter === 'process' ? 'Здесь продолжатся ваши знакомства.' : 'Здесь сохраняются завершённые процессы.'}</p></div><button type="button" className="cps-refresh" disabled={busy || refreshing} aria-label="Обновить вакансии" onClick={() => void refresh()}><RefreshCw size={18} className={refreshing ? 'is-refreshing' : ''}/></button></header>
        {refreshError && <p className="cps-error" role="alert">{refreshError}</p>}
        {paused && <div className="cps-paused-note"><Pause size={16}/><span>Поиск приостановлен. История сохранена.</span><button type="button" onClick={() => setFilter('new')}>Вернуться к поиску<ArrowRight size={14}/></button></div>}
        {candidate.autoApprove && !paused && <p className="cps-auto-note">Автосогласование вакансий включено. Время каждой встречи подтверждаете вы. <button type="button" onClick={onProfile}>Настроить</button></p>}
        {items.length ? <div className="cps-grid">{items.map(application => <ApplicationCard key={application.id} application={application} props={props} scope={scope}/>)}</div> : <div className="cps-empty"><BriefcaseBusiness size={28}/><h2>{filter === 'new' ? 'Возможности ещё впереди' : filter === 'process' ? 'Всё начинается с интереса' : 'История пока чиста'}</h2><p>{filter === 'new' ? 'Когда появятся новые вакансии, вы сможете изучить их и решить, хотите ли участвовать.' : filter === 'process' ? 'Выберите «Хочу участвовать» в новой вакансии. Все следующие шаги будут здесь.' : 'Завершённые и приостановленные процессы останутся в этой вкладке.'}</p>{filter === 'process' && candidate.applications.some(application => applicationGroup(application) === 'new') ? <ActionButton secondary onClick={() => setFilter('new')}>Посмотреть предложения<ArrowRight size={15}/></ActionButton> : filter === 'new' && <ActionButton secondary onClick={onProfile}>Мои предпочтения<ArrowRight size={15}/></ActionButton>}</div>}
      </>}
    </div>
  </section></LayoutGroup></MotionConfig>;
}
