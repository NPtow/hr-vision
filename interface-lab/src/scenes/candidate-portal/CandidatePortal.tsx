import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Briefcase, CalendarDays, Check, CheckCircle2, Clock3, Home, LogOut, Mail, Menu, RefreshCw, Send, UserRound, Video, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import { CandidateApiError, candidateRequest, dateLabel, dateTimeLabel, localDate, timeLabel, timeZone, type CandidateApplication, type CandidateSession, type CandidateState, type Channels, type RecruiterSlot } from './api';
import { PreferencesForm } from './PreferencesForm';
import { ResumeUpload } from './ResumeUpload';
import { AvailabilityForm } from './AvailabilityForm';
import { InterviewRoom } from './InterviewRoom';
import { InvitationPortal } from './InvitationPortal';
import '../agency-product/theme.css';
import './candidate-portal.css';

type Page = 'welcome' | 'login' | 'register' | 'resume' | 'preferences' | 'home' | 'calendar' | 'profile' | 'vacancies' | 'interview' | 'meeting';
const pages: Page[] = ['welcome', 'login', 'register', 'resume', 'preferences', 'home', 'calendar', 'profile', 'vacancies', 'interview', 'meeting'];
const readPage = (): Page => { const value = new URLSearchParams(location.search).get('cp') as Page; return pages.includes(value) ? value : 'welcome'; };
const authenticatedPage = (page: Page) => !['welcome', 'login', 'register'].includes(page);
function setupPage(candidate: CandidateState, newBooking = false): 'resume' | 'preferences' | null {
  if (candidate.interview && !newBooking) return null;
  if (!candidate.resume) return 'resume';
  if (!candidate.resumeReviewed || candidate.onboarding.missingFields.length) return 'preferences';
  return null;
}
function destination(candidate: CandidateState, wanted: Page = 'home'): Page {
  const pending = setupPage(candidate, wanted === 'calendar' && candidate.interview?.status === 'completed');
  if (pending) return pending;
  if (['resume', 'preferences'].includes(wanted) && candidate.resume && candidate.resumeReviewed && !candidate.onboarding.missingFields.length) return candidate.interview ? 'home' : 'calendar';
  return ['welcome', 'login', 'register'].includes(wanted) ? 'home' : wanted;
}
const initials = (name: string) => name.split(' ').filter(Boolean).slice(0, 2).map(part => part[0]).join('').toUpperCase();
const safeSupportEmail = (channels?: Channels) => channels?.supportEmail && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(channels.supportEmail) ? channels.supportEmail : null;

export function CandidatePortal() {
  const invitation = new URLSearchParams(location.search).get('cp');
  return invitation === 'guest' || invitation === 'claim' ? <InvitationPortal kind={invitation}/> : <AuthenticatedCandidatePortal/>;
}

