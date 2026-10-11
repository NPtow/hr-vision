import { useState } from 'react';
import { Copy, Link2, RefreshCw } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { recruiterRequest, type Invitation } from './api';

export function InvitationControls({ candidateId, scope, token, onChanged }: { candidateId: string; scope: 'call' | 'claim'; token: string; onChanged?: () => void }) {
  const [invitation, setInvitation] = useState<Invitation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const label = scope === 'call' ? 'Ссылка на встречу' : 'Ссылка на готовый профиль';
  async function copy(url: string) {
    try { await navigator.clipboard.writeText(url); setNotice('Ссылка скопирована.'); }
    catch { setNotice('Скопируйте ссылку из поля.'); }
  }
  async function create(replace = false) {
    if (busy) return; setBusy(true); setError(''); setNotice('');
    try {
      const result = await recruiterRequest<Invitation>('invitations', token, { candidateId, scope, replace });
      setInvitation(result); await copy(result.url); onChanged?.();
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }
  async function revoke() {
    if (busy) return; setBusy(true); setError(''); setNotice('');
    try { await recruiterRequest('invitations/revoke', token, { candidateId, scope }); setInvitation(null); setNotice('Ссылка отозвана.'); onChanged?.(); }
    catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="rp-invitation">
    {!invitation ? <Button type="button" variant="secondary" disabled={busy} onClick={() => create()}><Link2 size={16}/>{busy ? 'Готовим ссылку…' : label}</Button> : <>
      <label>{label}<input readOnly value={invitation.url} onFocus={event => event.target.select()} aria-label={label}/></label>
      <div className="rp-actions"><Button type="button" variant="secondary" disabled={busy} onClick={() => void copy(invitation.url)}><Copy size={15}/>Скопировать</Button><Button type="button" variant="ghost" disabled={busy} onClick={() => create(true)} aria-label={`Обновить: ${label}`}><RefreshCw size={15}/>Обновить</Button><Button type="button" variant="ghost" disabled={busy} onClick={revoke}>Отозвать</Button></div>
      <p className="rp-muted rp-help">При обновлении прежняя ссылка перестанет работать.</p>
    </>}
    {notice && <p className="rp-muted rp-help" role="status">{notice}</p>}
    {error && <p className="rp-error" role="alert">{error}</p>}
  </div>;
}
