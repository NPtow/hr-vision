import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowLeft, ArrowUpRight, Check, CircleAlert, Pause, Play, Plus } from 'lucide-react';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import { outreachRequest, OutreachError, parseResumeURLs, parseVacancy, safeHHLink,
  type BrowserState, type Campaign, type CampaignDetail, type CampaignMode, type CampaignStats, type DiscoveryStarted, type DiscoveryState, type Recipient } from './api';
import { useOperatorSession } from './session';
import { useDiscovery } from './discovery';
import './outreach.css';

const labels: Record<string, string> = {
  draft: 'Черновик', running: 'Идёт рассылка', active: 'Идёт рассылка', paused: 'На паузе', completed: 'Завершена',
  queued: 'В очереди', imported: 'Подготовлен', claimed: 'Готовится отправка', send_started: 'Отправляется',
  sent_observed: 'Сообщение в чате', unknown: 'Нужна проверка', failed: 'Ошибка', blocked: 'Приостановлен',
  retry_wait: 'Ожидает повтора', cancelled: 'Отменён', booked: 'Записался', skipped: 'Пропущен',
};
const statusLabel = (status?: string) => status ? labels[status] || status : 'Подготовлен';
const reasons: Record<string, string> = { logged_out: 'Нужно войти в HH', challenge: 'HH просит проверку', identity_mismatch: 'Аккаунт или чат не совпадает', ui_changed: 'Интерфейс HH изменился', cv_missing: 'Не удалось получить CV', booking_unavailable: 'Ссылка на запись недоступна', already_booked: 'Кандидат уже записан', campaign_paused: 'Кампания на паузе', lease_expired: 'Обработка прервалась', transport_error: 'Браузер недоступен', unconfirmed: 'Результат отправки не подтверждён', operator_pause: 'Остановлено оператором', duplicate: 'Повторный кандидат', invalid_evidence: 'Отправка не подтверждена' };
const reasonLabel = (reason: string) => reasons[reason] || 'Нужна проверка оператором';
const sourceLabel = (mode: CampaignMode) => mode === 'direct' ? 'Выбранные кандидаты' : 'Отклики на вакансию';
const number = (value: number | undefined) => value === undefined ? '—' : value.toLocaleString('ru-RU');

export function HHOutreach() {
  const auth = useOperatorSession();
  return <main className="hho-root">
    <header className="hho-header"><img src={logo} alt="HR Vision"/><span>Рассылка HH</span><a style={{ marginLeft: 'auto', color: '#81798d', fontSize: 13 }} href="/iframe.html?id=hr-vision-product--manager&viewMode=story&workspace=selection">Подборки ↗</a></header>
    {auth.status === 'ready' ? <OperatorWorkspace key={auth.token} token={auth.token} onExpired={auth.expire}/> :
      <section className="hho-surface hho-access" aria-live="polite">
        <h1>{auth.status === 'checking' ? 'Проверяем доступ' : 'Доступ для команды HR Vision'}</h1>
        {auth.status !== 'checking' && <><p>{auth.error || 'Откройте рабочую ссылку команды, чтобы управлять рассылкой.'}</p>
          <button className="hho-button hho-secondary" onClick={() => void auth.check()}>Проверить доступ</button></>}
      </section>}
  </main>;
}