function AuthenticatedCandidatePortal() {
  const [page, setPage] = useState<Page>(readPage);
  const [candidate, setCandidate] = useState<CandidateState | null>(null);
  const [channels, setChannels] = useState<Channels>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [notice, setNotice] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const [credentials, setCredentials] = useState({ email: '', password: '' });
  const [registrationConsent, setRegistrationConsent] = useState(false);
  const [meetingApplicationId, setMeetingApplicationId] = useState(() => new URLSearchParams(location.search).get('application') || '');
  const verifying = useRef(false);
  const alive = useRef(true);
  const authGeneration = useRef(0);
  const refreshSequence = useRef(0);
  const mutating = useRef(false);
  const go = useCallback((next: Page, replace = false, applicationId?: string) => {
    const url = new URL(location.href); url.searchParams.set('cp', next);
    if (applicationId) url.searchParams.set('application', applicationId); else url.searchParams.delete('application');
    history[replace ? 'replaceState' : 'pushState']({}, '', url);
    setPage(next); setMeetingApplicationId(applicationId || ''); setError(''); setLoadError(''); setNotice(''); setMenuOpen(false);
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, []);
  const apply = useCallback((next: CandidateState) => {
    if (!alive.current) return;
    setCandidate(next);
    if (next.channels) setChannels(next.channels);
  }, []);
  const refresh = useCallback(async () => {
    if (mutating.current) return;
    const generation = authGeneration.current;
    const sequence = ++refreshSequence.current;
    try { const next = await candidateRequest<CandidateState>('state'); if (alive.current && generation === authGeneration.current && sequence === refreshSequence.current) { apply(next); setLoadError(''); } }
    catch (cause) {
      if (!alive.current || generation !== authGeneration.current || sequence !== refreshSequence.current) return;
      if (cause instanceof CandidateApiError && cause.status === 401) { setCandidate(null); go('login', true); setError('Сессия завершилась. Войдите снова.'); }
      else setLoadError((cause as Error).message);
    }
  }, [apply, go]);
  const session = useCallback(async () => {
    const generation = ++authGeneration.current;
    setLoading(true); setError(''); setLoadError('');
    try {
      const result = await candidateRequest<CandidateSession>('session');
      if (!alive.current || generation !== authGeneration.current) return;
      setChannels(result.channels || {});
      if (result.authenticated && result.candidate) {
        apply(result.candidate);
        const nextPage = destination(result.candidate, readPage());
        if (nextPage !== readPage()) go(nextPage, true);
      } else { setCandidate(null); if (authenticatedPage(readPage())) go('login', true); }
    } catch (cause) { if (alive.current && generation === authGeneration.current) setLoadError((cause as Error).message); }
    finally { if (alive.current && generation === authGeneration.current) setLoading(false); }
  }, [apply, go]);
  useEffect(() => { alive.current = true; void session(); return () => { alive.current = false; }; }, [session]);
  useEffect(() => {
    const onPop = () => { const next = readPage(); setPage(next); setMeetingApplicationId(new URLSearchParams(location.search).get('application') || ''); setMenuOpen(false); setError(''); setLoadError(''); };
    window.addEventListener('popstate', onPop); return () => window.removeEventListener('popstate', onPop);
  }, []);
  useEffect(() => {
    if (!candidate) return;
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void refresh(); }, 12000);
    const focused = () => { if (document.visibilityState === 'visible') void refresh(); };
    window.addEventListener('focus', focused); document.addEventListener('visibilitychange', focused);
    return () => { clearInterval(timer); window.removeEventListener('focus', focused); document.removeEventListener('visibilitychange', focused); };
  }, [candidate?.id, refresh]);
  useEffect(() => {
    if (!candidate || verifying.current) return;
    const token = new URLSearchParams(location.search).get('verify');
    if (!token) return;
    verifying.current = true;
    const generation = authGeneration.current;
    void candidateRequest('email/verify', { token }).then(async () => {
      if (!alive.current || generation !== authGeneration.current) return;
      const url = new URL(location.href); url.searchParams.delete('verify'); history.replaceState({}, '', url);
      await refresh(); if (alive.current && generation === authGeneration.current) setNotice('Почта подтверждена.');
    }).catch(cause => { if (alive.current && generation === authGeneration.current) setError((cause as Error).message); }).finally(() => { verifying.current = false; });
  }, [candidate?.id, refresh]);
  useEffect(() => {
    if (!candidate || loading) return;
    const nextPage = destination(candidate, page);
    if (nextPage !== page) go(nextPage, true);
  }, [candidate, page, loading, go]);
  async function mutate(path: string, body: unknown, success?: string): Promise<CandidateState | null> {
    if (mutating.current) return null;
    mutating.current = true;
    const generation = ++authGeneration.current;
    setBusy(true); setError(''); setNotice('');
    try { const next = await candidateRequest<CandidateState>(path, body); if (!alive.current || generation !== authGeneration.current) return null; apply(next); if (success) setNotice(success); return next; }
    catch (cause) { if (alive.current && generation === authGeneration.current) setError((cause as Error).message); return null; }
    finally { if (generation === authGeneration.current) authGeneration.current++; mutating.current = false; if (alive.current) setBusy(false); }
  }
  async function logout() {
    if (mutating.current) return;
    mutating.current = true;
    const generation = ++authGeneration.current;
    setBusy(true); setError('');
    try { await candidateRequest('logout', {}); if (!alive.current || generation !== authGeneration.current) return; setCandidate(null); setCredentials({ email: '', password: '' }); setRegistrationConsent(false); go('login', true); }
    catch (cause) { if (alive.current && generation === authGeneration.current) setError((cause as Error).message); }
    finally { if (generation === authGeneration.current) authGeneration.current++; mutating.current = false; if (alive.current) setBusy(false); }
  }
  const showNavigation = candidate && !setupPage(candidate);
  const nav = showNavigation ? [['home', 'Кабинет', Home], ['vacancies', 'Вакансии', Briefcase], ['profile', 'Профиль', UserRound]] as const : [];
  const activePage = ['calendar', 'interview'].includes(page) ? 'home' : page === 'meeting' ? 'vacancies' : page;
  const pendingCount = candidate?.applications.filter(application => application.status === 'pending' || application.meeting?.status === 'pending').length || 0;
  const currentMeeting = candidate?.applications.find(application => application.id === meetingApplicationId);
  return <div className="agency-product-theme cp-root">
    <header className="cp-topbar">
      {showNavigation && <button className="cp-menu-toggle" aria-label={menuOpen ? 'Закрыть меню' : 'Открыть меню'} aria-expanded={menuOpen} aria-controls="candidate-navigation" onClick={() => setMenuOpen(value => !value)}>{menuOpen ? <X size={21}/> : <Menu size={21}/>}</button>}
      <button className="cp-logo" aria-label="HR Vision · главная" onClick={() => go(candidate ? destination(candidate) : 'welcome')}><img src={logo} alt="HR Vision"/></button>
      {candidate ? <div className="cp-account">{showNavigation && <button className="cp-avatar" aria-label="Открыть профиль" onClick={() => go('profile')}>{initials(candidate.name) || <UserRound size={18}/>}</button>}<Button variant="ghost" size="icon" aria-label="Выйти из кабинета" title="Выйти" disabled={busy} onClick={logout}><LogOut size={18}/></Button></div> : <Button className="cp-login-link" variant="ghost" disabled={loading} onClick={() => go(page === 'login' ? 'register' : 'login')}>{page === 'login' ? 'Создать кабинет' : 'Войти'}</Button>}
    </header>
    <div className={`cp-body ${showNavigation ? 'cp-authenticated' : ''}`}>
      {showNavigation && <aside id="candidate-navigation" className="cp-sidebar" data-open={menuOpen}><nav aria-label="Кабинет кандидата">{nav.map(([key, label, Icon]) => <button key={key} aria-current={activePage === key ? 'page' : undefined} onClick={() => go(key)}><Icon size={19}/>{label}{key === 'vacancies' && pendingCount > 0 && <span className="cp-count">{pendingCount}</span>}</button>)}</nav>{safeSupportEmail(channels) && <a className="cp-support" href={`mailto:${safeSupportEmail(channels)}`}><Mail size={17}/>Поддержка</a>}</aside>}
      <main className="cp-main" id="candidate-main" aria-busy={loading}>
        {loading ? <div className="cp-loading" role="status"><span className="cp-spinner"/>Открываем кабинет…</div> : <>
          {(error || loadError) && <div className="cp-message cp-error" role="alert"><span>{error || loadError}</span><Button size="sm" variant="ghost" onClick={() => { setError(''); void (candidate ? refresh() : session()); }}>Повторить</Button></div>}
          {notice && <div className="cp-message cp-success" role="status"><Check size={17}/>{notice}<button aria-label="Закрыть сообщение" onClick={() => setNotice('')}><X size={16}/></button></div>}
          {!candidate && page === 'welcome' && <Welcome onStart={() => go('register')}/>}
          {!candidate && (page === 'login' || authenticatedPage(page)) && <section className="cp-auth-card"><h1>С возвращением</h1><p className="cp-muted">Войдите в свой кабинет HR Vision.</p><form className="cp-auth-form" onSubmit={async event => { event.preventDefault(); const next = await mutate('login', { email: credentials.email.trim(), password: credentials.password }); if (next) { setCredentials(value => ({ ...value, password: '' })); go(destination(next), true); } }}>
            <label htmlFor="cp-login-email">Почта<Input id="cp-login-email" type="email" autoComplete="username" required value={credentials.email} onChange={event => setCredentials(value => ({ ...value, email: event.target.value }))}/></label>
            <label htmlFor="cp-login-password">Пароль<Input id="cp-login-password" type="password" autoComplete="current-password" required value={credentials.password} onChange={event => setCredentials(value => ({ ...value, password: event.target.value }))}/></label>
            <Button disabled={busy}>{busy ? 'Входим…' : 'Войти'}</Button>
          </form><p className="cp-auth-foot">Первый раз здесь? <button onClick={() => go('register')}>Создать кабинет</button></p></section>}
          {!candidate && page === 'register' && <section className="cp-auth-card"><Button className="cp-back" variant="ghost" onClick={() => go('welcome')}><ArrowLeft size={16}/>Назад</Button><h1>Создайте кабинет</h1><p className="cp-muted">Затем загрузите резюме и выберите время интервью.</p><form className="cp-auth-form" onSubmit={async event => { event.preventDefault(); const next = await mutate('register', { email: credentials.email.trim(), password: credentials.password, consentReuse: registrationConsent, autoApprove: false }); if (next) { setCredentials(value => ({ ...value, password: '' })); go(destination(next), true); } }}>
            <label htmlFor="cp-register-email">Почта<Input id="cp-register-email" type="email" autoComplete="username" required value={credentials.email} onChange={event => setCredentials(value => ({ ...value, email: event.target.value }))}/></label>
            <label htmlFor="cp-register-password">Пароль<Input id="cp-register-password" type="password" autoComplete="new-password" minLength={10} maxLength={128} required placeholder="Не менее 10 символов" value={credentials.password} onChange={event => setCredentials(value => ({ ...value, password: event.target.value }))}/></label>
            <label className="cp-check cp-registration-consent"><input type="checkbox" required checked={registrationConsent} onChange={event => setRegistrationConsent(event.target.checked)}/><span>Согласен на запись интервью и использование его материалов для участия в подходящих вакансиях.</span></label>
            <Button disabled={busy}>{busy ? 'Создаём кабинет…' : 'Создать кабинет'}<ArrowRight size={17}/></Button>
          </form><p className="cp-auth-foot">Уже есть кабинет? <button onClick={() => go('login')}>Войти</button></p></section>}
          {candidate && page === 'resume' && <section className="cp-form-page"><div className="cp-page-heading"><h1>Загрузите резюме</h1><p>Заполним профиль из файла. Вы проверите данные и дополните недостающие.</p></div><ResumeUpload resume={candidate.resume} busy={busy} onUpload={async payload => { const next = await mutate('resume', payload); if (!next) return false; go(setupPage(next, true) || 'calendar', true); return true; }}/></section>}
          {candidate && page === 'preferences' && <section className="cp-form-page"><div className="cp-page-heading"><h1>Проверьте данные</h1><p>Заполнили из резюме. Можно исправить и дополнить.</p></div><PreferencesForm key={candidate.id} initial={candidate} missingFields={candidate.onboarding.missingFields} busy={busy} onSubmit={async profile => { const next = await mutate('profile', profile); if (next) go(setupPage(next, true) || 'calendar', true); }}/></section>}
          {candidate && ['welcome', 'login', 'register', 'home'].includes(page) && <CandidateHome candidate={candidate} onNavigate={go} onRefresh={refresh} channels={channels}/>}
          {candidate && page === 'calendar' && <RecruiterCalendar candidate={candidate} busy={busy} onBack={() => go('home')} onBook={async slotId => { if (await mutate('book', { slotId })) go('home'); }}/>}
          {candidate && page === 'profile' && <section className="cp-form-page"><div className="cp-page-heading"><h1>Мой профиль</h1></div><ResumeUpload compact resume={candidate.resume} busy={busy} onUpload={async payload => !!await mutate('resume', payload, 'Резюме сохранено.')}/><PreferencesForm key={candidate.id} initial={candidate} busy={busy} onSubmit={async profile => { await mutate('profile', profile, 'Изменения сохранены.'); }}/><details className="cp-availability-section"><summary>Удобное время для встреч</summary><AvailabilityForm key={candidate.id} initial={candidate.availability} busy={busy} onSubmit={async availability => { await mutate('profile', { availability }, 'Удобное время сохранено.'); }}/></details><ContactSettings candidate={candidate} channels={channels} onRefresh={refresh}/></section>}
          {candidate && page === 'vacancies' && <section className="cp-content-page"><div className="cp-page-heading cp-heading-row"><div><h1>Мои вакансии</h1><p>{candidate.autoApprove ? 'Автосогласование включено. Время встречи всегда подтверждаете вы.' : 'Вы выбираете, в каких вакансиях участвовать.'}</p></div><Button variant="ghost" size="icon" aria-label="Обновить вакансии" onClick={refresh}><RefreshCw size={18}/></Button></div><Applications candidate={candidate} busy={busy} onDecision={async (applicationId, decision) => !!await mutate('application/decision', { applicationId, decision })} onConfirm={async (applicationId, meetingId) => !!await mutate('meeting/confirm', { applicationId, meetingId })} onJoin={id => go('meeting', false, id)} onProfile={() => go('profile')} channels={channels}/></section>}
          {candidate && page === 'interview' && (candidate.interview ? <InterviewRoom key={candidate.interview.id} title="Интервью с рекрутером" start={candidate.interview.start} completed={candidate.interview.status === 'completed'} configured={candidate.videoConfigured} onRefresh={refresh} onBack={() => go('home')}/> : <RecruiterCalendar candidate={candidate} busy={busy} onBack={() => go('home')} onBook={async slotId => { if (await mutate('book', { slotId })) go('home'); }}/>) }
          {candidate && page === 'meeting' && (currentMeeting?.meeting && ['confirmed', 'live', 'completed'].includes(currentMeeting.meeting.status) ? <InterviewRoom key={currentMeeting.meeting.id} title={`Встреча · ${currentMeeting.vacancy.company}`} start={currentMeeting.meeting.start} completed={currentMeeting.meeting.status === 'completed'} configured={candidate.videoConfigured} applicationId={currentMeeting.id} meetingId={currentMeeting.meeting.id} onRefresh={refresh} onBack={() => go('vacancies')}/> : <section className="cp-narrow cp-empty"><h1>Встреча пока не подтверждена</h1><p>Откройте вакансию и подтвердите предложенное время.</p><Button onClick={() => go('vacancies')}>К вакансиям</Button></section>)}
        </>}
      </main>
    </div>
  </div>;
}

