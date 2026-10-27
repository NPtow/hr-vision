import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowLeft, CalendarDays, Check, ClipboardList, Copy, Menu, Plus, RefreshCw, Users, Video, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { request, sessionKey, useEntryAccess } from '../connected-journey/api';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import '../employer-workspace/employer.css';
import './recruiter.css';
import { availableMeetingStarts, candidateStatus, formatDate, fromMoscowInput, meetingStatus, moscowInput, recruiterRequest, type Application, type Materials, type RecruiterCandidate, type RecruiterState, type Vacancy } from './api';
import { RecruiterCall, type ActiveCall } from './RecruiterCall';

type Page = 'candidates' | 'slots' | 'vacancies';
type RunAction = (path: string, body: unknown) => Promise<boolean>;

/** Private HR Vision workspace. The server separately verifies a manager session on every request. */
export function RecruiterPortal() {
  const access = useEntryAccess();
  const [token, setToken] = useState('');
  const [state, setState] = useState<RecruiterState | null>(null);
  const [page, setPage] = useState<Page>('candidates');
  const [selectedId, setSelectedId] = useState('');
  const [search, setSearch] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [showRegistrationLink, setShowRegistrationLink] = useState(false);
  const [activeCall, setActiveCall] = useState<ActiveCall | null>(null);
  const sequence = useRef(0);
  const mutation = useRef(false);

  const refresh = useCallback(async () => {
    if (!token || mutation.current) return;
    const seq = ++sequence.current;
    try {
      const next = await recruiterRequest<RecruiterState>('state', token);
      if (seq === sequence.current) setState(next);
    } catch (e) { if (seq === sequence.current) setError((e as Error).message); }
  }, [token]);

  useEffect(() => {
    if (access.status !== 'ready') return;
    let alive = true;
    // Manager session creation requires the private team browser grant. Candidate
    // registration never grants this capability and has a different session.
    void request<{ token: string }>('session', undefined, { actor: 'manager', scope: 'sfera' }).then(session => {
      if (!alive) return;
      sessionStorage.setItem(sessionKey, session.token); setToken(session.token);
    }).catch(e => { if (alive) setError(e.message); });
    return () => { alive = false; sequence.current++; };
  }, [access.status]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => { if (document.visibilityState === 'visible') void refresh(); }, 8000);
    return () => { clearInterval(timer); sequence.current++; };
  }, [refresh]);

  async function run(path: string, body: unknown) {
    if (mutation.current || !token) return false;
    mutation.current = true; sequence.current++; setBusy(true); setError(''); setNotice('');
    try {
      const next = await recruiterRequest<RecruiterState>(path, token, body);
      setState(next); return true;
    } catch (e) { setError((e as Error).message); return false; }
    finally { mutation.current = false; setBusy(false); }
  }

  const navigate = (next: Page) => { setPage(next); setMenuOpen(false); setNotice(''); };
  const candidate = state?.candidates.find(person => person.id === selectedId) || null;
  const people = state?.candidates.filter(person => `${person.name} ${person.email} ${person.desiredRole}`.toLocaleLowerCase('ru').includes(search.toLocaleLowerCase('ru'))) || [];

  if (access.status !== 'ready') return <div className="ew-root rp-root"><main className="rp-access"><img src={logo} alt="HR Vision"/><h1>{access.status === 'checking' ? 'Проверяем доступ…' : 'Вход для команды'}</h1>{access.status !== 'checking' && <><p>{access.status === 'error' ? 'Не удалось проверить доступ.' : 'Откройте рабочую ссылку, которую вам предоставила команда HR Vision.'}</p><Button variant="secondary" onClick={() => void access.check()}>Проверить доступ</Button></>}</main></div>;

  return <div className="ew-root rp-root">
    <div className="rp-shell" inert={activeCall ? true : undefined} aria-hidden={activeCall ? true : undefined}>
      <header className="ew-topbar rp-topbar">
        <button className="rp-menu" aria-label={menuOpen ? 'Закрыть меню' : 'Открыть меню'} aria-expanded={menuOpen} aria-controls="rp-navigation" onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? <X size={20}/> : <Menu size={20}/>}</button>
        <button className="ew-logo" onClick={() => navigate('candidates')} aria-label="HR Vision"><img src={logo} alt="HR Vision"/></button>
        <a className="rp-employer-link" href={employerLink()}>Кабинет работодателя</a>
      </header>
      <div className="rp-body">
        <aside className="rp-sidebar" id="rp-navigation" data-open={menuOpen}>
          <nav aria-label="Рабочее место рекрутера">
            <button aria-current={page === 'candidates' ? 'page' : undefined} onClick={() => navigate('candidates')}><Users size={18}/>Кандидаты</button>
            <button aria-current={page === 'slots' ? 'page' : undefined} onClick={() => navigate('slots')}><CalendarDays size={18}/>Расписание</button>
            <button aria-current={page === 'vacancies' ? 'page' : undefined} onClick={() => navigate('vacancies')}><ClipboardList size={18}/>Вакансии</button>
          </nav>
        </aside>
        <main className="rp-main">
          <header className="rp-page-heading"><h1>{page === 'candidates' ? 'Кандидаты' : page === 'slots' ? 'Расписание интервью' : 'Вакансии'}</h1><div className="rp-actions">{page === 'candidates' && <Button variant="secondary" onClick={() => setShowRegistrationLink(!showRegistrationLink)} aria-expanded={showRegistrationLink}>Ссылка кандидату</Button>}<Button variant="ghost" size="icon" aria-label="Обновить данные" disabled={busy || !token} onClick={() => { setError(''); void refresh(); }}><RefreshCw size={18}/></Button></div></header>
          {page === 'candidates' && showRegistrationLink && <div className="rp-registration-link"><label>Регистрация кандидата<input readOnly value={registrationLink()} onFocus={e => e.target.select()}/></label><Button variant="secondary" onClick={async () => { try { await navigator.clipboard.writeText(registrationLink()); setNotice('Ссылка скопирована.'); } catch { setError('Выделите и скопируйте ссылку из поля.'); } }}><Copy size={16}/>Скопировать</Button></div>}
          {error && <div className="rp-error" role="alert">{error}</div>}
          {notice && <p className="rp-notice" role="status"><Check size={16}/>{notice}</p>}
          {!state ? <div className="rp-empty"><p>{error ? 'Данные не загрузились.' : 'Загружаем…'}</p>{error && <Button variant="secondary" onClick={() => { setError(''); if (token) void refresh(); else void access.check(); }}>Повторить</Button>}</div> : <>
            {page === 'candidates' && <div className="rp-candidates" data-selected={!!candidate}>
              <section className="rp-candidate-list" aria-label="Зарегистрированные кандидаты">
                <input className="rp-search" type="search" aria-label="Найти кандидата" placeholder="Найти кандидата" value={search} onChange={e => setSearch(e.target.value)}/>
                {people.length ? people.map(person => <button key={person.id} className="rp-person-row" aria-pressed={candidate?.id === person.id} onClick={() => setSelectedId(person.id)}><strong>{person.name || person.email}</strong><span>{person.desiredRole || person.email}</span><small>{candidateStatus(person)}</small></button>) : <p className="rp-muted rp-list-empty">{search ? 'Никого не нашли' : 'Пока никто не зарегистрировался'}</p>}
              </section>
              {candidate ? <CandidateDetail key={candidate.id} candidate={candidate} vacancies={state.vacancies} token={token} videoConfigured={state.videoConfigured} busy={busy} run={run} onBack={() => setSelectedId('')} onCall={setActiveCall} onNotice={setNotice}/> : <div className="rp-empty"><Users size={28}/><h2>{state.candidates.length ? 'Выберите кандидата' : 'Новые кандидаты появятся здесь'}</h2><p>{state.candidates.length ? 'Профиль, интервью и подходящие вакансии в одном месте.' : 'После регистрации кандидат выбирает свободное время из вашего расписания.'}</p>{!state.slots.some(slot => !slot.booked) && <Button onClick={() => navigate('slots')}>Добавить время для интервью</Button>}</div>}
            </div>}
            {page === 'slots' && <SlotsPage state={state} busy={busy} run={run} onCandidate={id => { setSelectedId(id); navigate('candidates'); }}/ >}
            {page === 'vacancies' && <VacanciesPage vacancies={state.vacancies} busy={busy} run={run}/ >}
          </>}
        </main>
      </div>
    </div>
    {activeCall && <RecruiterCall key={`${activeCall.kind}-${activeCall.applicationId || activeCall.candidateId}`} active={activeCall} token={token} onClose={() => { setActiveCall(null); void refresh(); }} onJoined={() => void refresh()} onCompleted={() => { setActiveCall(null); setNotice(activeCall.kind === 'interview' ? 'Интервью завершено. Кандидату доступен его кабинет.' : 'Встреча завершена.'); void refresh(); }}/ >}
  </div>;
}