function OperatorWorkspace({ token, onExpired }: { token: string; onExpired: () => void }) {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [browser, setBrowser] = useState<BrowserState | null>(null);
  const [selectedId, setSelectedId] = useState('');
  const [detail, setDetail] = useState<CampaignDetail | null>(null);
  const [creating, setCreating] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [fetchedAt, setFetchedAt] = useState('');
  const [acknowledgedJobs, setAcknowledgedJobs] = useState<Set<string>>(() => new Set());
  const acknowledgedJobsRef = useRef(new Set<string>());
  const pending = useRef(false);
  const sequence = useRef(0);
  const alive = useRef(true);

  const load = useCallback(async () => {
    const seq = ++sequence.current;
    try {
      const [list, runtime, selected] = await Promise.all([
        outreachRequest<{ campaigns: Campaign[] }>('campaigns', token),
        outreachRequest<BrowserState>('browser', token),
        selectedId ? outreachRequest<CampaignDetail>('campaigns/' + encodeURIComponent(selectedId), token) : Promise.resolve(null),
      ]);
      if (!alive.current || seq !== sequence.current) return;
      setCampaigns(list.campaigns); setBrowser(runtime); setDetail(selected);
      setFetchedAt(new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }));
      setError('');
    } catch (cause) {
      if (!alive.current || seq !== sequence.current) return;
      if (cause instanceof OutreachError && [401, 403].includes(cause.status)) { onExpired(); return; }
      setError((cause as Error).message);
    } finally { if (alive.current && seq === sequence.current) setLoading(false); }
  }, [token, selectedId, onExpired]);
  const discovery = useDiscovery(token, selectedId, onExpired, load);
  useEffect(() => { alive.current = true; return () => { alive.current = false; sequence.current++; }; }, []);
  useEffect(() => {
    setLoading(true); setDetail(null); void load();
    const timer = setInterval(() => { if (!pending.current && document.visibilityState === 'visible') void load(); }, 10000);
    return () => { sequence.current++; clearInterval(timer); };
  }, [load]);

  async function run<T,>(label: string, action: () => Promise<T>): Promise<T | undefined> {
    if (pending.current) return;
    pending.current = true; sequence.current++; setBusy(label); setError(''); setNotice('');
    try {
      const result = await action();
      if (!alive.current) return;
      await load();
      return result;
    } catch (cause) {
      if (!alive.current) return;
      if (cause instanceof OutreachError && [401, 403].includes(cause.status)) onExpired();
      else setError((cause as Error).message);
    } finally { if (alive.current) { pending.current = false; setBusy(''); } }
  }
  async function create(values: Record<string, unknown>) {
    const result = await run('Сохраняем черновик', () => outreachRequest<{ campaign: Campaign }>('campaigns', token, values));
    if (result) { setSelectedId(result.campaign.id); setCreating(false); setNotice('Черновик сохранён. Подготовьте кандидатов перед запуском.'); }
  }
  async function discover(source: string) {
    if (!detail) return;
    const body = detail.campaign.mode === 'direct' ? { resumeUrls: parseResumeURLs(source) } : { vacancyUrl: source.trim() };
    const result = await run('Запускаем подготовку', () => outreachRequest<DiscoveryStarted>('campaigns/' + encodeURIComponent(detail.campaign.id) + '/discover', token, body));
    if (result) discovery.started(result);
    // A timed-out POST may already have queued an operation. Read its persisted
    // state instead of inviting a second import or treating preparation as done.
    await discovery.read();
  }
  async function start() {
    if (!detail) return;
    const base = 'campaigns/' + encodeURIComponent(detail.campaign.id);
    const result = await run('Запускаем рассылку', async () => {
      await outreachRequest(base + '/queue', token, {});
      return outreachRequest<{ campaign: Campaign }>(base + '/resume', token, {});
    });
    if (result) setNotice('Кампания запущена. Результаты отправок появятся здесь.');
  }
  async function pause() {
    if (!detail) return;
    const result = await run('Ставим на паузу', () => outreachRequest<{ campaign: Campaign }>('campaigns/' + encodeURIComponent(detail.campaign.id) + '/pause', token, {}));
    if (result) setNotice('Кампания на паузе. Уже начатая отправка может завершиться.');
  }
  async function resumeBrowser() {
    const result = await run('Продолжаем работу браузера', async () => {
      if (browser?.manual) await outreachRequest('browser/manual', token, { enabled: false });
      return outreachRequest<BrowserState>('browser/resume', token, {});
    });
    if (result) setNotice('Браузер вернулся к работе. Неопределённые отправки не повторяются.');
  }
  async function retryBlocked(jobId: string) {
    const result = await run('Возвращаем кандидата в очередь', () => outreachRequest('jobs/retry-blocked', token, { jobId }));
    if (result) setNotice('Кандидат возвращён в очередь. Сообщение отправится, когда кампания и браузер работают.');
  }
  async function acknowledgeUnknown(jobId: string) {
    if (acknowledgedJobsRef.current.has(jobId)) return;
    const result = await run('Оставляем отправку на проверке', () => outreachRequest<{ acknowledged: boolean; retryAllowed: false }>('jobs/' + encodeURIComponent(jobId) + '/acknowledge', token, { leaveUnknown: true }));
    if (result?.acknowledged && result.retryAllowed === false) {
      acknowledgedJobsRef.current.add(jobId);
      setAcknowledgedJobs(new Set(acknowledgedJobsRef.current));
      setNotice('Отправка оставлена на проверке. Её не повторим. Можно продолжить работу браузера с другими кандидатами.');
    }
  }
  const selectedDetail = detail?.campaign.id === selectedId ? detail : null;
  return <div className="hho-workspace">
    <div className="hho-title-row"><div><h1>Приглашения на интервью</h1><p>Сообщение в HH → выбор времени → встреча с рекрутером</p></div>
      <button className="hho-button" disabled={!!busy} onClick={() => { setCreating(true); setNotice(''); setError(''); }}><Plus size={18}/>Новая кампания</button></div>
    <BrowserNotice browser={browser} blocked={detail?.account?.blockedReason} busy={!!busy} onResume={resumeBrowser}/>
    {error && <div className="hho-feedback hho-error" role="alert"><CircleAlert size={18}/><span>{error}</span></div>}
    {notice && <div className="hho-feedback" role="status"><Check size={18}/><span>{notice}</span></div>}
    <div className="hho-layout">
      <nav className="hho-campaigns" aria-label="Кампании">
        {campaigns.map(campaign => <button key={campaign.id} className={'hho-campaign' + (selectedId === campaign.id && !creating ? ' is-selected' : '')}
          aria-current={selectedId === campaign.id && !creating ? 'page' : undefined} disabled={!!busy}
          onClick={() => { setSelectedId(campaign.id); setCreating(false); setNotice(''); setError(''); }}>
          <span className="hho-kicker">{sourceLabel(campaign.mode)}</span><strong>{campaign.name}</strong><span className="hho-muted">{statusLabel(campaign.status)}</span>
        </button>)}
        {!campaigns.length && !loading && <p className="hho-muted">Пока нет кампаний</p>}
        {fetchedAt && <small className="hho-updated">Обновлено в {fetchedAt}</small>}
      </nav>
      <div className="hho-main">
        {creating ? <CampaignForm browser={browser} busy={!!busy} onCancel={() => setCreating(false)} onCreate={create}/> :
          selectedDetail ? <CampaignView key={selectedDetail.campaign.id} detail={selectedDetail} discovery={discovery.state} discoveryError={discovery.error} browser={browser} busy={busy} onDiscover={discover} onStart={start} onPause={pause} onRetryBlocked={retryBlocked} onAcknowledgeUnknown={acknowledgeUnknown} acknowledgedJobs={acknowledgedJobs}/> :
          <section className="hho-surface hho-empty" aria-live="polite"><h2>{loading ? 'Загружаем кампании' : 'Один путь от приглашения до интервью'}</h2>
            {!loading && <p>{campaigns.length ? 'Выберите кампанию слева, чтобы увидеть кандидатов и статистику.' : 'Создайте черновик, подготовьте кандидатов из HH и проверьте сообщение перед запуском.'}</p>}</section>}
      </div>
    </div>
  </div>;
}

