import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../../components/ui/dialog';
import './recruiter-account.css';

export function RecruiterInvite({ open, onOpenChange, busy, error, onCreate }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  busy: boolean;
  error: string;
  onCreate: (values: { name: string; email: string }) => Promise<{ activationUrl: string } | null>;
}) {
  const id = useId();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [activationUrl, setActivationUrl] = useState('');
  const [formError, setFormError] = useState('');
  const [copied, setCopied] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const pending = useRef(false);
  const locked = busy || submitting;

  useEffect(() => {
    if (!open) { setName(''); setEmail(''); setActivationUrl(''); setFormError(''); setCopied(false); }
  }, [open]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (locked || pending.current) return;
    if (!name.trim()) { setFormError('Укажите имя рекрутера.'); return; }
    pending.current = true; setSubmitting(true); setFormError('');
    try {
      const result = await onCreate({ name: name.trim(), email: email.trim() });
      if (result) setActivationUrl(result.activationUrl);
    } catch { setFormError('Не удалось добавить рекрутера. Попробуйте ещё раз.'); }
    finally { pending.current = false; setSubmitting(false); }
  }

  return <Dialog open={open} onOpenChange={value => { if (!locked) onOpenChange(value); }}>
    <DialogContent className="rp-auth rp-invite" showCloseButton={!locked}>
      <DialogHeader>
        <DialogTitle>{activationUrl ? 'Рекрутер добавлен' : 'Добавить рекрутера'}</DialogTitle>
        <DialogDescription>{activationUrl ? 'Передайте личную ссылку рекрутеру: он задаст пароль и войдёт в свой кабинет.' : 'Создайте аккаунт и получите ссылку для первого входа.'}</DialogDescription>
      </DialogHeader>
      {activationUrl ? <div className="rp-invite-result">
        <label htmlFor={`${id}-link`}>Ссылка для первого входа<Input id={`${id}-link`} readOnly value={activationUrl} onFocus={event => event.target.select()}/></label>
        <Button type="button" onClick={async () => {
          try { await navigator.clipboard.writeText(activationUrl); setCopied(true); setFormError(''); }
          catch { setFormError('Выделите и скопируйте ссылку из поля.'); }
        }}>{copied ? 'Скопировано' : 'Скопировать ссылку'}</Button>
        {copied && <span className="rp-auth-hint" role="status">Ссылка скопирована.</span>}
        {formError && <p className="rp-auth-error" role="alert">{formError}</p>}
      </div> : <form className="rp-auth-form" onSubmit={submit} aria-busy={locked}>
        <fieldset disabled={locked}>
          <label htmlFor={`${id}-name`}>Имя и фамилия<Input id={`${id}-name`} name="recruiterName" required maxLength={140} value={name} onChange={event => { setName(event.target.value); setFormError(''); }}/></label>
          <label htmlFor={`${id}-email`}>Почта<Input id={`${id}-email`} name="recruiterEmail" type="email" required autoCapitalize="none" spellCheck={false} maxLength={254} value={email} onChange={event => { setEmail(event.target.value); setFormError(''); }}/></label>
        </fieldset>
        {(error || formError) && <p className="rp-auth-error" role="alert">{error || formError}</p>}
        <Button type="submit" disabled={locked}>{locked ? 'Добавляем…' : 'Добавить рекрутера'}</Button>
      </form>}
    </DialogContent>
  </Dialog>;
}
