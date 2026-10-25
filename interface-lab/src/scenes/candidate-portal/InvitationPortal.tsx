import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { CalendarDays, CheckCircle2, Download, FileText, RefreshCw, Video } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import { candidateRequest, dateTimeLabel, type CandidateState, type ClaimState, type GuestState } from './api';
import { InterviewRoom } from './InterviewRoom';

const storageKey = (kind: string) => `hr-vision-invitation:${kind}`;

/** Invitation secrets stay out of URLs after entry and are never sent as query parameters. */
function takeInvitation(kind: 'guest' | 'claim') {
  const fragment = new URLSearchParams(location.hash.slice(1));
  const supplied = fragment.get(kind === 'guest' ? 'invite' : 'claim');
  if (supplied !== null) {
    const url = new URL(location.href); url.hash = '';
    history.replaceState({}, '', url);
    try { sessionStorage.setItem(storageKey(kind), supplied); } catch { /* The current tab can still use its link. */ }
    return supplied;
  }
  try { return sessionStorage.getItem(storageKey(kind)) || ''; } catch { return ''; }
}

function openCabinet(page: 'home' | 'login') {
  const url = new URL(location.href); url.hash = ''; url.searchParams.set('cp', page);
  location.replace(url);
}

export function InvitationPortal({ kind }: { kind: 'guest' | 'claim' }) {
  const [token, setToken] = useState(() => takeInvitation(kind));
  useEffect(() => {
    const changed = () => setToken(takeInvitation(kind));
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, [kind]);
  return <div className="agency-product-theme cp-root">
    <header className="cp-topbar"><span className="cp-logo"><img src={logo} alt="HR Vision"/></span></header>
    <main className="cp-main">
      {!token ? <section className="cp-form-page cp-invitation"><h1>Нужна ссылка от рекрутера</h1><p className="cp-muted">Откройте приглашение целиком. Если ссылка больше не работает, попросите рекрутера отправить новую.</p></section>
        : kind === 'guest' ? <GuestInvitation key={token} token={token}/> : <ClaimInvitation key={token} token={token}/>}
    </main>
  </div>;
}

function GuestInvitation({ token }: { token: string }) {
  const [state, setState] = useState<GuestState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [entering, setEntering] = useState(false);
  const refresh = useCallback(async () => {
    try { setState(await candidateRequest<GuestState>('guest/state', { token })); setError(''); }
    catch (cause) { setError((cause as Error).message); }
    finally { setLoading(false); }
  }, [token]);
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void refresh(); }, 10000);
    return () => clearInterval(timer);
  }, [refresh]);
  // A meeting invitation grants call access only. The cabinet keeps its normal
  // session check and asks for sign-in when this browser has no session.
  useEffect(() => { if (state?.interview.status === 'completed') openCabinet('home'); }, [state?.interview.status]);
  if (loading) return <div className="cp-loading" role="status">Открываем приглашение…</div>;
  if (!state) return <section className="cp-form-page cp-invitation"><h1>Приглашение недоступно</h1><p className="cp-error" role="alert">{error}</p><p className="cp-muted">Попросите рекрутера проверить или обновить ссылку.</p><Button variant="secondary" onClick={() => void refresh()}><RefreshCw size={16}/>Проверить ещё раз</Button></section>;
  if (state.interview.status === 'completed') return <section className="cp-form-page cp-invitation"><CheckCircle2 size={32}/><h1>Спасибо за разговор</h1><Button onClick={() => openCabinet('home')}>В личный кабинет</Button></section>;
  if (entering) return <><InterviewRoom title={`Интервью · ${state.interview.recruiterName}`} start={state.interview.start} completed={false} configured={state.interview.videoConfigured ?? state.videoConfigured} callProvider={state.interview.callProvider} guestToken={token} onRefresh={refresh} onBack={() => setEntering(false)} onExit={() => openCabinet('home')}/>{error && <p className="cp-error cp-invitation-error" role="alert">{error}</p>}</>;
  return <section className="cp-form-page cp-invitation">
    <span className="cp-large-icon"><Video size={25}/></span>
    <h1>{state.name ? `${state.name.split(' ')[0]}, до встречи` : 'До встречи на интервью'}</h1>
    <p className="cp-muted">Знакомство с HR Vision и разговор о вашем опыте. Регистрироваться перед встречей не нужно.</p>
    <div className="cp-card cp-invitation-details"><CalendarDays size={21}/><div><strong>{dateTimeLabel(state.interview.start)}</strong><p className="cp-muted">30 минут · {state.interview.recruiterName}</p></div></div>
    {state.interview.callProvider !== 'zoom' && <p className="cp-muted">Начнём без записи. Рекрутер объяснит, как работает сервис, и спросит ваше согласие на запись интервью.</p>}
    {error && <p className="cp-error" role="alert">{error}</p>}
    <Button onClick={() => setEntering(true)}><Video size={17}/>Подготовиться к встрече</Button>
  </section>;
}