function browserReady(browser: BrowserState | null) { return !!browser?.configured && browser.loggedIn && !browser.captcha && !browser.paused && !browser.manual && browser.calibrationReady === true; }
function BrowserNotice({ browser, blocked, busy, onResume }: { browser: BrowserState | null; blocked?: string; busy: boolean; onResume: () => Promise<void> }) {
  const message = !browser ? 'Проверяем виртуальный браузер' : !browser.configured ? 'Виртуальный браузер ещё не подключён' : browser.captcha ? 'HH просит проверку — рассылка приостановлена' : !browser.loggedIn ? 'В виртуальном браузере нужно войти в HH' : browser.manual ? 'Браузер открыт для ручной работы' : browser.paused ? 'Работа браузера приостановлена' : !browser.calibrationReady ? 'Отправка ещё не проверена на текущем интерфейсе HH' : browser.busy ? 'Виртуальный браузер занят' : 'Виртуальный браузер готов';
  return <div className={'hho-browser' + (browserReady(browser) ? ' is-ready' : '')}><span className="hho-dot"/><span>{browser?.error || message}</span>
    {browser?.configured && (browser.paused || browser.manual || blocked) && <button className="hho-link" disabled={busy || browser.busy || !browser.loggedIn || browser.captcha || !browser.calibrationReady} onClick={() => void onResume()}>{browser.manual ? 'Вернуть браузер рассылке' : 'Продолжить работу браузера'}</button>}
    </div>;
}

