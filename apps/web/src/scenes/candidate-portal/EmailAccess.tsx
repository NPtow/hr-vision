import { useEffect, useState } from 'react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { candidateRequest, type CandidateState } from './api';
import { candidateAfterProfile } from './candidate-navigation';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';

let entryToken: string | undefined;
const exchanges = new Map<string, Promise<CandidateState>>();
export function EmailAccess() {
  const [token] = useState(() => {
    const value = entryToken ?? new URLSearchParams(location.hash.slice(1)).get('access') ?? '';
    entryToken = value;
    const clean = new URL(location.href); clean.hash = ''; history.replaceState({}, '', clean);
    return value;
  });
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    if (!token) { setError('Откройте ссылку из письма или запросите новую.'); return; }
    if (!exchanges.has(token)) exchanges.set(token, candidateRequest<CandidateState>('email-access/consume', { token }));
    void exchanges.get(token)!.then(candidate => {
      if (!active) return;
      const url = new URL(location.href); url.hash = ''; url.searchParams.set('cp', candidateAfterProfile(candidate)); location.replace(url);
    }).catch(cause => { if (active) setError((cause as Error).message); });
    return () => { active = false; };
  }, [token]);
  return <div className="agency-product-theme cp-root cp-surfaces"><header className="cp-topbar"><span className="cp-logo"><img src={logo} alt="HR Vision"/></span></header><main className="cp-main"><section className="cp-auth-card">{error ? <><h1>Нужна новая ссылка</h1><p className="cp-muted" role="alert">{error}</p><EmailLinkForm/></> : <p role="status">Открываем ваш кабинет…</p>}</section></main></div>;
}

export function EmailLinkForm() {
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState('');
  return <form className="cp-auth-form" onSubmit={async event => {
    event.preventDefault(); if (busy) return; setBusy(true); setError('');
    try { await candidateRequest('email-access/request', { email: email.trim() }); setSent(true); }
    catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }}>
    {sent ? <p role="status">Если кабинет с этой почтой существует, письмо со ссылкой придёт на неё. Проверьте входящие и спам.</p> : <><label>Почта<Input type="email" autoComplete="email" required value={email} onChange={event => setEmail(event.target.value)}/></label><Button disabled={busy}>{busy ? 'Отправляем…' : 'Получить ссылку для входа'}</Button></>}
    {error && <p role="alert" className="cp-error">{error}</p>}
  </form>;
}
