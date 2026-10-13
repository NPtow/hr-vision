import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, CalendarDays, Check, ChevronRight, MessageCircle, Plus, RotateCcw, Users, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '../../components/ui/dialog';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../../components/ui/tabs';
import { candidates } from '../agency-product/model';
import { InterviewReview } from '../interview-review/InterviewReview';
import { InterviewMediaProvider, LabMediaControl } from '../interview-review/InterviewMediaContext';
import { EmployerShell, type Scope, type EmployerPage } from '../employer-workspace/EmployerShell';
import { EmployerAuth } from '../employer-workspace/EmployerAuth';
import { VacancyCreate } from '../employer-workspace/VacancyCreate';
import { captureEmployerEntry, clearEmployerEntry } from '../../lib/employer-entry';
import { Avatar, PeoplePanel } from '../employer-workspace/PeoplePanel';
import { CandidateIntake, IntakeDetail } from '../employer-workspace/CandidateIntake';
import { useIntake } from '../employer-workspace/intake';
import { MeetingScheduler } from '../employer-workspace/MeetingScheduler';
import { EmployerAgenda } from '../employer-workspace/EmployerAgenda';
import { CandidateStageWorkspace } from '../employer-workspace/CandidateStageWorkspace';
import { DsaCandidates } from './DsaCandidates';
import { MeetingPanel } from './MeetingPanel';
import { Materials } from './MeetingMaterials';
import { EmployerScreening } from './EmployerScreening';
import { FeedbackGate, answerLabels } from './FeedbackGate';
import { actorName, initials, candidateStatus as status, meetingLabels, offerLabels, dateTime, request, useEntryAccess, useJourney, type AccountList, type Actor, type CandidateId, type Journey, type Offer, type Person, type SourceProfile, type Vacancy } from './api';
import '../agency-product/theme.css';
import '../agency-product/product.css';
import './connected.css';
import '../employer-workspace/employer.css';

type Page = EmployerPage;
const entryPage = (employer = false): Page => {
  const query = new URLSearchParams(location.search);
  if (employer) {
    const screen = query.get('screen');
    return screen === 'panel' ? 'shortlist' : ['create-vacancy', 'shortlist', 'meetings', 'meeting', 'review', 'pool', 'offer', 'chat', 'book'].includes(screen || '') ? screen as Page : 'tasks';
  }
  return query.get('task') === 'dsa' ? 'dsa' : query.get('screen') === 'panel' ? 'shortlist' : 'tasks';
};
const personList = (j: Journey) => Object.values(j.state!.candidates) as Person[];