function registrationLink() { return location.origin + '/iframe.html?id=hr-vision-product--candidate&viewMode=story'; }

function employerLink() { const url = new URL(location.href); url.searchParams.set('id', 'hr-vision-product--manager'); url.searchParams.delete('workspace'); return url.pathname + url.search; }

function CandidateDetail({ candidate, vacancies, token, videoConfigured, busy, run, onBack, onCall, onNotice }: {
  candidate: RecruiterCandidate; vacancies: Vacancy[]; token: string; videoConfigured: boolean; busy: boolean;
  run: RunAction; onBack: () => void; onCall: (call: ActiveCall) => void; onNotice: (message: string) => void;
}) {
  const [consent, setConsent] = useState(false);
  const [materials, setMaterials] = useState<Materials | null>(null);
  const [mediaBusy, setMediaBusy] = useState(false);
  const [mediaError, setMediaError] = useState('');
  const [vacancyId, setVacancyId] = useState('');
  const [recommendationReason, setRecommendationReason] = useState('');
  const [summary, setSummary] = useState(candidate.internalProfile?.summary || '');
  const [evaluation, setEvaluation] = useState(candidate.internalProfile?.evaluation || '');
  const interview = candidate.interview;
  const availableVacancies = vacancies.filter(vacancy => !candidate.applications.some(application => application.vacancy.id === vacancy.id));
  async function loadMaterials() {
    setMediaBusy(true); setMediaError('');
    try { setMaterials(await recruiterRequest<Materials>('interview/materials', token, { candidateId: candidate.id })); }
    catch (e) { setMediaError((e as Error).message); }
    finally { setMediaBusy(false); }
  }
  async function saveInternal(event: FormEvent) {
    event.preventDefault();
    if (await run('profile', { candidateId: candidate.id, summary, evaluation })) onNotice('Внутренний профиль сохранён.');
  }
  return <section className="rp-detail">
    <button className="rp-back" onClick={onBack}><ArrowLeft size={16}/>Все кандидаты</button>
    <header className="rp-person-heading"><div className="rp-avatar" aria-hidden="true">{candidate.name.split(' ').filter(Boolean).slice(0, 2).map(word => word[0]).join('') || '?'}</div><div><h2>{candidate.name || candidate.email}</h2><p>{[candidate.desiredRole, candidate.city].filter(Boolean).join(' · ')}</p><span className="rp-muted">{candidate.email}{candidate.telegram ? ` · ${candidate.telegram}` : ''}</span></div></header>
    <section className="rp-card">
      <div className="rp-section-heading"><h3>Первичное интервью</h3><span className="rp-muted">{candidateStatus(candidate)}</span></div>
      {interview ? <>
        <p>{formatDate(interview.start)} · 30 минут</p><p className="rp-muted">{interview.recruiterName}</p>
        {interview.status !== 'completed' ? <>
          <label className="rp-check"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)}/><span>Согласен на запись и расшифровку интервью</span></label>
          <Button disabled={!consent || !videoConfigured || busy} onClick={() => onCall({ kind: 'interview', candidateId: candidate.id, title: candidate.name })}><Video size={16}/>{interview.status === 'live' ? 'Вернуться в интервью' : 'Подключиться к интервью'}</Button>
          {!videoConfigured && <p className="rp-muted">Видеосвязь ещё не подключена.</p>}
        </> : <>
          <Button variant="secondary" disabled={mediaBusy} onClick={loadMaterials}><RefreshCw size={16}/>{mediaBusy ? 'Проверяем…' : 'Открыть запись и расшифровку'}</Button>
          {mediaError && <p role="alert" className="rp-error">{mediaError}</p>}
          {materials && <div className="rp-materials">{materials.recordings.length ? materials.recordings.map(recording => <video key={recording.id} src={recording.url} controls preload="metadata" aria-label={`Запись интервью · ${candidate.name}`}/>) : <p className="rp-muted">Запись ещё не доступна. Проверьте позже.</p>}{materials.transcripts?.map(transcript => <details key={transcript.id}><summary>Расшифровка</summary><pre>{transcript.text}</pre></details>)}</div>}
        </>}
      </> : <p className="rp-muted">Кандидат ещё выбирает время в расписании.</p>}
    </section>
    <section className="rp-info-grid">
      <div><h3>О себе</h3><p className="rp-prewrap">{candidate.about || 'Кандидат пока ничего не добавил.'}</p></div>
      <div><h3>Предпочтения</h3><p>{candidate.geographies.join(', ') || candidate.city || 'Не указаны'}</p>{candidate.workMode && <p className="rp-muted">{{ office: 'В офисе', hybrid: 'Гибрид', remote: 'Удалённо', any: 'Любой формат работы' }[candidate.workMode]}{candidate.desiredSalary ? ` · ${candidate.desiredSalary}` : ''}</p>}<p className="rp-muted">{candidate.autoApprove ? 'Автосогласование подходящих вакансий включено' : 'Каждую вакансию согласует сам'}</p></div>
    </section>
    <section className="rp-section">
      <h3>Вакансии</h3>
      {interview?.status === 'completed' ? <>
        {!candidate.consentReuse ? <p className="rp-muted">Кандидат пока не разрешил использовать интервью для подбора вакансий.</p> : availableVacancies.length > 0 && <form className="rp-recommend-form" onSubmit={async e => { e.preventDefault(); if (await run('applications', { candidateId: candidate.id, vacancyId, recommendationReason: recommendationReason.trim() })) { setVacancyId(''); setRecommendationReason(''); onNotice('Вакансия предложена кандидату.'); } }}>
          <label className="rp-visually-hidden" htmlFor="rp-vacancy-choice">Выберите вакансию</label><select id="rp-vacancy-choice" required value={vacancyId} onChange={e => { setVacancyId(e.target.value); setRecommendationReason(''); }}><option value="">Выберите вакансию</option>{availableVacancies.map(vacancy => <option key={vacancy.id} value={vacancy.id}>{vacancy.title} · {vacancy.company}</option>)}</select>
          {vacancyId && <><label>Почему подходит<textarea required rows={3} maxLength={2000} value={recommendationReason} onChange={e => setRecommendationReason(e.target.value)}/></label><Button disabled={busy || !recommendationReason.trim()}>Предложить</Button></>}
        </form>}
        {!vacancies.length && <p className="rp-muted">Добавьте вакансию в разделе «Вакансии».</p>}
        {candidate.applications.length ? candidate.applications.map(application => <ApplicationCard key={application.id} application={application} candidate={candidate} busy={busy} run={run} onCall={onCall} videoConfigured={videoConfigured}/>) : vacancies.length > 0 && <p className="rp-muted">Пока нет предложенных вакансий.</p>}
      </> : <p className="rp-muted">Предлагать вакансии можно после первичного интервью.</p>}
    </section>
    <form className="rp-section rp-internal" onSubmit={saveInternal}>
      <div className="rp-section-heading"><h3>Внутренний профиль</h3><span className="rp-muted">Виден только команде</span></div>
      <label>Краткое резюме<textarea value={summary} onChange={e => setSummary(e.target.value)} maxLength={12000} rows={4}/></label>
      <label>Оценка и заметки<textarea value={evaluation} onChange={e => setEvaluation(e.target.value)} maxLength={12000} rows={4}/></label>
      <Button variant="secondary" disabled={busy}>Сохранить</Button>
    </form>
  </section>;
}