function CampaignForm({ browser, busy, onCancel, onCreate }: { browser: BrowserState | null; busy: boolean; onCancel: () => void; onCreate: (values: Record<string, unknown>) => Promise<void> }) {
  const [mode, setMode] = useState<CampaignMode>('direct');
  const [name, setName] = useState(''); const [vacancy, setVacancy] = useState('');
  const [template, setTemplate] = useState(''); const [limit, setLimit] = useState(10); const [pace, setPace] = useState(60);
  const [error, setError] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault(); setError('');
    if (template.split('{booking_url}').length !== 2) { setError('Добавьте {booking_url} в сообщение — на её месте будет персональная ссылка на выбор времени.'); return; }
    const vacancyId = mode === 'applicants' ? parseVacancy(vacancy) : '';
    if (mode === 'applicants' && !vacancyId) { setError('Укажите номер вакансии или ссылку на неё в HH.'); return; }
    if (!browser?.accountId) { setError('Сначала нужно подключить рабочий аккаунт HH к виртуальному браузеру.'); return; }
    await onCreate({ name: name.trim(), accountId: browser.accountId, mode, vacancyId, template: template.trim(), limit, paceSeconds: pace });
  }
  return <section className="hho-surface"><div className="hho-section-heading"><h2>Новая кампания</h2><button className="hho-link" onClick={onCancel} disabled={busy}><ArrowLeft size={16}/>Назад</button></div>
    <form className="hho-form" onSubmit={event => void submit(event)}>
      <label>Название<input value={name} onChange={e => setName(e.target.value)} required maxLength={160} disabled={busy} placeholder="Приглашения · менеджер по продажам"/></label>
      <fieldset disabled={busy}><legend>Кого приглашаем</legend><div className="hho-mode-options">
        {(['direct', 'applicants'] as const).map(value => <label key={value} className={'hho-choice' + (mode === value ? ' is-selected' : '')}><input type="radio" name="campaign-mode" value={value} checked={mode === value} onChange={() => setMode(value)}/>{sourceLabel(value)}</label>)}
      </div></fieldset>
      {mode === 'applicants' && <label>Вакансия HH<input value={vacancy} onChange={e => setVacancy(e.target.value)} required disabled={busy} placeholder="Ссылка на вакансию или её номер"/></label>}
      <label>Сообщение<textarea value={template} onChange={e => setTemplate(e.target.value)} required disabled={busy} rows={6} maxLength={8000} placeholder="Текст приглашения и {booking_url}" aria-describedby="hho-template-help"/></label>
      <p className="hho-field-help" id="hho-template-help">Вместо {'{booking_url}'} каждый кандидат получит свою ссылку на свободные слоты рекрутеров.</p>
      <div className="hho-form-pair"><label>Не больше кандидатов<input type="number" min={1} max={1000} step={1} value={limit} disabled={busy} required onChange={e => setLimit(Number(e.target.value))}/></label>
        <label>Пауза между отправками, секунд<input type="number" min={30} max={86400} step={1} value={pace} disabled={busy} required onChange={e => setPace(Number(e.target.value))}/></label></div>
      {error && <p className="hho-error hho-feedback" role="alert">{error}</p>}
      {!browser?.accountId && <p className="hho-muted">Сохранение станет доступно после подключения рабочего аккаунта HH.</p>}
      <div className="hho-actions"><button className="hho-button" disabled={busy || !browser?.accountId} type="submit">{busy ? 'Сохраняем…' : 'Сохранить черновик'}</button><span className="hho-muted">Черновик не отправляет сообщения</span></div>
    </form>
  </section>;
}