function Welcome({ onStart }: { onStart: () => void }) {
  return <section className="cp-welcome"><div className="cp-welcome-copy"><h1>Одно интервью —<br/>больше подходящих<br className="cp-desktop-break"/> вакансий</h1><p>Расскажите о себе рекрутеру один раз. Мы подберём вакансии и с вашего согласия передадим работодателям материалы интервью.</p><Button size="lg" onClick={onStart}>Начать<ArrowRight size={18}/></Button><span className="cp-welcome-duration"><Clock3 size={16}/>Интервью займёт 30 минут</span></div><div className="cp-journey-card"><div className="cp-journey-item"><span><UserRound size={22}/></span><div><h2>Загрузите резюме</h2><p>Заполним профиль из файла. Вы дополните недостающие данные.</p></div></div><div className="cp-journey-item"><span><Video size={22}/></span><div><h2>Познакомьтесь с рекрутером</h2><p>Живой разговор на нашем сайте, с телефона или компьютера.</p></div></div><div className="cp-journey-item"><span><Briefcase size={22}/></span><div><h2>Выбирайте вакансии</h2><p>Предложения и встречи — в одном кабинете. Каждое время подтверждаете вы.</p></div></div></div></section>;
}

function CandidateHome({ candidate, onNavigate, onRefresh, channels }: { candidate: CandidateState; onNavigate: (page: Page) => void; onRefresh: () => Promise<void>; channels: Channels }) {
  const interview = candidate.interview;
  const completed = interview?.status === 'completed';
  const applications = candidate.applications.filter(application => application.status !== 'declined');
  return <section className="cp-content-page"><div className="cp-page-heading"><h1>Мой поиск</h1></div><div className="cp-home-grid"><div className="cp-home-primary">
    {!interview && <article className="cp-feature-card"><span className="cp-large-icon"><Video size={28}/></span><h2>Давайте познакомимся</h2><p>Выберите время для интервью с рекрутером. За 30 минут обсудим ваш опыт и следующую работу.</p><Button onClick={() => onNavigate('calendar')}>Выбрать время<ArrowRight size={17}/></Button></article>}
    {interview && !completed && <article className="cp-feature-card"><div className="cp-status"><span className="cp-status-dot"/>Вы записаны</div><h2>Интервью с рекрутером</h2><p className="cp-booking-date">{dateTimeLabel(interview.start)}</p><p>{interview.recruiterName} · 30 минут · {timeZone()}</p><Button onClick={() => onNavigate('interview')}><Video size={17}/>Подготовиться и войти</Button></article>}
    {completed && <article className="cp-feature-card"><div className="cp-status"><CheckCircle2 size={17}/>Встреча завершена</div><h2>{applications.length ? 'Есть подходящие вакансии' : 'Ищем подходящие вакансии'}</h2><p>{applications.length ? 'Посмотрите предложения. Если вас приглашают на встречу, подтвердите удобное время.' : 'Когда появятся предложения, вы увидите их здесь.'}</p><div className="cp-actions">{applications.length ? <Button onClick={() => onNavigate('vacancies')}>Посмотреть вакансии<ArrowRight size={17}/></Button> : <Button variant="secondary" onClick={onRefresh}><RefreshCw size={16}/>Проверить предложения</Button>}<Button variant="secondary" onClick={() => onNavigate('interview')}><Video size={16}/>Материалы встречи</Button></div></article>}
    {!completed && applications.length > 0 && <button className="cp-simple-row" onClick={() => onNavigate('vacancies')}><span><Briefcase size={20}/>Предложения вакансий</span><span>{applications.length}<ArrowRight size={18}/></span></button>}
    <ContactSettings compact candidate={candidate} channels={channels} onRefresh={onRefresh}/>
  </div><aside className="cp-summary-card"><div className="cp-profile-summary"><span className="cp-avatar cp-avatar-large">{initials(candidate.name) || <UserRound size={24}/>}</span><h2>{candidate.name || 'Ваш профиль'}</h2><p>{candidate.desiredRole}</p></div><dl>{candidate.city && <div><dt>Город</dt><dd>{candidate.city}</dd></div>}{candidate.salaryMin != null && candidate.salaryMax != null && <div><dt>Желаемая зарплата</dt><dd>{candidate.salaryMin.toLocaleString('ru-RU')}–{candidate.salaryMax.toLocaleString('ru-RU')} ₽</dd></div>}{candidate.latestStartDate && <div><dt>Выход на работу до</dt><dd>{dateLabel(`${candidate.latestStartDate}T12:00:00`)}</dd></div>}<div><dt>Вакансии</dt><dd>{candidate.autoApprove ? 'Согласуются автоматически' : 'Согласуете лично'}</dd></div></dl><Button variant="ghost" onClick={() => onNavigate('profile')}>Изменить профиль<ArrowRight size={16}/></Button></aside></div></section>;
}

