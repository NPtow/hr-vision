import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { LayoutGroup, MotionConfig, motion, useReducedMotion } from 'motion/react';
import { ArrowRight, ArrowUpRight, CalendarDays, Check, ChevronDown, Clock3, Play, Video } from 'lucide-react';
import type { CandidateApplication, CandidateOffer, CandidateState, CandidateWorkflowCommand } from './api';
import { timeZone } from './api';
import { applicationAfterInterest, applicationGroup, applicationHistory, applicationOffer, applicationRevision, applicationStatus, canReplyToMeeting, currentApplicationMeeting, meetingInterval, validDate, workspaceDateTime, type ApplicationHistoryItem } from './workspace-model';
import { applicationCardTone, applicationMeetingIsJoinable, expandedSearchApplications, orderedSearchApplications, vacancyCardDetails } from './candidate-search-model';
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
  const isNew = applicationGroup(application) === 'new';
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
  if (history) actions = null;
  else if (isNew) actions = <div className="cps-actions"><ActionButton disabled={disabled} onClick={() => void run(() => onDecision(application.id, 'accepted'))}>Мне интересно</ActionButton><ActionButton secondary disabled={disabled} onClick={() => void run(() => onDecision(application.id, 'declined'))}>Не подходит</ActionButton></div>;
  else if (offer?.status === 'sent') actions = offerReply ? <div className="cps-offer-confirm" role="group" aria-label="Подтверждение ответа на оффер"><h4>{offerReply === 'accepted' ? 'Принять оффер на этих условиях?' : 'Отклонить предложение?'}</h4><p>{offer.role} · {application.vacancy.company}. Работодатель получит ваш ответ на версию {offer.version}.</p><div className="cps-actions"><ActionButton disabled={disabled} onClick={() => void workflow({ applicationId: application.id, action: 'offer.reply', version: offer.version, value: offerReply })}>{offerReply === 'accepted' ? 'Да, принимаю' : 'Да, отклоняю'}<Check size={16}/></ActionButton><ActionButton secondary disabled={disabled} onClick={() => setOfferReply(null)}>Вернуться к условиям</ActionButton></div></div>
    : <div className="cps-actions"><ActionButton disabled={disabled} onClick={() => setOfferReply('accepted')}>Принять оффер<ArrowUpRight size={16}/></ActionButton><ActionButton secondary disabled={disabled} onClick={() => setOfferReply('declined')}>Отклонить</ActionButton></div>;
  else if (meeting?.status === 'pending') actions = meetingReplyAvailable
    ? <div className="cps-actions"><ActionButton disabled={disabled} onClick={() => meetingReply('confirm')}>Подтвердить время<Check size={16}/></ActionButton><ActionButton secondary disabled={disabled} onClick={() => meetingReply('decline')}>Время не подходит</ActionButton></div>
    : <p className="cps-waiting"><Clock3 size={16}/>Ожидаем новое время от команды</p>;
  else if (meeting && ['confirmed', 'live'].includes(meeting.status)) actions = <>
    {props.candidate.videoConfigured && applicationMeetingIsJoinable(application) ? <div className="cps-actions"><ActionButton disabled={disabled} onClick={() => onJoin(application.id, meeting.id)}>Войти во встречу<Video size={16}/></ActionButton>{meeting.status === 'confirmed' && meetingReplyAvailable && <ActionButton secondary disabled={disabled} onClick={() => meetingReply('decline')}>Время не подходит</ActionButton>}</div>
      : <><p className="cps-waiting">{!props.candidate.videoConfigured ? 'Видеосвязь пока недоступна. Время встречи сохранено.' : (validDate(meeting.start)?.getTime() ?? 0) > Date.now() ? 'Вход откроется за 15 минут до встречи.' : 'Время встречи закончилось.'}</p>{meeting.status === 'confirmed' && meetingReplyAvailable && <div className="cps-actions"><ActionButton secondary disabled={disabled} onClick={() => meetingReply('decline')}>Время не подходит</ActionButton></div>}</>}
  </>;
  else if (meeting?.status === 'completed' && applicationAfterInterest(application) === 'pending') actions = <div className="cps-actions"><ActionButton disabled={disabled} onClick={() => void workflow({ applicationId: application.id, action: 'after', meetingId: meeting.id, value: 'yes' })}>Хочу продолжить<ArrowRight size={16}/></ActionButton><ActionButton secondary disabled={disabled} onClick={() => void workflow({ applicationId: application.id, action: 'after', meetingId: meeting.id, value: 'no' })}>Не продолжать</ActionButton></div>;
  else actions = null;

  return <section className={`cps-next${isNew ? ' cps-next-new' : ''}`}>
    {!isNew && <><span className="cps-eyebrow">{history ? 'Результат' : status.attention ? 'Ваш следующий шаг' : 'Сейчас'}</span><h3>{!history && !meeting && !offer ? 'Ждём команду' : status.label}</h3><p className="cps-next-description">{status.detail}</p></>}
    {!isNew && !history && offer?.status !== 'sent' && meeting && ['pending', 'confirmed', 'live', 'completed'].includes(meeting.status) && <div className="cps-meeting"><CalendarDays size={19}/><div><strong>{meetingInterval(meeting)}</strong><span>{timeZone()}</span></div></div>}
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