export function ConnectedJourney({ initialRole = 'start' }: { initialRole?: 'start' | 'manager' | 'candidate' }) {
  const realEmployer = initialRole !== 'candidate' && new URLSearchParams(location.search).get('legacy') !== '1';
  const [siteEntry, setSiteEntry] = useState(() => {
    if (!realEmployer) return null;
    const url = new URL(location.href);
    const entry = captureEmployerEntry(url, sessionStorage);
    if (url.href !== location.href) history.replaceState(history.state, '', url);
    return entry;
  });
  const [scope, setScope] = useState<Scope>(!realEmployer && new URLSearchParams(location.search).get('task') === 'dsa' ? 'dsa' : 'sfera');
  const journey = useJourney(scope, { employer: realEmployer });
  const { state, error, busy, login, act, setError } = journey;
  const access = useEntryAccess({ employer: realEmployer });
  const [picker, setPicker] = useState(!realEmployer && initialRole === 'start');
  const [page, setPage] = useState<Page>(initialRole === 'candidate' ? 'vacancies' : entryPage(realEmployer));
  const [panelMode, setPanelMode] = useState<'proposed' | 'selected'>(new URLSearchParams(location.search).get('pool') === 'selected' ? 'selected' : 'proposed');
  const [accounts, setAccounts] = useState<AccountList | null>(null);
  const [accountsError, setAccountsError] = useState('');
  const [accountsAttempt, setAccountsAttempt] = useState(0);
  const intake = useIntake(state?.actor === 'manager' && (!realEmployer || state.vacancy.id) ? journey.token : '', scope, realEmployer ? state?.vacancy.id : undefined);
  const [intakeSelected, setIntakeSelected] = useState('');
  const [vacancyLoading, setVacancyLoading] = useState(false);
  const vacancySwitch = useRef(false);
  const historyTarget = useRef<URL | null>(null);
  const historyRestoring = useRef(false);
  const [historyVersion, setHistoryVersion] = useState(0);
  const [selected, setSelected] = useState<CandidateId>(realEmployer ? new URLSearchParams(location.search).get('applicationId') || '' : 'anna');
  const [resetOpen, setResetOpen] = useState(false);
  const [callActive, setCallActive] = useState(false);
  const [toast, setToast] = useState('');
  const choseAccount = useRef(false);
  const generation = useRef({ scope, vacancyId: '', id: '' });
  const autoEnter = useRef(false);
  const siteEntryOpened = useRef(false);
  function openVacancyEditor() {
    setPage('create-vacancy'); setError('');
    const url = new URL(location.href);
    url.searchParams.set('screen', 'create-vacancy');
    url.searchParams.delete('applicationId'); url.searchParams.delete('pool');
    url.searchParams.delete('stageId'); url.searchParams.delete('meetingId');
    history.replaceState(history.state, '', url);
  }
  useEffect(() => {
    if (!realEmployer || !state || !siteEntry?.createVacancy || siteEntryOpened.current) return;
    siteEntryOpened.current = true;
    openVacancyEditor();
  }, [realEmployer, state, siteEntry]);
  function finishSiteEntry() {
    clearEmployerEntry(sessionStorage);
    setSiteEntry(current => current ? { ...current, createVacancy: false, description: '' } : null);
  }
  useEffect(() => {
    if (!state) return;
    if (generation.current.scope === state.scope && (!realEmployer || generation.current.vacancyId === state.vacancy.id) && generation.current.id && generation.current.id !== state.generation) {
      setPage(state.actor === 'manager' ? 'tasks' : 'vacancies');
      setToast('Команда начала прохождение заново');
    }
    generation.current = { scope: state.scope, vacancyId: state.vacancy.id || '', id: state.generation };
  }, [state?.generation, state?.vacancy.id, realEmployer]);
  useEffect(() => {
    if (realEmployer || access.status !== 'ready') return;
    let current = true;
    setAccounts(null); setAccountsError('');
    request<AccountList>(realEmployer ? 'employer/accounts' : 'accounts', realEmployer ? journey.token || undefined : undefined, realEmployer ? undefined : { scope }).then(result => { if (current) setAccounts(result); }).catch(e => { if (current) setAccountsError((e as Error).message); });
    return () => { current = false; };
  }, [access.status, scope, accountsAttempt, realEmployer, journey.token]);
  useEffect(() => {
    if (!realEmployer && access.status === 'ready' && !journey.token && initialRole === 'manager' && !autoEnter.current) {
      autoEnter.current = true;
      void enter('manager');
    }
  }, [access.status, journey.token, initialRole, realEmployer]);
  useEffect(() => { if (!toast) return; const id = setTimeout(() => setToast(''), 4500); return () => clearTimeout(id); }, [toast]);
  useEffect(() => { if (!choseAccount.current && state && initialRole !== 'start' && ((initialRole === 'manager') !== (state.actor === 'manager'))) setPicker(true); }, [state?.actor, initialRole]);
  async function enter(actor: Actor) { choseAccount.current = true; setIntakeSelected(''); if (await login(actor)) { setPicker(false); setPage(actor === 'manager' ? entryPage(realEmployer) : 'vacancies'); if (actor !== 'manager') setSelected(actor); } }
  async function copyEntry() {
    try {
      const link = request<{ url: string }>('invite', journey.token, { scope });
      // Start the clipboard operation inside the click, including in Safari.
      if (typeof ClipboardItem !== 'undefined') {
        await navigator.clipboard.write([new ClipboardItem({ 'text/plain': link.then(({ url }) => new Blob([url], { type: 'text/plain' })) })]);
      } else {
        await navigator.clipboard.writeText((await link).url);
      }
      setToast('Ссылка на вход скопирована');
    } catch { setError('Не удалось скопировать ссылку. Попробуйте ещё раз.'); }
  }
  const manager = state?.actor === 'manager';
  const people = state ? personList(journey) : [];
  const panelPeople = realEmployer ? people.filter(p => p.reviewState === panelMode) : people;
  const shownPeople = realEmployer && page === 'shortlist' ? panelPeople : people;
  const person = state ? (realEmployer && manager && selected ? shownPeople.find(p => p.id === selected)
    : shownPeople.find(p => p.id === (manager ? selected : state.actor as CandidateId)) || shownPeople[0]) : undefined;
  const stageWorkspace = realEmployer && manager && ((page === 'shortlist' && panelMode === 'selected') || ['meeting', 'review', 'offer'].includes(page));
  const select = (id: CandidateId, next: Page) => {
    setSelected(id); setPage(next);
    if (realEmployer) {
      const url = new URL(location.href);
      if (id) url.searchParams.set('applicationId', id); else url.searchParams.delete('applicationId');
      url.searchParams.set('screen', next); url.searchParams.delete('stageId'); url.searchParams.delete('meetingId');
      history.replaceState(history.state, '', url);
    }
  };
  const meetings = realEmployer ? state?.meetings || [] : people.filter(p => p.meeting && p.meeting.status !== 'cancelled').sort((a,b) => a.meeting!.start.localeCompare(b.meeting!.start));
  const pool = realEmployer ? state?.offerCandidates || [] : people.filter(p => p.decision === 'pool');
  const pendingFeedback = manager && !realEmployer ? people.filter(p => p.meeting?.status === 'completed' && !p.feedback).sort((a, b) => (a.meeting!.endedAt || a.meeting!.start).localeCompare(b.meeting!.endedAt || b.meeting!.start)) : [];
  const feedbackPerson = !callActive && page !== 'create-vacancy' ? pendingFeedback[0] : undefined;
  const inEmployer = !!manager && (realEmployer || !picker);
  const panel: Page = scope === 'dsa' ? 'dsa' : 'shortlist';
  const vacancyOptions = realEmployer ? (state?.vacancies || []).flatMap(v => v.id ? [{ id: v.id, role: v.role }] : []) : [];
  async function switchVacancy(id: string) {
    if (vacancySwitch.current || busy || callActive || id === state?.vacancy.id || !vacancyOptions.some(v => v.id === id)) return;
    vacancySwitch.current = true;
    setVacancyLoading(true); setSelected(''); setIntakeSelected('');
    try {
      if (!await journey.openVacancy(id)) return;
      setPanelMode('proposed'); setPage('shortlist');
      const url = new URL(location.href);
      url.searchParams.set('screen', 'shortlist'); url.searchParams.set('pool', 'proposed');
      url.searchParams.delete('applicationId'); url.searchParams.delete('stageId'); url.searchParams.delete('meetingId');
      history.replaceState(history.state, '', url);
    } finally { vacancySwitch.current = false; setVacancyLoading(false); }
  }
  async function openTask(vacancy: Vacancy) {
    if (!vacancy.id || !await journey.openVacancy(vacancy.id)) return;
    const mode = 'selected';
    setPanelMode(mode); setIntakeSelected(''); select('', 'shortlist');
    const url = new URL(location.href); url.searchParams.set('pool', mode);
    history.replaceState(history.state, '', url);
  }
  async function openPerson(person: Person, next: Page) {
    if (realEmployer && person.vacancyId && person.vacancyId !== state?.vacancy.id && !await journey.openVacancy(person.vacancyId)) return;
    if (realEmployer) setPanelMode('selected');
    select(person.id, next);
    if (realEmployer) {
      const url = new URL(location.href); url.searchParams.set('pool', 'selected');
      if (next === 'meeting' && person.meeting) {
        url.searchParams.set('meetingId', person.meeting.id); url.searchParams.set('stageId', `meeting:${person.meeting.id}`);
      }
      if (next === 'offer' && person.offer?.id) url.searchParams.set('stageId', `offer:${person.offer.id}`);
      history.replaceState(history.state, '', url);
    }
  }
  const navigate = async (next: Page) => {
    if (busy) return;
    if (realEmployer) {
      if (page === 'create-vacancy' && next !== 'create-vacancy') finishSiteEntry();
      if (next === 'shortlist' || next === 'dsa') setPanelMode('proposed');
      setIntakeSelected(''); select(next === 'shortlist' ? '' : selected, next === 'dsa' ? 'shortlist' : next);
      const url = new URL(location.href); if (next === 'shortlist' || next === 'dsa') url.searchParams.set('pool', 'proposed');
      history.replaceState(history.state, '', url);
      return;
    }
    const targetScope = next === 'dsa' ? 'dsa' : next === 'shortlist' ? 'sfera' : scope;
    if (targetScope !== scope) {
      if (!await login('manager', targetScope)) return;
      setScope(targetScope);
      const url = new URL(location.href);
      if (targetScope === 'dsa') url.searchParams.set('task', 'dsa'); else url.searchParams.delete('task');
      history.replaceState(history.state, '', url);
    }
    setIntakeSelected(''); setPage(next);
  };
  useEffect(() => {
    if (!realEmployer) return;
    const restore = () => {
      historyTarget.current = new URL(location.href);
      setHistoryVersion(version => version + 1);
    };
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, [realEmployer]);
  useEffect(() => {
    const target = historyTarget.current;
    if (!target || busy || callActive || historyRestoring.current) return;
    historyRestoring.current = true; setVacancyLoading(true);
    void (async () => {
      try {
        const query = target.searchParams;
        const vacancyId = query.get('vacancyId');
        // A historic URL must load its own application scope before opening a card.
        // Do not overwrite the URL: another Back/Forward may arrive during the read.
        const opened = !vacancyId || vacancyId === state?.vacancy.id || await journey.openVacancy(vacancyId, { updateLocation: false });
        if (historyTarget.current !== target) return;
        const screen = query.get('screen');
        setPage(opened ? screen === 'panel' ? 'shortlist' : ['create-vacancy', 'shortlist', 'meetings', 'meeting', 'review', 'pool', 'offer', 'chat', 'book'].includes(screen || '') ? screen as Page : 'tasks' : 'shortlist');
        setPanelMode(!opened || query.get('pool') === 'selected' ? 'selected' : 'proposed');
        setSelected(opened ? query.get('applicationId') || '' : '__unavailable_history__');
        setIntakeSelected(''); historyTarget.current = null; setVacancyLoading(false);
      } finally {
        historyRestoring.current = false;
        if (historyTarget.current && historyTarget.current !== target) setHistoryVersion(version => version + 1);
      }
    })();
  }, [historyVersion, busy, callActive]);
  if (realEmployer && journey.initializing) return <div className="ew-auth"><main className="ew-auth-main"><p className="ew-auth-card" role="status">Открываем кабинет…</p></main></div>;
  if (realEmployer && !state) return <EmployerAuth initialMode={siteEntry?.mode} busy={busy} error={error} onModeChange={() => setError('')} onSubmit={async (mode, values) => {
    if (!await journey.authenticate(mode, values)) return false;
    setPicker(false); setSelected(''); setIntakeSelected(''); setToast('');
    if (page === 'create-vacancy' || siteEntry?.createVacancy) openVacancyEditor(); else setPage('tasks');
    return true;
  }}/>;
  return <InterviewMediaProvider><div className={`agency-product agency-product-theme cj-app ${inEmployer ? 'ew-root' : ''}`}><EmployerShell enabled={inEmployer} page={page} scope={scope} vacancy={state?.vacancy.role || (realEmployer ? 'Кандидаты' : 'Менеджер по работе с клиентами')} vacancyId={state?.vacancy.id} vacancies={vacancyOptions} onVacancyChange={realEmployer ? id => { void switchVacancy(id); } : undefined} navigationBusy={busy || vacancyLoading || callActive} name={state?.actorName || ''} hasOffers={pool.length > 0} candidateContext={realEmployer ? stageWorkspace ? 'vacancy' : 'queue' : undefined} candidateNavigation={stageWorkspace && people.filter(p => p.reviewState === 'selected').length > 0 ? <nav className="esc-candidate-nav" aria-label="Кандидаты в работе">{people.filter(p => p.reviewState === 'selected').map(p => <button type="button" className="esc-candidate-switch" key={p.id} aria-label={p.name} title={p.name} aria-pressed={person?.id === p.id} disabled={busy || callActive} onClick={() => { setPanelMode('selected'); select(p.id, 'shortlist'); }}><Avatar name={p.name} photo={p.photo}/></button>)}</nav> : undefined} onNavigate={navigate} onAccount={() => setPicker(true)} accountBusy={busy || callActive} onLogout={realEmployer ? async () => { if (await journey.logout()) { finishSiteEntry(); setSiteEntry({ mode: 'login', createVacancy: false, description: '' }); setPage('tasks'); setSelected(''); setIntakeSelected(''); setToast(''); } } : undefined}>
    <header className="cj-header"><button className="cj-brand" onClick={() => setPicker(true)}>HR Vision<span>.</span></button>{state && !picker && <button className="cj-account" onClick={() => setPicker(true)}><span className="cj-avatar">{initials(state.actorName)}</span>{state.actorName}<ChevronRight size={14}/></button>}</header>
    {error && page !== 'create-vacancy' && !stageWorkspace && <div className="cj-error" role="alert"><span>{error}</span><Button variant="ghost" size="icon-sm" aria-label="Закрыть ошибку" onClick={() => setError('')}><X size={16}/></Button></div>}
    {(picker || !state) ? <main className="cj-start">
      <h1>Выберите аккаунт</h1>
      {access.status === 'checking' && <p role="status">Открываем аккаунты…</p>}
      {access.status === 'missing' && <div className="cj-entry-notice" role="status"><h2>Откройте актуальную ссылку HR Vision из чата</h2><p>Она сразу откроет готовые аккаунты. После первого входа доступ сохранится в этом браузере.</p></div>}
      {access.status === 'error' && <div className="cj-entry-notice" role="alert"><p>Не удалось открыть аккаунты. Проверьте соединение и попробуйте ещё раз.</p><Button variant="outline" onClick={() => { void access.check(); }}>Попробовать снова</Button></div>}
      {access.status === 'ready' && !accounts && (accountsError ? <div className="cj-entry-notice" role="alert"><p>{accountsError}</p><Button variant="outline" onClick={() => setAccountsAttempt(n => n + 1)}>Попробовать снова</Button></div> : <p role="status">Загружаем аккаунты…</p>)}
      {access.status === 'ready' && accounts && <div className="cj-account-grid">
        {accounts.accounts.map(c => <button key={c.id} disabled={busy} onClick={() => enter(c.id)} className="cj-account-card"><span className="cj-avatar">{initials(c.name)}</span><small>{c.id === 'manager' ? `Работодатель · ${accounts.company}` : 'Кандидат'}</small><h2>{c.name}</h2>{c.role && c.id !== 'manager' && <p>{c.role}</p>}<span className="cj-card-link">{c.id === 'manager' ? 'Войти работодателем' : 'Войти кандидатом'}<ArrowRight size={17}/></span></button>)}
      </div>}
    </main> : feedbackPerson ? <FeedbackGate key={feedbackPerson.meeting!.id} journey={journey} person={feedbackPerson} remaining={pendingFeedback.length} onSaved={() => select(feedbackPerson.id, 'review')}/> : manager && page === 'dsa' ? <DsaCandidates journey={journey} selected={person} onSelect={setSelected} onOpen={next => { if (person) select(person.id, next); }} onAdd={() => setPage('intake')} intake={intake} intakeSelected={intakeSelected} onIntakeSelect={setIntakeSelected}/> : <>
      {!manager && <div className="cj-context"><button onClick={() => setPage(manager ? 'tasks' : 'vacancies')}>{manager ? 'Задачи найма' : 'Подходящие вакансии'}</button><ChevronRight size={14}/><strong>{state.vacancy.role}</strong><details><summary>Задача и условия</summary><div><h3>{state.vacancy.problem}</h3><p>{state.vacancy.expectations}</p><p>{state.vacancy.salary} · {state.vacancy.format}</p></div></details></div>}
      {!manager && <nav className="cj-nav" aria-label="Этапы найма">
        {(manager ? [['shortlist',`Подборка · ${people.length}`], ...(meetings.length ? [['meetings',`Встречи · ${meetings.length}`]] : []), ...(person?.feedback ? [['review', 'Разбор встречи']] : []), ...(pool.length ? [['pool',`Пул для оффера · ${pool.length}`]] : [])] : [['vacancies','Вакансии'], ['schedule','Моё расписание'], ...(person?.meeting ? [['meeting','Встреча']] : []), ...(person?.offer ? [['offer','Оффер']] : [])]).map(([key,label]) => <button key={key} aria-current={page === key || (key === 'meetings' && page === 'meeting') ? 'page' : undefined} onClick={() => setPage(key as Page)}>{label}</button>)}
        {person?.interest === 'accepted' && <button aria-current={page === 'chat' ? 'page' : undefined} onClick={() => setPage('chat')}><MessageCircle size={14}/>Чат</button>}
      </nav>}
      {state.closedBy && page !== 'create-vacancy' && page !== 'meetings' && <div className="cj-success"><Check size={18}/><span>{manager ? `${actorName(state, state.closedBy)} принял(а) оффер. Подбор завершён.` : state.closedBy === state.actor ? 'Вы приняли оффер. Поздравляем с новым этапом!' : 'Компания завершила подбор по этой вакансии.'}</span></div>}
      {manager && page === 'tasks' && <main className="cj-main">
        <div className="cj-section-heading ew-tasks-heading"><h1>Задачи найма</h1>{realEmployer && <Button disabled={busy} onClick={openVacancyEditor}><Plus size={16}/>Создать вакансию</Button>}</div>
        {realEmployer ? (state.vacancies?.length ? state.vacancies.map(vacancy => <article className="cj-task" key={vacancy.id}><div><small>{vacancy.company}</small><h2>{vacancy.problem || vacancy.role}</h2>{vacancy.problem && <p>{vacancy.role}</p>}</div><Button disabled={busy} onClick={() => { void openTask(vacancy); }}>{vacancy.selectedCount ? 'Посмотреть выбранных кандидатов' : 'Посмотреть кандидатов'}<ArrowRight size={16}/></Button></article>) : <section className="cj-empty"><h2>Пока нет вакансий</h2><p>Создайте первую вакансию, чтобы начать подбор.</p></section>) : <><article className="cj-task"><div><small>ДСА Инжиниринг</small><h2>Менеджер по работе с клиентами</h2></div><Button disabled={busy} onClick={() => navigate('dsa')}>Открыть кандидатов ДСА<ArrowRight size={16}/></Button></article><article className="cj-task"><div><small>Сфера</small><h2>Вернуть клиентов к повторным покупкам</h2><p>Менеджер по работе с клиентами</p></div><Button disabled={busy} onClick={() => navigate('shortlist')}>Открыть подборку<ArrowRight size={16}/></Button></article></>}
      </main>}
      {manager && page === 'shortlist' && !stageWorkspace && (realEmployer && !state.vacancy.id ? <main className="cj-main"><section className="cj-empty"><h2>Пока нет вакансий</h2><p>Создайте вакансию, чтобы посмотреть кандидатов.</p><Button onClick={openVacancyEditor}>Создать вакансию</Button></section></main> : vacancyLoading || (intake.loading && !intake.people.length) ? <main className="cj-main ew-vacancy-loading" role="status" aria-live="polite">Загружаем кандидатов…</main> : <Shortlist key={state.vacancy.id || scope} journey={journey} person={person} people={panelPeople} select={select} go={setPage} intake={intake} intakeSelected={intakeSelected} onIntakeSelect={setIntakeSelected}/>)}
      {stageWorkspace && (vacancyLoading ? <main className="cj-main" role="status">Загружаем кандидата…</main> : person ? <CandidateStageWorkspace key={`${state.generation}:${person.id}`} journey={journey} person={person} source={sourceFor(person)} entry={page === 'meeting' || page === 'review' || page === 'offer' ? page : undefined} callActive={callActive} onCallChange={setCallActive} onChat={() => select(person.id, 'chat')}/> : <main className="cj-main"><section className="esc-card esc-unavailable"><h2>{selected ? 'Кандидат недоступен' : 'Выбранных кандидатов пока нет'}</h2><p>{selected ? error || 'Этого кандидата нет в выбранной вакансии.' : 'После приглашения на встречу кандидат появится здесь со своей историей этапов.'}</p><Button onClick={() => { void navigate('shortlist'); }}>К подборке кандидатов</Button></section></main>)}
      {manager && page === 'intake' && <CandidateIntake token={journey.token} scope={scope} vacancy={state.vacancy.role} onBack={() => setPage(scope === 'dsa' ? 'dsa' : 'shortlist')} onSaved={id => { setIntakeSelected(id); if (realEmployer) { setPanelMode('proposed'); void journey.refresh(); } void intake.refresh(); setPage(scope === 'dsa' ? 'dsa' : 'shortlist'); setToast('Кандидаты добавлены'); }}/>}
      {manager && page === 'book' && <MeetingScheduler journey={journey} person={person} onSelect={setSelected} onBack={() => setPage(panel)} onBooked={() => { if (realEmployer) { setPanelMode('selected'); select(person?.id || '', 'shortlist'); const url = new URL(location.href); url.searchParams.set('pool', 'selected'); history.replaceState(history.state, '', url); } else setPage('meeting'); }}/>}
      {manager && page === 'meetings' && <EmployerAgenda people={meetings} busy={busy} fallbackVacancy={realEmployer ? undefined : state.vacancy.role} onOpen={person => { void openPerson(person, 'meeting'); }} onChooseCandidate={() => { void navigate(panel); }}/>}
      {page === 'meeting' && !stageWorkspace && person?.meeting && <main className="cj-main">{manager && <Button className="cj-interview-back" variant="ghost" onClick={() => setPage('meetings')}><ArrowLeft size={14}/>Все встречи</Button>}<MeetingPanel key={person.meeting.id} journey={journey} person={person} onCallChange={setCallActive} onReschedule={() => setPage(manager ? 'book' : 'schedule')} onComplete={manager ? () => setPage('review') : undefined}/>{!manager && person.meeting.status === 'completed' && <CandidateAfter journey={journey} person={person}/>}</main>}
      {manager && page === 'review' && !stageWorkspace && person && <Review journey={journey} person={person} go={next => setPage(next === 'shortlist' ? panel : next)}/>}
      {manager && page === 'pool' && <main className="cj-main"><h1>Пул для оффера</h1><p>Предложение отправляется одному человеку. Если он откажется, можно перейти к следующему.</p>{pool.map(p => <button key={p.id} className="cj-list-row" onClick={() => { void openPerson(p,'offer'); }}><span className="cj-avatar">{initials(p.name)}</span><div><strong>{p.name}</strong><small>{status(p)}{p.afterInterest === 'no' ? ' · кандидат не хочет продолжать' : ''}{realEmployer && p.vacancyRole ? ` · ${p.vacancyRole}` : ''}</small></div><ChevronRight size={17}/></button>)}</main>}
      {page === 'offer' && !stageWorkspace && person && <OfferPanel key={person.id} person={person} journey={journey} onChat={() => setPage('chat')}/>}
      {page === 'chat' && person && <Chat key={person.id} person={person} journey={journey}/>}
      {!manager && page === 'vacancies' && person && <Vacancies journey={journey} person={person} go={setPage}/>}
      {!manager && page === 'schedule' && person && <Schedule journey={journey} person={person}/>}
      {manager && !realEmployer && <footer className="cj-test-footer"><details><summary>Инструменты команды</summary><div className="cj-actions"><Button variant="outline" onClick={copyEntry}>Скопировать общий вход</Button><Button variant="ghost" onClick={() => setResetOpen(true)}><RotateCcw size={14}/>Начать заново</Button></div><LabMediaControl/></details></footer>}
    </>}
    <Dialog open={resetOpen} onOpenChange={setResetOpen}><DialogContent><DialogHeader><DialogTitle>Начать прохождение заново?</DialogTitle><DialogDescription>Вакансия и аккаунты {state?.vacancy.company} останутся. Ответы, встречи, чат и офферы этого подбора будут сброшены.</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" onClick={() => setResetOpen(false)}>Отмена</Button><Button disabled={busy} onClick={async () => { if (await act('reset')) { setPage('tasks'); setResetOpen(false); setToast('Можно пройти путь заново'); } }}>Сбросить тест</Button></DialogFooter></DialogContent></Dialog>
    {realEmployer && state && <VacancyCreate key={state.workspaceId || state.actorName} active={page === 'create-vacancy'} token={journey.token} draftKey={state.workspaceId || state.actorName} onBack={() => { finishSiteEntry(); setError(''); select('', 'tasks'); }} busy={busy} error={error} initialDescription={siteEntry?.description} company={state.workspaceName || state.vacancy.company} onCreate={async values => {
      if (!await journey.createVacancy(values)) return false;
      finishSiteEntry(); select('', 'tasks'); setIntakeSelected(''); setPanelMode('proposed'); setToast('Вакансия создана'); return true;
    }}/>}
    {toast && <div className="cj-toast" role="status"><Check size={17}/>{toast}</div>}
  </EmployerShell></div></InterviewMediaProvider>;
}

function sourceFor(person: Person): SourceProfile {
  return person.sourceProfile || { id: person.candidateId || person.id, name: person.name, initials: initials(person.name), role: person.summary || '', interviewRole: person.role || '', interviewDate: '', score: null, duration: null, photo: person.photo || null, video: null, shortChapters: [], fullChapters: [], shortSummary: '', profile: [], cv: '', conclusion: [], questions: [] };
}

function Shortlist({ journey, person, people, select, go, intake, intakeSelected, onIntakeSelect }: { journey: Journey; person?: Person; people: Person[]; select: (id: CandidateId, page: Page) => void; go: (page: Page) => void; intake: ReturnType<typeof useIntake>; intakeSelected: string; onIntakeSelect: (id: string) => void }) {
  const profile = journey.employer ? undefined : candidates.find(c => c.id === person?.id);
  const source = journey.employer && person ? sourceFor(person) : undefined;
  const pendingIntake = intake.people.filter(p => !journey.employer || !personList(journey).some(candidate => candidate.id === p.id || candidate.candidateId === p.id));
  const added = pendingIntake.find(p => p.id === intakeSelected) || (!person ? pendingIntake.find(p => !p.archived) : undefined);
  const meetingActive = person?.meeting && ['pending','confirmed','live'].includes(person.meeting.status);
  const rows = [...people.map(p => ({ id: p.id, name: p.name, meta: journey.employer ? p.summary || p.role || '' : candidates.find(c => c.id === p.id)?.experience || '', status: status(p) })), ...pendingIntake.map(p => ({ id: p.id, name: p.name, meta: p.role || 'Новый кандидат', status: p.archived ? 'В архиве' : p.preparedProfile ? 'Из вашей подборки' : 'Добавлен вручную', added: true, archived: p.archived }))];
  return <PeoplePanel title={journey.state!.vacancy.role} rows={rows} selected={added?.id || person?.id || ''} onAdd={() => go('intake')} onSelect={id => { if (pendingIntake.some(p => p.id === id)) onIntakeSelect(id); else { onIntakeSelect(''); select(id, 'shortlist'); } }}>
    {added ? <IntakeDetail key={added.id} person={added} token={journey.token} onArchived={() => { void intake.refresh(); }}/>: person && (profile || source) ? <>
      <header className="ew-person-heading"><Avatar name={person.name} photo={person.photo}/><div className="ew-person-name"><h2>{person.name}</h2><p>{source ? source.interviewRole || person.role : profile!.role}</p><small>{source ? source.role : `${profile!.city} · ${profile!.experience} · ${profile!.salary}`}</small></div><span className="ew-status">{status(person)}</span><Button variant="ghost" size="icon" onClick={() => go('chat')} aria-label={`Написать ${person.name}`}><MessageCircle size={18}/></Button></header>
      <Tabs defaultValue="interview" key={person.id} className="ew-profile-tabs">
        <div className="ew-profile-toolbar"><TabsList aria-label="Материалы кандидата"><TabsTrigger value="interview">Интервью</TabsTrigger><TabsTrigger value="resume">Резюме</TabsTrigger></TabsList>
          <div className="ew-person-actions">{meetingActive ? <Button onClick={() => go('meeting')}>Открыть встречу</Button> : person.meeting?.status === 'completed' ? <Button onClick={() => go('review')}>{person.feedback ? 'Разбор встречи' : 'Оставить фидбек'}</Button> : <Button disabled={journey.busy || !!journey.state!.closedBy} onClick={() => go('book')}>Назначить встречу</Button>}</div>
        </div>
        <TabsContent value="interview">{source ? <EmployerScreening journey={journey} person={person} source={source}/> : <InterviewReview person={profile!} appearance="panel"/>}</TabsContent>
        <TabsContent value="resume"><section className="ew-detail-card">{source ? <>{source.profile.map((fact, index) => <p key={index}><strong>{fact.label}</strong><br/>{fact.value}</p>)}<p className="ew-prewrap">{source.cv || 'Резюме не добавлено.'}</p></> : <><h3>Опыт</h3>{profile!.history.map(h => <p key={h.company}><strong>{h.company}</strong> · {h.role}<br/><small>{h.years}</small></p>)}<h3>Условия</h3><p>{profile!.salary}</p><p>Готовность к выходу: {profile!.availability}</p><p>{profile!.motivation}</p><h3>Что уточнить</h3><p>{profile!.unknown}</p></>}</section></TabsContent>
      </Tabs>
    </> : undefined}{intake.error && <p className="ew-inline-error" role="alert">{intake.error}</p>}
  </PeoplePanel>;
}

function Vacancies({ journey, person, go }: { journey: Journey; person: Person; go: (p: Page) => void }) {
  const v = journey.state!.vacancy;
  const [auto, setAuto] = useState(person.auto);
  useEffect(() => setAuto(person.auto), [person.auto]);
  return <main className="cj-main"><div className="cj-section-heading"><div><span className="cj-kicker">Под ваш опыт и ожидания</span><h1>Подходящие вакансии</h1></div><span className="cj-badge">1 предложение</span></div><div className="cj-vacancy"><span className="cj-kicker">{v.company}</span><h2>{v.role}</h2><strong>{v.salary}</strong><p>{v.format}</p><hr/><h3>Задача, которую предстоит решить</h3><p>{v.problem}. {v.expectations}</p><h3>Почему это может подойти</h3><p>{person.summary || candidates.find(c => c.id === person.id)?.summary}</p><div className="cj-actions">{person.interest === 'accepted' ? <><span className="cj-positive"><Check size={16}/>Вы готовы знакомиться</span><Button onClick={() => go(person.meeting ? 'meeting' : 'schedule')}>{person.meeting ? 'Открыть встречу' : 'Проверить своё расписание'}</Button></> : <><Button disabled={journey.busy || !!journey.state!.closedBy} onClick={() => journey.act('interest',person.id,{value:'accepted'})}>Мне интересно<ArrowRight size={16}/></Button><Button variant="ghost" disabled={journey.busy || person.interest === 'declined'} onClick={() => journey.act('interest',person.id,{value:'declined'})}>{person.interest === 'declined' ? 'Вы отказались' : 'Не подходит'}</Button></>}</div>{person.interest === 'accepted' && !person.meeting && <p className="cj-info">Ваш профиль появился у работодателя. Он сможет выбрать время из вашего расписания.</p>}</div><details className="cj-auto"><summary>Автоматически принимать подходящие вакансии</summary><p>Ваш профиль сразу показывается компаниям из подходящих предложений. Первую встречу вы всё равно подтверждаете вручную.</p><label className="cj-check"><input type="checkbox" checked={auto} disabled={journey.busy || !!journey.state!.closedBy} onChange={async e => { const enabled = e.target.checked; setAuto(enabled); if (!await journey.act('auto', person.id, {enabled})) setAuto(!enabled); }}/>Принимать подходящие вакансии автоматически</label></details>{person.meeting?.status === 'completed' && <CandidateAfter journey={journey} person={person}/>}</main>;
}

function Schedule({ journey, person }: { journey: Journey; person: Person }) {
  const [date, setDate] = useState(''); const [time, setTime] = useState('');
  const days = person.slots || [];
  return <main className="cj-main"><h1>Когда удобно встретиться</h1><p>Добавьте интервалы по 30 минут. Работодатель увидит только свободные слоты. Время указано по Москве.</p><form className="cj-schedule-add" onSubmit={async e => { e.preventDefault(); if (await journey.act('slot.add',person.id,{start:`${date}T${time}:00+03:00`})) { setTime(''); } }}><label>Дата<Input type="date" required value={date} onChange={e => setDate(e.target.value)}/></label><label>Время · МСК<Input type="time" required value={time} onChange={e => setTime(e.target.value)}/></label><Button disabled={journey.busy} type="submit">Добавить время</Button></form><div className="cj-availability">{days.map(s => { const busy = (person.busy || []).includes(s) || Boolean(person.meeting && ['pending','confirmed','live'].includes(person.meeting.status) && person.meeting.start === s); return <div className="cj-list-row" key={s}><CalendarDays size={17}/><div><strong>{dateTime(s)}</strong><small>{person.freeSlots.includes(s) ? 'Доступно для приглашения' : new Date(s).getTime() < Date.now() ? 'Время прошло' : busy ? 'Занято' : 'Работодатель занят в это время'}</small></div><Button variant="ghost" disabled={journey.busy || busy} onClick={() => journey.act('slot.remove',person.id,{start:s})} aria-label={`Удалить слот ${dateTime(s)}`}><X size={15}/></Button></div>; })}</div></main>;
}

function CandidateAfter({ journey, person }: { journey: Journey; person: Person }) {
  if (person.decision === 'declined') return <section className="cj-after"><h2>Компания не продолжает подбор</h2><p>По этой вакансии работодатель решил продолжить с другим кандидатом.</p><Materials journey={journey} person={person}/></section>;
  return <section className="cj-after"><h2>Хотите продолжить с этой компанией?</h2><p>Ваш ответ увидит работодатель. Это ещё не принятие оффера.</p><div className="cj-actions"><Button variant={person.afterInterest === 'yes' ? 'default':'outline'} aria-pressed={person.afterInterest === 'yes'} disabled={journey.busy || !!person.offer} onClick={() => journey.act('after',person.id,{value:'yes'})}>{person.afterInterest === 'yes' && <Check size={14}/>}Хочу продолжить</Button><Button variant={person.afterInterest === 'no' ? 'secondary':'ghost'} aria-pressed={person.afterInterest === 'no'} disabled={journey.busy || !!person.offer} onClick={() => journey.act('after',person.id,{value:'no'})}>Не подходит</Button></div><Materials journey={journey} person={person}/></section>;
}

const feedbackFields = [ ['example','Какой пример показал, что человек справится с задачей?'], ['doubts','Какие сомнения остались после встречи?'], ['check','Что ещё нужно проверить перед решением?'] ] as const;
function Review({ journey, person, go }: { journey: Journey; person: Person; go: (p: Page) => void }) {
  if (person.meeting?.status !== 'completed') return <main className="cj-main"><h1>Сначала проведите встречу</h1><Button onClick={() => go('meeting')}>К встрече</Button></main>;
  return <main className={`cj-main ${person.feedback ? 'cj-review-page' : ''}`}>
    <span className="cj-kicker">{person.name}</span>
    <h1>{person.feedback ? 'Разбор встречи' : 'Как прошла встреча?'}</h1>
    {!person.feedback ? <FeedbackGate key={person.meeting.id} journey={journey} person={person} remaining={1} onSaved={() => go('review')}/> : <>
      <div className="cj-review-status"><span>{person.afterInterest === 'yes' ? 'Кандидат хочет продолжить' : person.afterInterest === 'no' ? 'Кандидат не хочет продолжать' : 'Ждём ответ кандидата после встречи'}</span><span className="cj-badge"><Check size={12}/>Фидбек сохранён</span></div>
      <div className="cj-review-layout">
        <Materials journey={journey} person={person}/>
        <aside className="cj-review-decision">
          <Tabs defaultValue="feedback"><TabsList aria-label="Разбор кандидата"><TabsTrigger value="feedback">Ваш фидбек</TabsTrigger><TabsTrigger value="assessment">Оценка интервью</TabsTrigger></TabsList>
            <TabsContent value="feedback">{person.feedback.questions?.map(q => <article className="cj-review-observation" key={q.id}><h3>{q.question}</h3><p>{answerLabels[person.feedback!.answers![q.id].rating]}</p>{person.feedback!.answers![q.id].comment && <p>{person.feedback!.answers![q.id].comment}</p>}</article>) || feedbackFields.map(([key,label]) => <article className="cj-review-observation" key={key}><h3>{label}</h3><p>{person.feedback![key]}</p></article>)}</TabsContent>
            <TabsContent value="assessment"><section className="cj-assessment"><span className="cj-kicker">Заглушка · анализ не подключён</span><h2>Насколько опыт подходит задаче</h2><p>Оценка будет опираться на запись этой встречи. Пока автоматических выводов нет.</p><h3>Что подтверждено</h3><p>Здесь будут выводы со ссылками на моменты интервью.</p><h3>Что осталось проверить</h3><p>Здесь будут пробелы и вопросы без ответа.</p></section></TabsContent>
          </Tabs>
          <div className="cj-review-actions">
            <Button disabled={journey.busy || !!journey.state!.closedBy || person.decision === 'pool'} onClick={async () => {if(await journey.act('decision',person.id,{value:'pool'})) go('pool');}}>{person.decision === 'pool' ? 'В пуле для оффера' : 'Добавить в пул для оффера'}</Button>
            {person.decision === 'pool' && <Button variant="outline" onClick={() => go('offer')}>Подготовить оффер</Button>}
            <Button variant="ghost" disabled={journey.busy || !!journey.state!.closedBy || !!person.offer} onClick={() => journey.act('decision',person.id,{value:'declined'})}>Не продолжаем</Button>
          </div>
        </aside>
      </div>
      <Button className="cj-interview-back" variant="ghost" onClick={() => go('shortlist')}><ArrowLeft size={14}/>Первичное интервью кандидата</Button>
    </>}
  </main>;
}

function OfferPanel({ journey, person, onChat }: { journey: Journey; person: Person; onChat: () => void }) {
  const v = journey.state!.vacancy;
  const manager = journey.state!.actor === 'manager';
  const offer = person.offer;
  const [editing, setEditing] = useState(!offer);
  const [draft, setDraft] = useState({role:offer?.role || v.role,compensation:offer?.compensation || v.salary,format:offer?.format || v.format,startDate:offer?.startDate || '',expectations:offer?.expectations || v.expectations});
  const fieldLabels: Record<keyof typeof draft,string> = {role:'Роль',compensation:'Доход и бонус',format:'Формат работы',startDate:'Предполагаемая дата выхода',expectations:'Задачи и ожидаемый результат'};
  const active = personList(journey).find(p => p.id !== person.id && p.offer?.status === 'sent');
  return <main className="cj-main"><span className="cj-kicker">{manager ? person.name : v.company}</span><h1>{manager ? 'Предложение о работе' : 'Ваш оффер'}</h1>{manager && editing && !journey.state!.closedBy ? <form className="cj-offer-form" onSubmit={async e => {e.preventDefault(); if(await journey.act('offer.save',person.id,draft)) setEditing(false);}}>{Object.entries(fieldLabels).map(([key,label]) => <label key={key} className="cj-field">{label}{key === 'expectations' ? <Textarea required value={draft.expectations} onChange={e => setDraft({...draft,expectations:e.target.value})}/> : <Input type={key === 'startDate' ? 'date':'text'} required value={draft[key as keyof typeof draft]} onChange={e => setDraft({...draft,[key]:e.target.value})}/>}</label>)}<Button disabled={journey.busy} type="submit">Сохранить условия</Button></form> : offer ? <><div className="cj-offer"><div className="cj-section-heading"><span className="cj-kicker">{v.company} приглашает в команду</span><span className="cj-badge">{offerLabels[offer.status]}</span></div><h2>{offer.role}</h2><strong className="cj-salary">{offer.compensation}</strong><dl><dt>Формат</dt><dd>{offer.format}</dd><dt>Начало работы</dt><dd>{offer.startDate}</dd><dt>Ваш результат</dt><dd>{offer.expectations}</dd></dl></div><div className="cj-actions">{manager ? <>{offer.status === 'draft' && <Button disabled={journey.busy || !!active || !!journey.state!.closedBy || person.afterInterest === 'no'} onClick={() => journey.act('offer.send',person.id)}>Отправить оффер<ArrowRight size={16}/></Button>}{['draft','sent'].includes(offer.status) && !journey.state!.closedBy && <Button variant="outline" onClick={() => {setDraft({role:offer.role,compensation:offer.compensation,format:offer.format,startDate:offer.startDate,expectations:offer.expectations});setEditing(true);}}>Изменить условия</Button>}</> : offer.status === 'sent' && <><Button disabled={journey.busy} onClick={() => journey.act('offer.reply',person.id,{value:'accepted',version:offer.version})}>Принять оффер<Check size={16}/></Button><Button variant="outline" disabled={journey.busy} onClick={onChat}>Обсудить условия</Button><Button variant="ghost" disabled={journey.busy} onClick={() => journey.act('offer.reply',person.id,{value:'declined',version:offer.version})}>Отклонить</Button></>}<Button variant="ghost" onClick={onChat}>Открыть чат</Button></div>{active && <p className="cj-info">Сейчас ждём ответ от {active.name}. Следующий оффер станет доступен после отказа.</p>}{person.afterInterest === 'no' && <p className="cj-info">Кандидат не хочет продолжать. Обсудите это в чате или выберите другого.</p>}</> : <p>Предложение появится здесь, когда работодатель отправит его.</p>}</main>;
}

function Chat({ journey, person }: { journey: Journey; person: Person }) {
  const [text, setText] = useState('');
  return <main className="cj-main cj-chat"><span className="cj-kicker">По вакансии «{journey.state!.vacancy.role}»</span><h1>{journey.state!.actor === 'manager' ? person.name : `Иван Петров · ${journey.state!.vacancy.company}`}</h1><div className="cj-messages" role="log" aria-label="Сообщения">{!person.messages.length && <p className="cj-chat-empty">Здесь можно уточнить задачу, встречу или условия оффера.</p>}{person.messages.map(m => m.system ? <div className="cj-system-message" key={m.id}>{m.text}</div> : <div key={m.id} className={`cj-message ${m.actor === journey.state!.actor ? 'cj-own' : ''}`}><small>{actorName(journey.state!, m.actor)}</small><p>{m.text}</p><time>{new Intl.DateTimeFormat('ru-RU',{hour:'2-digit',minute:'2-digit',timeZone:'Europe/Moscow'}).format(new Date(m.at))}</time></div>)}</div><form onSubmit={async e => {e.preventDefault(); if(await journey.act('chat',person.id,{text}))setText('');}}><Textarea aria-label="Сообщение" placeholder="Написать сообщение…" value={text} maxLength={2000} onChange={e => setText(e.target.value)}/><Button type="submit" disabled={!text.trim() || journey.busy}>Отправить<ArrowRight size={16}/></Button></form></main>;
}