function RecruiterCalendar({ candidate, busy, onBook, onBack }: { candidate: CandidateState; busy: boolean; onBook: (slotId: string) => Promise<void>; onBack: () => void }) {
  const [slots, setSlots] = useState<RecruiterSlot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [day, setDay] = useState('');
  const [selected, setSelected] = useState('');
  const load = useCallback(async () => {
    setLoading(true); setError('');
    try { const result = await candidateRequest<{ slots: RecruiterSlot[] }>('slots'); const future = result.slots.filter(slot => new Date(slot.start).getTime() > Date.now()).sort((left, right) => left.start.localeCompare(right.start)); setSlots(future); setDay(previous => future.some(slot => localDate(new Date(slot.start)) === previous) ? previous : future[0] ? localDate(new Date(future[0].start)) : ''); setSelected(''); }
    catch (cause) { setError((cause as Error).message); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const days = [...new Set(slots.map(slot => localDate(new Date(slot.start))))];
  const selectedSlot = slots.find(slot => slot.id === selected);
  if (candidate.interview && candidate.interview.status !== 'completed') return <section className="cp-narrow cp-empty"><CalendarDays size={32}/><h1>Вы уже записаны</h1><p>{dateTimeLabel(candidate.interview.start)}</p><Button onClick={onBack}>К интервью</Button></section>;
  return <section className="cp-calendar-page"><Button variant="ghost" className="cp-back" onClick={onBack}><ArrowLeft size={16}/>В кабинет</Button><div className="cp-page-heading"><h1>Выберите время интервью</h1><p>30 минут с рекрутером · {timeZone()}</p></div>{loading ? <div className="cp-loading" role="status"><span className="cp-spinner"/>Загружаем расписание…</div> : error ? <div className="cp-empty"><p role="alert" className="cp-error">{error}</p><Button variant="secondary" onClick={load}>Попробовать ещё раз</Button></div> : !slots.length ? <div className="cp-empty cp-card"><CalendarDays size={32}/><h2>Пока нет свободного времени</h2><p>Рекрутер ещё не добавил новые слоты. Ваш профиль сохранён — можно вернуться в кабинет.</p><div className="cp-actions"><Button onClick={onBack}>В кабинет</Button><Button variant="ghost" onClick={load}>Обновить расписание</Button></div></div> : <div className="cp-calendar-grid"><div className="cp-days" role="group" aria-label="Даты интервью">{days.map(value => <button key={value} aria-pressed={day === value} onClick={() => { setDay(value); setSelected(''); }}><span>{new Intl.DateTimeFormat('ru-RU', { weekday: 'short' }).format(new Date(`${value}T12:00:00`))}</span><strong>{new Date(`${value}T12:00:00`).getDate()}</strong><span>{new Intl.DateTimeFormat('ru-RU', { month: 'short' }).format(new Date(`${value}T12:00:00`))}</span></button>)}</div><div className="cp-slot-panel"><h2>{day ? dateLabel(`${day}T12:00:00`) : 'Выберите дату'}</h2><div className="cp-slots" role="group" aria-label="Доступное время">{slots.filter(slot => localDate(new Date(slot.start)) === day).map(slot => <button key={slot.id} aria-pressed={selected === slot.id} onClick={() => setSelected(slot.id)}><strong>{timeLabel(slot.start)}</strong><span>{slot.recruiterName}</span>{selected === slot.id && <Check size={16}/>}</button>)}</div>{selectedSlot && <div className="cp-book-confirm"><p>{dateTimeLabel(selectedSlot.start)}<br/><span>{selectedSlot.recruiterName} · 30 минут</span></p><Button disabled={busy} onClick={() => onBook(selectedSlot.id)}>{busy ? 'Записываем…' : 'Записаться'}</Button></div>}</div></div>}</section>;
}

function Applications({ candidate, busy, onDecision, onConfirm, onJoin, onProfile, channels }: {
  candidate: CandidateState; busy: boolean; onDecision: (id: string, decision: 'accepted' | 'declined') => Promise<boolean>; onConfirm: (id: string, meetingId: string) => Promise<boolean>; onJoin: (id: string) => void; onProfile: () => void; channels: Channels;
}) {
  const items = candidate.applications.filter(application => application.status !== 'declined');
  if (!items.length) return <div className="cp-empty cp-card"><span className="cp-large-icon"><Briefcase size={27}/></span><h2>{candidate.interview?.status === 'completed' ? 'Предложения появятся здесь' : 'Начнём с интервью'}</h2><p>{candidate.interview?.status === 'completed' ? 'Подбираем вакансии с учётом вашего опыта и предпочтений.' : 'После знакомства с рекрутером сможем предложить подходящие вакансии.'}</p><Button variant="secondary" onClick={onProfile}>Мои предпочтения</Button></div>;
  return <div className="cp-application-list">{items.map(application => <ApplicationCard key={application.id} application={application} busy={busy} onDecision={onDecision} onConfirm={onConfirm} onJoin={onJoin} channels={channels}/>)}</div>;
}

function ApplicationCard({ application, busy, onDecision, onConfirm, onJoin, channels }: {
  application: CandidateApplication; busy: boolean; onDecision: (id: string, decision: 'accepted' | 'declined') => Promise<boolean>; onConfirm: (id: string, meetingId: string) => Promise<boolean>; onJoin: (id: string) => void; channels: Channels;
}) {
  const { vacancy, meeting } = application;
  return <article className="cp-application-card"><div className="cp-application-top"><div><p className="cp-muted">{vacancy.company}</p><h2>{vacancy.title}</h2><p className="cp-muted">{vacancy.city}{vacancy.remote ? ' · Удалённо' : ''}</p></div>{application.status === 'accepted' && <span className="cp-status"><Check size={15}/>{application.approvedAutomatically ? 'Согласовано автоматически' : 'Вы участвуете'}</span>}</div><p className="cp-vacancy-description">{vacancy.description}</p>
    {application.status === 'pending' ? <div className="cp-actions"><Button disabled={busy} onClick={() => onDecision(application.id, 'accepted')}>Хочу участвовать<ArrowRight size={16}/></Button><Button variant="ghost" disabled={busy} onClick={() => onDecision(application.id, 'declined')}>Не подходит</Button></div> : !meeting ? <p className="cp-waiting-line"><Clock3 size={16}/>Ждём предложения о встрече от работодателя.</p> : <div className="cp-employer-meeting"><div><CalendarDays size={21}/><div><strong>{dateTimeLabel(meeting.start)}</strong><p>{meeting.status === 'pending' ? 'Работодатель предлагает время. Подтвердите, если вам удобно.' : meeting.status === 'completed' ? 'Встреча завершена' : 'Встреча подтверждена'} · {timeZone()}</p></div></div>{meeting.status === 'pending' && <div className="cp-actions"><Button disabled={busy} onClick={() => onConfirm(application.id, meeting.id)}>Подтвердить время<Check size={16}/></Button>{safeSupportEmail(channels) && <a href={`mailto:${safeSupportEmail(channels)}?subject=${encodeURIComponent(`Другое время встречи: ${vacancy.title}`)}`}>Нужно другое время</a>}</div>}{['confirmed', 'live'].includes(meeting.status) && <Button onClick={() => onJoin(application.id)}><Video size={17}/>Войти во встречу</Button>}{meeting.status === 'completed' && <Button variant="secondary" onClick={() => onJoin(application.id)}><Video size={17}/>Материалы встречи</Button>}</div>}
  </article>;
}

function ContactSettings({ candidate, channels, onRefresh, compact = false }: { candidate: CandidateState; channels: Channels; onRefresh: () => Promise<void>; compact?: boolean }) {
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [telegramLink, setTelegramLink] = useState('');
  const emailState = typeof channels.email === 'object' ? channels.email : undefined;
  const telegramState = typeof channels.telegram === 'object' ? channels.telegram : undefined;
  const emailConfigured = emailState?.configured === true || channels.email === true || channels.emailConfigured === true;
  const telegramConfigured = telegramState?.configured === true || channels.telegram === true || channels.telegramConfigured === true;
  const emailVerified = emailState?.verified === true;
  const telegramLinked = telegramState?.linked === true;
  const telegramCanLink = telegramState?.canLink === true || channels.telegramBindingAvailable === true;
  const emailDelivery = emailState?.lastDelivery;
  const emailDescription = !emailConfigured ? (emailVerified ? 'Почта подтверждена. Уведомления пока недоступны.' : 'Отправка писем пока не подключена') : emailVerified ? 'Почта подтверждена' : emailDelivery?.status === 'delivered' ? 'Письмо отправлено. Откройте ссылку в нём.' : emailDelivery?.status === 'pending' || emailDelivery?.status === 'sending' ? 'Письмо ожидает отправки' : emailDelivery?.status === 'error' ? 'Не удалось отправить письмо. Попробуйте ещё раз.' : 'Подтвердите почту для уведомлений';
  async function verifyEmail() {
    setBusy('email'); setError(''); setMessage('');
    try { const result = await candidateRequest<{ status?: string; retryAfter?: number }>('email/request', {}); setMessage(result.status === 'delivered' ? 'Письмо отправлено. Откройте ссылку для подтверждения.' : result.status === 'verified' ? 'Почта уже подтверждена.' : result.status === 'disabled' ? 'Отправка писем пока не подключена.' : result.status === 'rate_limited' ? `Повторный запрос доступен через ${Math.max(1, Math.ceil((result.retryAfter || 60) / 60))} мин.` : result.status === 'error' ? 'Не удалось отправить письмо. Попробуйте позже.' : 'Письмо ожидает отправки. Статус обновится здесь.'); await onRefresh(); }
    catch (cause) { setError((cause as Error).message); }
    finally { setBusy(''); }
  }
  async function connectTelegram() {
    setBusy('telegram'); setError(''); setMessage('');
    try { const result = await candidateRequest<{ url: string | null }>('telegram/link', {}); if (!result.url) throw new Error('Подключение бота пока недоступно.'); const link = new URL(result.url); if (link.protocol !== 'https:' || !['t.me', 'telegram.me'].includes(link.hostname)) throw new Error('Не удалось получить ссылку на бота.'); setTelegramLink(link.toString()); }
    catch (cause) { setError((cause as Error).message); }
    finally { setBusy(''); }
  }
  return <section className={`cp-contacts ${compact ? 'cp-contacts-compact' : ''}`}><h2>Остаёмся на связи</h2><div className="cp-contact-row"><Mail size={20}/><div><strong>{candidate.email}</strong><span>{emailDescription}</span></div>{emailVerified ? <Check size={18}/> : emailConfigured && <Button variant="secondary" size="sm" disabled={!!busy} onClick={verifyEmail}>{busy === 'email' ? 'Запрашиваем…' : 'Подтвердить'}</Button>}</div><div className="cp-contact-row"><Send size={20}/><div><strong>Telegram</strong><span>{telegramLinked ? telegramConfigured ? 'Бот подключён' : 'Бот связан с вами. Уведомления пока недоступны.' : telegramConfigured ? 'Статусы и приглашения в боте' : 'Бот пока не подключён'}</span></div>{telegramLinked ? <Check size={18}/> : telegramCanLink && (telegramLink ? <a className="cp-contact-link" href={telegramLink} target="_blank" rel="noopener noreferrer">Открыть бота<ArrowRight size={15}/></a> : <Button variant="secondary" size="sm" disabled={!!busy} onClick={connectTelegram}>{busy === 'telegram' ? 'Готовим…' : 'Подключить'}</Button>)}</div>{message && <p role="status" className="cp-contact-status">{message}</p>}{error && <p role="alert" className="cp-error">{error}</p>}</section>;
}