function VacancyDescription({ application }: { application: CandidateApplication }) {
  const { description, facts, sections } = vacancyCardDetails(application.vacancy);
  return <div className="cps-vacancy-description">
    {facts.length > 0 && <dl className="cps-vacancy-facts">{facts.map(fact => <div key={fact.label}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}</dl>}
    {description && <section className="cps-about"><h3>О работе</h3><p>{description}</p></section>}
    {sections.length > 0 && <div className="cps-vacancy-sections">{sections.map(section => <section className="cps-about" key={section.title}><h3>{section.title}</h3><p>{section.text}</p></section>)}</div>}
    {!description && !facts.length && !sections.length && <p className="cps-empty-description">Описание вакансии пока не добавлено.</p>}
  </div>;
}

function ApplicationCard({ application, props, scope }: { application: CandidateApplication; props: CandidateSearchProps; scope: string }) {
  const reduced = useReducedMotion();
  const card = useRef<HTMLElement>(null);
  const title = useRef<HTMLHeadingElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef(false);
  const offer = applicationOffer(application);
  const open = props.selectedId === application.id;
  const tone = applicationCardTone(application);
  const isNew = applicationGroup(application) === 'new';
  const contentId = `${scope}-${application.id}`;
  const titleId = `${contentId}-title`;
  const statusId = `${contentId}-status`;
  const actionKey = `${application.id}:${applicationRevision(application)}:${application.status}:${offer?.id}:${offer?.version}:${offer?.status}:${application.archivedAt || ''}`;
  const onSelect = props.onSelect;

  useLayoutEffect(() => {
    if (open) title.current?.focus({ preventScroll: true });
    else if (restoreFocus.current) {
      trigger.current?.focus({ preventScroll: true });
      restoreFocus.current = false;
    }
  }, [open]);
  useEffect(() => {
    if (!open) return;
    function clickOutside(event: MouseEvent) {
      const target = event.target;
      // A synchronous action can replace its own button before the document handler.
      // The original event path still identifies that click as inside this card.
      if (!(target instanceof Node) || event.composedPath().includes(card.current!) || card.current?.contains(target)) return;
      // Another card opens itself; closing here would overwrite that same click.
      if (target instanceof Element && target.closest('.cps-card-trigger')) return;
      onSelect('');
    }
    function escape(event: KeyboardEvent) {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      event.preventDefault();
      restoreFocus.current = true;
      onSelect('');
    }
    document.addEventListener('click', clickOutside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('click', clickOutside); document.removeEventListener('keydown', escape); };
  }, [open, onSelect]);

  return <motion.article ref={card} layout={!reduced} initial={{ opacity: 0, y: reduced ? 0 : 8 }} animate={{ opacity: 1, y: 0 }}
    transition={{ layout: { type: 'spring', stiffness: 380, damping: 38, mass: .8 } }}
    style={{ borderRadius: 28 }} className={`cps-card cps-tone-${tone}${open ? ' is-open' : ''}${tone === 'offer' && offer?.status === 'sent' ? ' cps-offer-shimmer' : ''}`} aria-labelledby={titleId}>
    {tone === 'offer' && <span className="cps-offer-spectrum" aria-hidden="true"/>}
    <div className="cps-card-face">
      <div className="cps-card-summary">
        <motion.h2 layout={reduced ? false : 'position'} id={titleId} ref={title} tabIndex={open ? -1 : undefined}>
          {open ? application.vacancy.title : <button ref={trigger} type="button" className="cps-card-trigger" aria-expanded={false} aria-describedby={statusId} onClick={() => onSelect(application.id)}>{application.vacancy.title}</button>}
        </motion.h2>
        <motion.p layout={reduced ? false : 'position'} className="cps-company-name">{application.vacancy.company}</motion.p>
        <motion.p layout={reduced ? false : 'position'} className="cps-salary">{application.vacancy.salary?.trim() || 'Зарплата не указана'}</motion.p>
        <span id={statusId} className="cps-sr-only">{applicationStatus(application).label}</span>
      </div>
      {open && <motion.div id={contentId} className="cps-card-body" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: reduced ? 0 : .18, delay: reduced ? 0 : .08 }}>
        <div className="cps-card-description">
          {isNew ? <VacancyDescription application={application}/> : <>
            {offer && !['withdrawn', 'superseded'].includes(offer.status) && <OfferTerms offer={offer}/>}
            <details className="cps-job-details"><summary>О вакансии<ChevronDown size={15} aria-hidden="true"/></summary><VacancyDescription application={application}/></details>
          </>}
        </div>
        <div className="cps-card-aside">
          <ApplicationActions key={actionKey} application={application} props={props}/>
          {!isNew && <History application={application} onJoin={props.onJoin}/>}
        </div>
      </motion.div>}
    </div>
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
  const guideKey = `hrv-search-glow-guide:v2:${candidate.id}`;
  const [showGuide, setShowGuide] = useState(() => {
    try { return localStorage.getItem(guideKey) !== 'seen'; } catch { return true; }
  });
  const firstCard = useRef<HTMLDivElement>(null);
  const [columns, setColumns] = useState(3);
  const ordered = orderedSearchApplications(candidate.applications);
  const [selectionOrder, setSelectionOrder] = useState(() => ordered.map(application => application.id));
  // A saved answer may change priority. Keep the open card in place until it closes.
  const positions = new Map(selectionOrder.map((id, index) => [id, index]));
  const stable = props.selectedId ? [...ordered].sort((left, right) => (positions.get(left.id) ?? Infinity) - (positions.get(right.id) ?? Infinity)) : ordered;
  const items = expandedSearchApplications(stable, props.selectedId, columns);
  const cardProps = { ...props, onSelect: (id: string) => {
    setSelectionOrder(ordered.map(application => application.id));
    props.onSelect(id);
  } };
  useLayoutEffect(() => {
    const grid = firstCard.current;
    if (!grid) return;
    const resize = () => {
      const width = grid.getBoundingClientRect().width;
      setColumns(width <= 600 ? 1 : width <= 1030 ? 2 : 3);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(grid);
    return () => observer.disconnect();
  }, [candidate.applications.length]);
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
      <ul><li><i className="cps-guide-new" aria-hidden="true"/>Новое</li><li><i className="cps-guide-action" aria-hidden="true"/>Ваш ход</li><li><i className="cps-guide-progress" aria-hidden="true"/>Ждём команду</li><li><i className="cps-guide-closed" aria-hidden="true"/>Завершено</li><li><i className="cps-guide-offer" aria-hidden="true"/>Оффер</li></ul>
      <button type="button" onClick={dismissGuide}>Понятно<Check size={16} aria-hidden="true"/></button>
    </aside>}
    {items.length > 0 && <div className="cps-grid" ref={firstCard} aria-label="Предложения работы">{items.map(application => <ApplicationCard key={application.id} application={application} props={cardProps} scope={scope}/>)}</div>}
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
