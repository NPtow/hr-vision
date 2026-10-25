import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Briefcase, CalendarDays, Check, Home, LogOut, Mail, Menu, RefreshCw, Send, UserRound, Video, X } from 'lucide-react';
import { Tooltip } from 'radix-ui';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import { CandidateApiError, candidateRequest, dateLabel, dateTimeLabel, timeLabel, type CandidateApplication, type CandidateWorkflowCommand, type CandidateSession, type CandidateState, type Channels } from './api';
import { PreferencesForm } from './PreferencesForm';
import { ResumeUpload } from './ResumeUpload';
import { InterviewRoom } from './InterviewRoom';
import { InvitationPortal } from './InvitationPortal';
import { BookingPortal } from './BookingPortal';
import { PooledBookingCalendar } from './PooledBookingCalendar';
import type { BookingSlot } from './pooled-booking-model';
import { TelegramHandoff } from './TelegramHandoff';
import { CandidateSearch } from './CandidateSearch';
import { EmailAccess, EmailLinkForm } from './EmailAccess';
import { OnboardingResume } from './OnboardingResume';
import { CandidateCompletion } from './CandidateCompletion';
import { CandidateSchedule } from './CandidateSchedule';
import { CandidateProfile } from './CandidateProfile';
import { PublicCandidateProfile } from './PublicCandidateProfile';
import { applicationGroup, currentApplicationMeeting } from './workspace-model';
import { candidatePage, candidateLoginTarget, candidateNavigationURL, authenticatedCandidatePage as authenticatedPage, candidateSetupPage as setupPage, candidateDestination as destination, candidateAfterProfile, candidateCompletionStep, type CandidatePage as Page } from './candidate-navigation';
import '@fontsource-variable/inter';
import '../agency-product/theme.css';
import './candidate-portal.css';
import './candidate-cabinet.css';
import './candidate-surfaces.css';

const readPage = (): Page => candidatePage(new URLSearchParams(location.search));
const loginTarget = (): Page => candidateLoginTarget(new URLSearchParams(location.search));
const initials = (name: string) => name.split(' ').filter(Boolean).slice(0, 2).map(part => part[0]).join('').toUpperCase();
const safeSupportEmail = (channels?: Channels) => channels?.supportEmail && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(channels.supportEmail) ? channels.supportEmail : null;