function ApplicationCard({ application, candidate, busy, run, onCall, videoConfigured }: {
  application: Application; candidate: RecruiterCandidate; busy: boolean; run: RunAction; onCall: (call: ActiveCall) => void; videoConfigured: boolean;
}) {
  const [start, setStart] = useState('');
  const [consent, setConsent] = useState(false);
  const starts = availableMeetingStarts(candidate.availability);
  const meeting = application.meeting;
  return <article className="rp-application">
    <div className="rp-section-heading"><div><h4>{application.vacancy.title}</h4><p className="rp-muted">{application.vacancy.company} · {application.vacancy.remote ? 'Удалённо' : application.vacancy.city}</p></div><span className="rp-status">{application.status === 'pending' ? 'Ждём согласия' : application.status === 'declined' ? 'Отказ' : application.approvedAutomatically ? 'Согласовано автоматически' : 'Кандидат согласен'}</span></div>
    {application.recommendationReason && <div className="rp-recommendation"><p className="rp-muted">Рекомендация рекрутера</p><p className="rp-prewrap">{application.recommendationReason}</p></div>}
    {application.status === 'accepted' && !meeting && <>
      {starts.length ? <form className="rp-inline-form" onSubmit={async e => { e.preventDefault(); await run('meetings', { applicationId: application.id, start }); }}>
        <label className="rp-visually-hidden" htmlFor={`rp-time-${application.id}`}>Время в расписании кандидата</label><select id={`rp-time-${application.id}`} required value={start} onChange={e => setStart(e.target.value)}><option value="">Свободное время кандидата</option>{starts.map(time => <option key={time} value={time}>{formatDate(time)}</option>)}</select><Button disabled={!start || busy}>Предложить встречу</Button>
      </form> : <p className="rp-muted">Кандидату нужно добавить доступное время для встречи.</p>}
      <p className="rp-muted rp-help">Время встречи кандидат подтверждает отдельно.</p>
    </>}
    {meeting && <div className="rp-meeting"><p>{formatDate(meeting.start)} · 30 минут</p><p className="rp-muted">{meetingStatus[meeting.status]}</p>{['confirmed', 'live'].includes(meeting.status) && <><label className="rp-check"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)}/><span>Согласен на запись и расшифровку встречи</span></label><Button disabled={!consent || !videoConfigured || busy} onClick={() => onCall({ kind: 'meeting', candidateId: candidate.id, applicationId: application.id, title: `${candidate.name} · ${application.vacancy.title}` })}><Video size={16}/>{meeting.status === 'live' ? 'Вернуться во встречу' : 'Подключиться ко встрече'}</Button>{!videoConfigured && <p className="rp-muted">Видеосвязь ещё не подключена.</p>}</>}</div>}
  </article>;
}