function CampaignView({ detail, discovery, discoveryError, browser, busy, onDiscover, onStart, onPause, onRetryBlocked, onAcknowledgeUnknown, acknowledgedJobs }: { detail: CampaignDetail; discovery: DiscoveryState | null; discoveryError: string; browser: BrowserState | null; busy: string; onDiscover: (source: string) => Promise<void>; onStart: () => Promise<void>; onPause: () => Promise<void>; onRetryBlocked: (jobId: string) => Promise<void>; onAcknowledgeUnknown: (jobId: string) => Promise<void>; acknowledgedJobs: Set<string> }) {
  const { campaign, stats, recipients } = detail;
  const [source, setSource] = useState(''); const [sourceError, setSourceError] = useState('');
  const [confirmStart, setConfirmStart] = useState(false);
  const running = ['running', 'active'].includes(campaign.status);
  const preparing = discovery?.status === 'queued' || discovery?.status === 'running';
  const preparationReady = !!discovery && !preparing && !discoveryError && !['failed', 'interrupted'].includes(discovery.status);
  const ready = browserReady(browser) && browser?.sendingEnabled && detail.sendingEnabled && !detail.account?.blockedReason && !browser?.busy && preparationReady && stats.recipients > 0 && !running && !['completed', 'cancelled'].includes(campaign.status);
  async function prepare(event: FormEvent) {
    event.preventDefault(); setSourceError('');
    try {
      if (campaign.mode === 'direct') parseResumeURLs(source);
      else if (!safeHHLink(source.trim())) throw new Error('Вставьте ссылку на страницу откликов из HH.');
      await onDiscover(source);
    } catch (cause) { setSourceError((cause as Error).message); }
  }
  return <>
    <section className="hho-surface"><div className="hho-section-heading"><div><span className="hho-kicker">{sourceLabel(campaign.mode)}</span><h2>{campaign.name}</h2></div><span className="hho-pill">{statusLabel(campaign.status)}</span></div>
      <Stats stats={stats}/>
      <DiscoveryNotice state={discovery} error={discoveryError}/>
      {(!detail.sendingEnabled || !browser?.sendingEnabled) && <p className="hho-launch-note">Отправка ещё не включена. Можно подготовить кампанию и кандидатов.</p>}
      {detail.account?.blockedReason && <p className="hho-launch-note">Отправка приостановлена: {reasonLabel(detail.account.blockedReason)}.</p>}
      <div className="hho-controls">
        {running ? <button className="hho-button hho-secondary" disabled={!!busy} onClick={() => void onPause()}><Pause size={17}/>Пауза</button> :
          <button className="hho-button" disabled={!!busy || !ready} onClick={() => setConfirmStart(true)}><Play size={17}/>{campaign.status === 'paused' ? 'Продолжить' : 'Запустить'}</button>}
        <span className="hho-muted">До {number(campaign.limit)} кандидатов · интервал {campaign.paceSeconds} с</span>
        {busy && <span role="status">{busy}…</span>}
      </div>
      {confirmStart && !running && <div className="hho-start-review"><h3>Запустить отправку в HH?</h3><p>Кандидаты получат это сообщение с персональной ссылкой на запись.</p><p className="hho-message">{campaign.template.replaceAll('{booking_url}', '[персональная ссылка на выбор времени]')}</p>
        <div className="hho-actions"><button className="hho-button" disabled={!!busy || !ready} onClick={async () => { setConfirmStart(false); await onStart(); }}>Отправлять приглашения</button><button className="hho-link" disabled={!!busy} onClick={() => setConfirmStart(false)}>Отмена</button></div></div>}
      <details className="hho-details"><summary>Сообщение и параметры</summary><p className="hho-message">{campaign.template}</p>{campaign.vacancyId && <p className="hho-muted">Вакансия HH: {campaign.vacancyId}</p>}</details>
    </section>
    {!running && !['completed', 'cancelled'].includes(campaign.status) && <section className="hho-surface"><h2>Подготовить кандидатов</h2><p className="hho-muted">Браузер прочитает CV и подготовит профиль. Сообщения отправляются только после запуска.</p>
      <form className="hho-form" onSubmit={event => void prepare(event)}><label>{campaign.mode === 'direct' ? 'Ссылки на резюме HH' : 'Ссылка на отклики HH'}
        {campaign.mode === 'direct' ? <textarea value={source} onChange={e => setSource(e.target.value)} rows={4} required disabled={!!busy || preparing} placeholder="Каждая ссылка с новой строки"/> :
          <input type="url" value={source} onChange={e => setSource(e.target.value)} required disabled={!!busy || preparing} placeholder="Откройте отклики в HH и скопируйте адрес"/>}</label>
        {sourceError && <p className="hho-feedback hho-error" role="alert">{sourceError}</p>}
        <button className="hho-button hho-secondary" disabled={!!busy || preparing || !!discoveryError || !discovery || !browser?.configured || !browser.loggedIn || browser.captcha || browser.busy || browser.paused || browser.manual} type="submit">{preparing ? 'Подготовка идёт…' : 'Подготовить кандидатов'}</button>
      </form></section>}
    <section className="hho-surface"><div className="hho-section-heading"><h2>Кандидаты <span className="hho-muted">{number(stats.recipients)}</span></h2></div>
      {recipients.length ? <div className="hho-recipients">{recipients.map(recipient => <RecipientRow key={recipient.id} recipient={recipient} busy={!!busy} onRetryBlocked={onRetryBlocked} onAcknowledgeUnknown={onAcknowledgeUnknown} acknowledged={!!recipient.jobId && acknowledgedJobs.has(recipient.jobId)}/>)}</div> : <p className="hho-muted">Пока никто не подготовлен. Добавьте аудиторию из HH.</p>}
    </section>
  </>;
}