export function CandidatePortal() {
  const invitation = new URLSearchParams(location.search).get('cp');
  if (invitation === 'email-access') return <EmailAccess/>;
  if (invitation === 'public') return <PublicCandidateProfile/>;
  if (invitation === 'invite-booking') return <BookingPortal/>;
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
  const menuId = useId();
  const menuToggle = useRef<HTMLButtonElement>(null);
  const [telegramEntry, setTelegramEntry] = useState(() => new URLSearchParams(location.search).get('telegram') === 'connect');
  const [credentials, setCredentials] = useState({ email: '', password: '' });
  const [emailLogin, setEmailLogin] = useState(true);
  const [registrationConsent, setRegistrationConsent] = useState(false);
  const [meetingApplicationId, setMeetingApplicationId] = useState(() => new URLSearchParams(location.search).get('application') || '');
  const [meetingId, setMeetingId] = useState(() => new URLSearchParams(location.search).get('meeting') || '');
  const [selectedId, setSelectedId] = useState(() => new URLSearchParams(location.search).get('application') || '');
  const workflowRequests = useRef(new Map<string, string>());
  const verifying = useRef(false);
  const alive = useRef(true);
  const authGeneration = useRef(0);
  const refreshSequence = useRef(0);
  const mutating = useRef(false);
  const go = useCallback((next: Page, replace = false, applicationId?: string, targetMeetingId?: string, preserveLoginTarget = false) => {
    const url = candidateNavigationURL(location.href, next, applicationId, targetMeetingId, preserveLoginTarget);
    applicationId = url.searchParams.get('application') || undefined;
    targetMeetingId = url.searchParams.get('meeting') || undefined;
    history[replace ? 'replaceState' : 'pushState']({}, '', url);
    setPage(next); setMeetingApplicationId(applicationId || ''); setMeetingId(targetMeetingId || ''); setError(''); setLoadError(''); setNotice(''); setMenuOpen(false);
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
      if (cause instanceof CandidateApiError && cause.status === 401) { setCandidate(null); go('login', true, undefined, undefined, true); setError('Сессия завершилась. Войдите снова.'); }
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
        const query = new URLSearchParams(location.search);
        const nextPage = destination(result.candidate, readPage() === 'login' ? loginTarget() : readPage());
        if (nextPage !== readPage()) go(nextPage, true, query.get('application') || undefined, query.get('meeting') || undefined);
      } else { setCandidate(null); if (authenticatedPage(readPage())) go('login', true, undefined, undefined, true); }
    } catch (cause) { if (alive.current && generation === authGeneration.current) setLoadError((cause as Error).message); }
    finally { if (alive.current && generation === authGeneration.current) setLoading(false); }
  }, [apply, go]);
  useEffect(() => { alive.current = true; void session(); return () => { alive.current = false; }; }, [session]);
  useEffect(() => {
    const onPop = () => { const next = readPage(); setPage(next); setMeetingApplicationId(new URLSearchParams(location.search).get('application') || ''); setMeetingId(new URLSearchParams(location.search).get('meeting') || ''); setSelectedId(new URLSearchParams(location.search).get('application') || ''); setMenuOpen(false); setError(''); setLoadError(''); };
    window.addEventListener('popstate', onPop); return () => window.removeEventListener('popstate', onPop);
  }, []);
  useEffect(() => {
    if (!menuOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setMenuOpen(false);
      menuToggle.current?.focus();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [menuOpen]);
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
    const query = new URLSearchParams(location.search);
    const nextPage = destination(candidate, page === 'login' ? loginTarget() : page);
    if (nextPage !== page) go(nextPage, true, query.get('application') || undefined, query.get('meeting') || undefined);
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
  const nav = showNavigation ? [['home', 'Поиск', Home], ['calendar', 'Календарь', CalendarDays]] as const : [];
  const activePage = ['calendar', 'booking', 'interview'].includes(page) ? 'calendar' : ['vacancies', 'meeting'].includes(page) ? 'home' : page;
  const pendingCount = candidate?.interview?.status === 'completed'
    ? candidate.applications.filter(application => applicationGroup(application) === 'new').length : 0;
  const currentApplication = candidate?.applications.find(application => application.id === meetingApplicationId);
  const currentMeeting = meetingId
    ? currentApplication?.meetings?.find(meeting => meeting.id === meetingId) ?? (currentApplication?.meeting?.id === meetingId ? currentApplication.meeting : undefined)
    : currentApplication?.meeting;
  function openVacancy(applicationId: string) {
    setSelectedId(applicationId); go('home', false, applicationId);
  }
  async function decide(applicationId: string, decision: 'accepted' | 'declined') {
    const next = await mutate('application/decision', { applicationId, decision });
    if (!next) { await refresh(); return false; }
    setSelectedId(applicationId);
    return true;
  }
  async function replyToMeeting(application: CandidateApplication, action: 'confirm' | 'decline') {
    const meeting = currentApplicationMeeting(application);
    if (!meeting || !['pending', 'confirmed'].includes(meeting.status)) return false;
    const next = await mutate(`meeting/${action}`, { applicationId: application.id, meetingId: meeting.id, ...(meeting.revision == null ? {} : { revision: meeting.revision }) }, action === 'confirm' ? 'Время встречи подтверждено.' : 'Попросили другое время. Участие в вакансии сохранено.');
    if (!next) await refresh();
    return !!next;
  }
  async function workflow(command: CandidateWorkflowCommand) {
    const fingerprint = JSON.stringify({ candidateId: candidate?.id, ...command });
    const requestId = command.requestId ?? workflowRequests.current.get(fingerprint) ?? crypto.randomUUID();
    workflowRequests.current.set(fingerprint, requestId);
    const next = await mutate('workflow', { ...command, requestId });
    if (!next) { await refresh(); return false; }
    workflowRequests.current.delete(fingerprint);
    setSelectedId(command.applicationId);
    return true;
  }
  async function changeSearch(searching: boolean) {
    const next = await mutate('search', { searching });
    if (!next) return false;
    setSelectedId(''); go('home');
    return true;
  }
  const telegramChannel = typeof channels.telegram === 'object' ? channels.telegram : undefined;
  useEffect(() => {
    if (!telegramEntry || !telegramChannel?.linked) return;
    const url = new URL(location.href); url.searchParams.delete('telegram');
    history.replaceState(history.state, '', url);
    setTelegramEntry(false); setNotice('Telegram подключён. Вакансии и встречи будут приходить в бот.');
  }, [telegramEntry, telegramChannel?.linked]);
  return <div className={`agency-product-theme cp-root cp-surfaces ${showNavigation ? 'cp-cabinet' : ''} ${candidate?.interview?.status === 'completed' && ['welcome', 'login', 'register', 'home', 'vacancies'].includes(page) ? 'cp-search-feed' : ''}`}>
    <header className="cp-topbar">
      {showNavigation && <button ref={menuToggle} className="cp-menu-toggle" aria-label={menuOpen ? 'Закрыть меню' : 'Открыть меню'} aria-expanded={menuOpen} aria-controls={menuId} onClick={() => setMenuOpen(open => !open)}>{menuOpen ? <X size={20}/> : <Menu size={20}/>}</button>}
      <button className="cp-logo" aria-label="HR Vision · главная" onClick={() => go(candidate ? destination(candidate) : 'welcome')}><img src={logo} alt="HR Vision"/></button>
      {candidate ? <div className="cp-account">{showNavigation && <button className="cp-avatar" aria-current={page === 'profile' ? 'page' : undefined} aria-label="Открыть профиль" onClick={() => go('profile')}>{initials(candidate.name) || <UserRound size={18}/>}</button>}<Button variant="ghost" size="icon" aria-label="Выйти из кабинета" title="Выйти" disabled={busy} onClick={logout}><LogOut size={18}/></Button></div> : <Button className="cp-login-link" variant="ghost" disabled={loading} onClick={() => go(page === 'login' ? 'register' : 'login')}>{page === 'login' ? 'Создать кабинет' : 'Войти'}</Button>}
    </header>
    <div className={`cp-body ${showNavigation ? 'cp-authenticated' : ''}`}>
      {showNavigation && <aside id={menuId} className="cp-sidebar" data-open={menuOpen} aria-label="Разделы кандидата">
        <Tooltip.Provider delayDuration={200} skipDelayDuration={100}>
          <nav aria-label="Кабинет кандидата">{nav.map(([key, label, Icon]) => <Tooltip.Root key={key}>
            <Tooltip.Trigger asChild><button aria-label={label} aria-current={activePage === key ? 'page' : undefined} onClick={() => go(key)}><Icon size={21} aria-hidden="true"/></button></Tooltip.Trigger>
            <Tooltip.Portal><Tooltip.Content className="cp-navigation-tooltip" side="right" sideOffset={12} collisionPadding={12}>{label}{key === 'home' && pendingCount > 0 ? ` · ${pendingCount}` : ''}</Tooltip.Content></Tooltip.Portal>
          </Tooltip.Root>)}</nav>
          {safeSupportEmail(channels) && <Tooltip.Root>
            <Tooltip.Trigger asChild><a className="cp-support" aria-label="Поддержка" href={`mailto:${safeSupportEmail(channels)}`}><Mail size={21} aria-hidden="true"/></a></Tooltip.Trigger>
            <Tooltip.Portal><Tooltip.Content className="cp-navigation-tooltip" side="right" sideOffset={12} collisionPadding={12}>Поддержка</Tooltip.Content></Tooltip.Portal>
          </Tooltip.Root>}
        </Tooltip.Provider>
      </aside>}
      <main className="cp-main" id="candidate-main" aria-busy={loading}>
        {loading ? <div className="cp-loading" role="status"><span className="cp-spinner"/>Открываем кабинет…</div> : <>
          {(error || loadError) && <div className="cp-message cp-error" role="alert"><span>{error || loadError}</span><Button size="sm" variant="ghost" onClick={() => { setError(''); void (candidate ? refresh() : session()); }}>Повторить</Button></div>}
          {notice && <div className="cp-message cp-success" role="status"><Check size={17}/>{notice}<button aria-label="Закрыть сообщение" onClick={() => setNotice('')}><X size={16}/></button></div>}
          {candidate && telegramEntry && !telegramChannel?.linked && !['interview', 'meeting'].includes(page) && <TelegramHandoff key={candidate.id} available={telegramChannel?.canLink === true || channels.telegramBindingAvailable === true}/>}
          {!candidate && page === 'welcome' && <Welcome onStart={() => go('register')}/>}
          {!candidate && (page === 'login' || authenticatedPage(page)) && <section className="cp-auth-card"><h1>С возвращением</h1><p className="cp-muted">Войдите в свой кабинет HR Vision.</p>{emailLogin ? <EmailLinkForm/> : <form className="cp-auth-form" onSubmit={async event => { event.preventDefault(); const next = await mutate('login', { email: credentials.email.trim(), password: credentials.password }); if (next) { setCredentials(value => ({ ...value, password: '' })); const query = new URLSearchParams(location.search); go(destination(next, loginTarget()), true, query.get('application') || undefined, query.get('meeting') || undefined); } }}>
            <label htmlFor="cp-login-email">Почта<Input id="cp-login-email" type="email" autoComplete="username" required value={credentials.email} onChange={event => setCredentials(value => ({ ...value, email: event.target.value }))}/></label>
            <label htmlFor="cp-login-password">Пароль<Input id="cp-login-password" type="password" autoComplete="current-password" required value={credentials.password} onChange={event => setCredentials(value => ({ ...value, password: event.target.value }))}/></label>
            <Button disabled={busy}>{busy ? 'Входим…' : 'Войти'}</Button>
          </form>}<button type="button" className="cp-email-login-switch" onClick={() => setEmailLogin(value => !value)}>{emailLogin ? 'Войти по паролю' : 'Войти по ссылке из письма'}</button><p className="cp-auth-foot">Первый раз здесь? <button onClick={() => go('register')}>Создать кабинет</button></p></section>}
          {!candidate && page === 'register' && <section className="cp-auth-card"><Button className="cp-back" variant="ghost" onClick={() => go('welcome')}><ArrowLeft size={16}/>Назад</Button><h1>Создайте кабинет</h1><p className="cp-muted">Затем загрузите резюме и выберите время интервью.</p><form className="cp-auth-form" onSubmit={async event => { event.preventDefault(); const next = await mutate('register', { email: credentials.email.trim(), password: credentials.password, consentReuse: registrationConsent, autoApprove: false }); if (next) { setCredentials(value => ({ ...value, password: '' })); go(destination(next), true); } }}>
            <label htmlFor="cp-register-email">Почта<Input id="cp-register-email" type="email" autoComplete="username" required value={credentials.email} onChange={event => setCredentials(value => ({ ...value, email: event.target.value }))}/></label>
            <label htmlFor="cp-register-password">Пароль<Input id="cp-register-password" type="password" autoComplete="new-password" minLength={10} maxLength={128} required placeholder="Не менее 10 символов" value={credentials.password} onChange={event => setCredentials(value => ({ ...value, password: event.target.value }))}/></label>
            <label className="cp-check cp-registration-consent"><input type="checkbox" required checked={registrationConsent} onChange={event => setRegistrationConsent(event.target.checked)}/><span>Согласен на запись интервью и использование его материалов для участия в подходящих вакансиях.</span></label>
            <Button disabled={busy}>{busy ? 'Создаём кабинет…' : 'Создать кабинет'}<ArrowRight size={17}/></Button>
          </form><p className="cp-auth-foot">Уже есть кабинет? <button onClick={() => go('login')}>Войти</button></p></section>}
          {candidate && page === 'resume' && <section className="cp-form-page"><div className="cp-page-heading"><h1>Загрузите резюме</h1><p>Заполним профиль из файла. Вы проверите данные и дополните недостающие.</p></div><ResumeUpload resume={candidate.resume} busy={busy} onUpload={async payload => { const next = await mutate('resume', payload); if (!next) return false; go(candidateAfterProfile(next), true); return true; }}/></section>}
          {candidate && page === 'preferences' && <section className="cp-form-page cp-onboarding-profile"><div className="cp-page-heading"><h1>Ваш профиль</h1></div><OnboardingResume candidate={candidate} onReplace={() => go('resume')}/><PreferencesForm key={candidate.id + String(candidate.resume?.uploadedAt)} initial={candidate} missingFields={candidate.onboarding.missingFields} busy={busy} onSubmit={async profile => { const next = await mutate('profile', profile); if (next) go(candidateAfterProfile(next), true); }}/></section>}
          {candidate && ['welcome', 'login', 'register', 'home', 'vacancies'].includes(page) && (!selectedId && candidateCompletionStep(candidate) ? <CandidateCompletion candidate={candidate} busy={busy} onOpen={go} onConsent={() => mutate('profile', { consentReuse: true })}/> : <CandidateSearch candidate={candidate} busy={busy} selectedId={selectedId} onSelect={setSelectedId} onDecision={decide} onMeetingReply={replyToMeeting} onWorkflow={workflow} onJoin={(applicationId, targetMeetingId) => go('meeting', false, applicationId, targetMeetingId)} onBookInterview={() => go('booking')} onInterview={() => go('interview')} onResumeSearch={() => changeSearch(true)}/>)}
          {candidate && page === 'calendar' && <CandidateSchedule candidate={candidate} busy={busy} onTimezone={async timezone => !!await mutate('profile', { timezone })} onAvailability={async availabilitySchedule => !!await mutate('profile', { availabilitySchedule })} onMeetingReply={replyToMeeting} onOpenVacancy={openVacancy} onJoin={(applicationId, targetMeetingId) => go('meeting', false, applicationId, targetMeetingId)} onInterview={() => go('interview')} onBookInterview={() => go('booking')}/>}
          {candidate && page === 'booking' && <RecruiterCalendar candidate={candidate} busy={busy} onBack={() => go('calendar')} onBook={async slotId => { const booked = await mutate('book', { slotId }); if (booked) go('home'); return Boolean(booked); }}/>}
          {candidate && page === 'profile' && <CandidateProfile candidate={candidate} busy={busy} onSave={async profile => !!await mutate('profile', profile, 'Изменения сохранены.')} onUpload={async payload => !!await mutate('resume', payload, 'Резюме сохранено.')} onSearchChange={changeSearch} contacts={<ContactSettings candidate={candidate} channels={channels} onRefresh={refresh}/>} onBack={() => go('home')}/>}
          {candidate && page === 'interview' && (candidate.interview ? <InterviewRoom key={candidate.interview.id} title="Интервью с рекрутером" start={candidate.interview.start} completed={candidate.interview.status === 'completed'} configured={candidate.interview.videoConfigured ?? candidate.videoConfigured} callProvider={candidate.interview.callProvider} onRefresh={refresh} onBack={() => go('home')}/> : <RecruiterCalendar candidate={candidate} busy={busy} onBack={() => go('home')} onBook={async slotId => { const booked = await mutate('book', { slotId }); if (booked) go('home'); return Boolean(booked); }}/>) }
          {candidate && page === 'meeting' && (currentMeeting && ['confirmed', 'live', 'completed'].includes(currentMeeting.status) && (!currentApplication?.archivedAt || currentMeeting.status === 'completed') ? <InterviewRoom key={currentMeeting.id} title={`Встреча · ${currentApplication?.vacancy.company}`} start={currentMeeting.start} completed={currentMeeting.status === 'completed'} configured={currentMeeting.videoConfigured ?? candidate.videoConfigured} callProvider={currentMeeting.callProvider} applicationId={currentApplication!.id} meetingId={currentMeeting.id} onRefresh={refresh} onBack={() => openVacancy(currentApplication!.id)} onExit={() => openVacancy(currentApplication!.id)}/> : <section className="cp-narrow cp-empty"><h1>Встреча недоступна</h1><p>Откройте вакансию, чтобы посмотреть актуальное время и историю встреч.</p><Button onClick={() => currentApplication ? openVacancy(currentApplication.id) : go('home')}>К поиску</Button></section>)}
        </>}
      </main>
    </div>
  </div>;
}

function Welcome({ onStart }: { onStart: () => void }) {
  return <section className="cp-welcome">
    <div className="cp-welcome-copy">
      <h1>Одно интервью —<br/>больше подходящих<br className="cp-desktop-break"/> вакансий</h1>
      <Button size="lg" onClick={onStart}>Начать<ArrowRight size={18}/></Button>
    </div>
    <div className="cp-journey-card">
      <div className="cp-journey-item"><span><UserRound size={22}/></span><h2>Загрузите резюме</h2></div>
      <div className="cp-journey-item"><span><Video size={22}/></span><h2>Познакомьтесь с рекрутером</h2></div>
      <div className="cp-journey-item"><span><Briefcase size={22}/></span><h2>Выбирайте вакансии</h2></div>
    </div>
  </section>;
}

function RecruiterCalendar({ candidate, busy, onBook, onBack }: { candidate: CandidateState; busy: boolean; onBook: (slotId: string) => Promise<boolean>; onBack: () => void }) {
  const [slots, setSlots] = useState<BookingSlot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState('');
  const [timezone, setTimezone] = useState('Europe/Moscow');
  const pending = useRef(false);
  const load = useCallback(async () => {
    setLoading(true); setError('');
    try { const result = await candidateRequest<{ slots: BookingSlot[] }>('slots'); setSlots(result.slots); setSelected(value => result.slots.some(slot => slot.id === value) ? value : ''); }
    catch (cause) { setError((cause as Error).message); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function book() {
    if (pending.current || !selected) return;
    pending.current = true;
    try { if (!await onBook(selected)) await load(); }
    finally { pending.current = false; }
  }
  if (candidate.interview && candidate.interview.status !== 'completed') return <section className="cp-narrow cp-empty"><CalendarDays size={32}/><h1>Вы уже записаны</h1><p>{dateTimeLabel(candidate.interview.start)}</p><Button onClick={onBack}>К интервью</Button></section>;
  return <section className="cp-pooled-booking">
    <button className="pbc-back" type="button" disabled={busy} onClick={onBack}><ArrowLeft size={16}/>В кабинет</button>
    <div className="pbc-surface">
      {loading ? <div className="cp-loading" role="status">Загружаем расписание…</div> : error ? <div className="cp-empty"><p role="alert" className="cp-error">{error}</p><Button variant="secondary" onClick={load}>Попробовать ещё раз</Button></div> : !slots.length ? <div className="cp-empty"><h2>Пока нет свободного времени</h2><Button variant="ghost" onClick={load}>Проверить расписание</Button></div> :
        <PooledBookingCalendar slots={slots} selectedId={selected} onSelect={setSelected} timezone={timezone} onTimezone={setTimezone} disabled={busy}
          continueLabel={busy ? 'Бронируем…' : 'Записаться'} onContinue={() => void book()}/>}
    </div>
  </section>;
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
  const emailDescription = !emailConfigured ? (emailVerified ? 'Подтверждена · уведомления недоступны' : 'Отправка писем недоступна') : emailVerified ? 'Почта подтверждена' : emailDelivery?.status === 'delivered' ? 'Письмо отправлено. Откройте ссылку в нём.' : emailDelivery?.status === 'pending' || emailDelivery?.status === 'sending' ? 'Письмо ожидает отправки' : emailDelivery?.status === 'error' ? 'Не удалось отправить письмо. Попробуйте ещё раз.' : 'Подтвердите почту для уведомлений';
  async function verifyEmail() {
    setBusy('email'); setError(''); setMessage('');
    try { const result = await candidateRequest<{ status?: string; retryAfter?: number }>('email/request', {}); setMessage(result.status === 'delivered' ? 'Письмо отправлено. Откройте ссылку для подтверждения.' : result.status === 'verified' ? 'Почта уже подтверждена.' : result.status === 'disabled' ? 'Отправка писем пока не подключена.' : result.status === 'rate_limited' ? `Повторный запрос доступен через ${Math.max(1, Math.ceil((result.retryAfter || 60) / 60))} мин.` : result.status === 'error' ? 'Не удалось отправить письмо. Попробуйте позже.' : 'Письмо ожидает отправки.'); await onRefresh(); }
    catch (cause) { setError((cause as Error).message); }
    finally { setBusy(''); }
  }
  async function connectTelegram() {
    setBusy('telegram'); setError(''); setMessage('');
    try { const result = await candidateRequest<{ url: string | null }>('telegram/link', {}); if (!result.url) throw new Error('Подключение бота пока недоступно.'); const link = new URL(result.url); if (link.protocol !== 'https:' || !['t.me', 'telegram.me'].includes(link.hostname)) throw new Error('Не удалось получить ссылку на бота.'); setTelegramLink(link.toString()); }
    catch (cause) { setError((cause as Error).message); }
    finally { setBusy(''); }
  }
  return <section className={`cp-contacts ${compact ? 'cp-contacts-compact' : ''}`}><h2>Связь</h2><div className="cp-contact-row"><Mail size={20}/><div><strong>{candidate.email}</strong><span>{emailDescription}</span></div>{emailVerified ? <Check size={18}/> : emailConfigured && <Button variant="secondary" size="sm" disabled={!!busy} onClick={verifyEmail}>{busy === 'email' ? 'Запрашиваем…' : 'Подтвердить'}</Button>}</div><div className="cp-contact-row"><Send size={20}/><div><strong>Telegram</strong><span>{telegramLinked ? telegramConfigured ? 'Бот подключён' : 'Подключён · уведомления недоступны' : telegramConfigured ? 'Приглашения и статусы' : 'Бот пока не подключён'}</span></div>{telegramLinked ? <Check size={18}/> : telegramCanLink && (telegramLink ? <a className="cp-contact-link" href={telegramLink} target="_blank" rel="noopener noreferrer">Открыть бота<ArrowRight size={15}/></a> : <Button variant="secondary" size="sm" disabled={!!busy} onClick={connectTelegram}>{busy === 'telegram' ? 'Готовим…' : 'Подключить'}</Button>)}</div>{message && <p role="status" className="cp-contact-status">{message}</p>}{error && <p role="alert" className="cp-error">{error}</p>}</section>;
}