function ClaimInvitation({ token }: { token: string }) {
  const [state, setState] = useState<ClaimState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [consent, setConsent] = useState(false);
  useEffect(() => {
    let alive = true;
    void candidateRequest<ClaimState>('claim/state', { token }).then(next => { if (alive) setState(next); }).catch(cause => { if (alive) setError((cause as Error).message); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [token]);
  async function download() {
    setDownloading(true); setError('');
    try {
      const result = await candidateRequest<{ filename: string; mime: string; contentBase64: string }>('claim/resume', { token });
      const bytes = Uint8Array.from(atob(result.contentBase64), character => character.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: result.mime }));
      const link = document.createElement('a'); link.href = url; link.download = result.filename; link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) { setError((cause as Error).message); }
    finally { setDownloading(false); }
  }
  async function activate(event: FormEvent) {
    event.preventDefault(); if (busy || !consent) return;
    setBusy(true); setError('');
    try {
      await candidateRequest<CandidateState>('claim', { token, email: email.trim(), password, consentReuse: true });
      try { sessionStorage.removeItem(storageKey('claim')); } catch { /* No persisted token in restricted storage. */ }
      openCabinet('home');
    } catch (cause) { setError((cause as Error).message); setBusy(false); }
  }
  if (loading) return <div className="cp-loading" role="status">Открываем ваш профиль…</div>;
  if (!state) return <section className="cp-form-page cp-invitation"><h1>Профиль недоступен</h1><p className="cp-error" role="alert">{error}</p><p className="cp-muted">Попросите рекрутера новую ссылку на профиль.</p></section>;
  if (state.claimed) return <section className="cp-form-page cp-invitation"><CheckCircle2 size={32}/><h1>Кабинет уже создан</h1><p className="cp-muted">Войдите с почтой и паролем, которые вы указали при регистрации.</p><Button onClick={() => openCabinet('login')}>Войти в кабинет</Button></section>;
  const person = state.candidate;
  if (!person) return <section className="cp-form-page cp-invitation"><h1>Профиль недоступен</h1><p className="cp-muted">Попросите рекрутера новую ссылку.</p></section>;
  return <section className="cp-form-page cp-invitation cp-claim">
    <h1>Ваш профиль готов</h1><p className="cp-muted">Создайте кабинет, чтобы получать подходящие вакансии. Резюме и интервью уже здесь — загружать их повторно не нужно.</p>
    <div className="cp-card cp-claim-profile"><h2>{person.name || 'Ваш профиль'}</h2>{person.city && <p className="cp-muted">{person.city}</p>}{person.about && <p className="cp-prewrap">{person.about}</p>}{person.resume && <div className="cp-resume-current"><FileText size={23}/><div><strong>{person.resume.filename}</strong></div><Button variant="ghost" size="icon" aria-label="Скачать подготовленное резюме" disabled={downloading} onClick={download}><Download size={18}/></Button></div>}</div>
    <form className="cp-auth-form" onSubmit={activate}>
      <label>Почта<Input type="email" autoComplete="email" required maxLength={254} value={email} onChange={event => setEmail(event.target.value)} disabled={busy}/></label>
      <label>Пароль<Input type="password" autoComplete="new-password" minLength={10} maxLength={128} required value={password} onChange={event => setPassword(event.target.value)} disabled={busy}/></label>
      <label className="cp-check"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} disabled={busy}/><span>Согласен на использование резюме и интервью для участия в подходящих вакансиях. Каждую вакансию согласую сам.</span></label>
      {error && <p className="cp-error" role="alert">{error}</p>}
      <Button disabled={busy || !consent}>{busy ? 'Создаём кабинет…' : 'Создать кабинет'}</Button>
    </form>
    <p className="cp-muted cp-help">Профиль можно дополнить в кабинете. Создание кабинета не прерывает интервью в другой вкладке.</p>
  </section>;
}