function DiscoveryNotice({ state, error }: { state: DiscoveryState | null; error: string }) {
  if (error) return <p className="hho-feedback hho-error" role="alert">{error}</p>;
  if (!state) return <p className="hho-launch-note" role="status">Проверяем подготовку кандидатов…</p>;
  if (state.status === 'idle') return null;
  if (state.status === 'queued' || state.status === 'running') return <p className="hho-launch-note" role="status">{state.status === 'queued' ? 'Подготовка ожидает свободного браузера.' : 'Подготавливаем кандидатов из HH.'} Обновляем результат автоматически. Рассылку можно запустить после завершения.</p>;
  const result = state.result;
  const issues = result?.errors?.map(item => typeof item === 'string' ? item : item.error || item.message || 'Не удалось подготовить кандидата').join(' · ');
  const stopped = state.status === 'failed' || state.status === 'interrupted';
  return <div className={'hho-discovery-result' + (stopped ? ' is-error' : '')} role={stopped ? 'alert' : 'status'}>
    <p>{stopped ? (state.error || (state.status === 'interrupted' ? 'Подготовка прервалась. Повторите её, чтобы закончить.' : 'Не удалось закончить подготовку.')) : result?.complete === false ? 'Подготовлена часть источника.' : 'Подготовка завершена.'}</p>
    {result && <p className="hho-muted">Добавлено: {number(result.imported)} · Уже в кампании: {number(result.repeated)} · Пропущено: {number(result.skipped)}</p>}
    {state.status === 'completed' && result?.complete === false && <p className="hho-muted">Обработана часть источника в пределах лимита кампании.</p>}
    {issues && <p className="hho-error">{issues}</p>}
  </div>;
}