function SlotsPage({ state, busy, run, onCandidate }: { state: RecruiterState; busy: boolean; run: RunAction; onCandidate: (id: string) => void }) {
  const [start, setStart] = useState('');
  const [name, setName] = useState('');
  const [showPast, setShowPast] = useState(false);
  const [formError, setFormError] = useState('');
  const slots = state.slots.filter(slot => showPast || Date.parse(slot.end) > Date.now()).sort((a, b) => a.start.localeCompare(b.start));
  async function submit(event: FormEvent) {
    event.preventDefault(); setFormError('');
    let iso = '';
    try { iso = fromMoscowInput(start); if (Date.parse(iso) <= Date.now()) throw new Error(); }
    catch { setFormError('Выберите будущее время.'); return; }
    if (await run('slots', { start: iso, recruiterName: name.trim() })) setStart('');
  }
  return <div className="rp-page-content">
    <form className="rp-card rp-slot-form" onSubmit={submit}><h2>Добавить время</h2><div className="rp-fields"><label>Рекрутер<input required value={name} onChange={e => setName(e.target.value)} placeholder="Имя и фамилия" maxLength={120}/></label><label>Начало · МСК<input required type="datetime-local" min={moscowInput(new Date())} value={start} onChange={e => setStart(e.target.value)}/></label></div><div className="rp-section-heading"><span className="rp-muted">Интервью длится 30 минут</span><Button disabled={busy || !name.trim() || !start}><Plus size={16}/>Добавить</Button></div>{formError && <p role="alert" className="rp-error">{formError}</p>}</form>
    <div className="rp-section-heading rp-space"><h2>Расписание</h2><label className="rp-check"><input type="checkbox" checked={showPast} onChange={e => setShowPast(e.target.checked)}/><span>Показать прошедшие</span></label></div>
    {slots.length ? <div className="rp-slot-list">{slots.map(slot => { const person = state.candidates.find(candidate => candidate.id === slot.candidateId); return <article key={slot.id} className="rp-slot-row"><div><strong>{formatDate(slot.start)}</strong><p className="rp-muted">{slot.recruiterName}</p></div>{slot.booked ? <Button variant="secondary" disabled={!person} onClick={() => person && onCandidate(person.id)}>{person?.name || 'Забронировано'}</Button> : <span className="rp-muted">Свободно</span>}</article>; })}</div> : <div className="rp-empty rp-short"><CalendarDays size={28}/><h2>Свободное время ещё не добавлено</h2><p>Кандидаты увидят здесь созданные слоты при записи на интервью.</p></div>}
  </div>;
}