function Stats({ stats }: { stats: CampaignStats }) {
  const counters: [string, keyof CampaignStats][] = [['Сообщение в чате', 'sentObserved'], ['Страница открыта', 'pageLoaded'], ['Забронировано', 'booked'], ['Интервью завершено', 'completed'], ['Запись готова', 'videoReady']];
  return <><dl className="hho-stats">{counters.map(([label, key]) => <div key={key}><dd>{number(stats[key])}</dd><dt>{label}</dt></div>)}</dl>
    <div className="hho-health"><span>Поставлено в очередь: {number(stats.queued)}</span><span>Нужна проверка: {number(stats.unknown)}</span><span>Остановлено: {number(stats.blocked)}</span><span>Транскрипт готов: {number(stats.transcriptReady)}</span></div>
    <p className="hho-stats-note">Уникальные кандидаты. Открытие страницы не подтверждает прочтение сообщения в HH.</p></>;
}

function RecipientRow({ recipient, busy, onRetryBlocked, onAcknowledgeUnknown, acknowledged }: { recipient: Recipient; busy: boolean; onRetryBlocked: (jobId: string) => Promise<void>; onAcknowledgeUnknown: (jobId: string) => Promise<void>; acknowledged: boolean }) {
  const status = recipient.state;
  const link = safeHHLink(recipient.resumeUrl); const error = recipient.reason ? reasonLabel(recipient.reason) : '';
  const progress = recipient.progress;
  return <details className="hho-recipient"><summary><strong>{recipient.name || 'Кандидат HH'}</strong><span className={status === 'unknown' || status === 'blocked' ? 'hho-warning-text' : 'hho-muted'}>{statusLabel(status)}</span></summary>
    <div className="hho-recipient-content">{link && <a href={link} target="_blank" rel="noreferrer noopener">Резюме в HH<ArrowUpRight size={15}/></a>}
      {recipient.resumeId && <span>Резюме HH: {recipient.resumeId}</span>}
      {recipient.vacancyId && <span>Вакансия: {recipient.vacancyId}</span>}{recipient.responseId && <span>Отклик: {recipient.responseId}</span>}
      {progress && <div className="hho-recipient-progress">{progress.pageLoaded && <span>Страница открыта</span>}{progress.booked && <span>Слот забронирован</span>}{progress.completed && <span>Интервью завершено</span>}{progress.videoReady && <span>Запись готова</span>}{progress.transcriptReady && <span>Транскрипт готов</span>}</div>}
      {error && <p className="hho-error">{error}</p>}{status === 'unknown' && <><p>Результат отправки не подтверждён. Это сообщение повторно не отправим. Можно продолжить работу с другими кандидатами.</p>
        {recipient.jobId && (acknowledged ? <span className="hho-muted">Оставлено на проверке</span> : <button className="hho-link" disabled={busy} onClick={() => void onAcknowledgeUnknown(recipient.jobId!)}>Оставить на проверке</button>)}</>}
      {status === 'blocked' && recipient.jobId && <button className="hho-link" disabled={busy} onClick={() => void onRetryBlocked(recipient.jobId!)}>Вернуть в очередь</button>}
    </div></details>;
}