function VacanciesPage({ vacancies, busy, run }: { vacancies: Vacancy[]; busy: boolean; run: RunAction }) {
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState(''); const [company, setCompany] = useState(''); const [city, setCity] = useState(''); const [remote, setRemote] = useState(false); const [description, setDescription] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (await run('vacancies', { title: title.trim(), company: company.trim(), city: city.trim(), remote, description: description.trim() })) { setAdding(false); setTitle(''); setCompany(''); setCity(''); setRemote(false); setDescription(''); }
  }
  return <div className="rp-page-content"><div className="rp-section-heading"><p className="rp-muted">Вакансии HR Vision для предложений кандидатам</p><Button onClick={() => setAdding(true)} disabled={adding}><Plus size={16}/>Добавить вакансию</Button></div>
    {adding && <form className="rp-card rp-vacancy-form" onSubmit={submit}><div className="rp-section-heading"><h2>Новая вакансия</h2><Button type="button" variant="ghost" size="icon" aria-label="Закрыть форму вакансии" onClick={() => setAdding(false)}><X size={18}/></Button></div><div className="rp-fields"><label>Название<input required value={title} onChange={e => setTitle(e.target.value)} maxLength={180}/></label><label>Компания<input required value={company} onChange={e => setCompany(e.target.value)} maxLength={180}/></label><label>Город<input required={!remote} value={city} onChange={e => setCity(e.target.value)} maxLength={120}/></label><label className="rp-check"><input type="checkbox" checked={remote} onChange={e => setRemote(e.target.checked)}/><span>Удалённая работа</span></label></div><label>О вакансии<textarea required rows={5} value={description} onChange={e => setDescription(e.target.value)} maxLength={12000}/></label><Button disabled={busy}>Сохранить вакансию</Button></form>}
    {vacancies.length ? <div className="rp-vacancy-list">{vacancies.map(vacancy => <article key={vacancy.id} className="rp-card"><h2>{vacancy.title}</h2><p className="rp-muted">{vacancy.company} · {vacancy.remote ? 'Удалённо' : vacancy.city}</p><p className="rp-prewrap">{vacancy.description}</p></article>)}</div> : !adding && <div className="rp-empty"><ClipboardList size={28}/><h2>Пока нет вакансий</h2><p>Добавьте первую, чтобы предложить её кандидату после интервью.</p></div>}
  </div>;
}
