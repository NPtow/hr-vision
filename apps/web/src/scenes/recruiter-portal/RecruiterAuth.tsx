import { useId, useRef, useState, type FormEvent } from 'react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import '../agency-product/theme.css';
import './recruiter-account.css';

export type RecruiterAuthMode = 'register' | 'login';
export type RecruiterAuthValues = { name?: string; email: string; password: string };

export function RecruiterAuth({ busy, error, onSubmit, activationToken, onActivate }: {
  busy: boolean;
  error: string;
  onSubmit: (mode: RecruiterAuthMode, values: RecruiterAuthValues) => Promise<boolean>;
  onModeChange?: () => void;
  initialMode?: RecruiterAuthMode;
  activationToken?: string;
  onActivate?: (token: string, password: string) => Promise<boolean>;
}) {
  const id = useId();
  const [values, setValues] = useState({ email: '', password: '' });
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState('');
  const pending = useRef(false);
  const activation = Boolean(activationToken);
  const locked = busy || submitting;
  const message = error || formError;

  function changeField(field: keyof typeof values, value: string) {
    setValues(current => ({ ...current, [field]: value }));
    setFormError('');
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (locked || pending.current) return;
    if (activation && !onActivate) {
      setFormError('Не удалось открыть настройку аккаунта. Обновите страницу.');
      return;
    }
    pending.current = true;
    setSubmitting(true);
    setFormError('');
    try {
      const saved = activation
        ? await onActivate!(activationToken!, values.password)
        : await onSubmit('login', { email: values.email.trim(), password: values.password });
      if (saved) setValues(current => ({ ...current, password: '' }));
    } catch {
      setFormError(activation ? 'Не удалось сохранить пароль. Попробуйте ещё раз.' : 'Не удалось войти. Попробуйте ещё раз.');
    } finally {
      pending.current = false;
      setSubmitting(false);
    }
  }

  return <div className="agency-product-theme rp-auth">
    <header className="rp-auth-header">
      <img className="rp-auth-logo" src={logo} alt="HR Vision"/>
    </header>
    <main className="rp-auth-main">
      <section className="rp-auth-card" aria-labelledby={`${id}-title`}>
        <h1 id={`${id}-title`}>{activation ? 'Создайте пароль' : 'Вход в кабинет рекрутера'}</h1>
        <form className="rp-auth-form" onSubmit={submit} aria-busy={locked}>
          <fieldset disabled={locked}>
            {!activation && <label htmlFor={`${id}-email`}>Почта
              <Input id={`${id}-email`} name="email" type="email" autoComplete="username" autoCapitalize="none" spellCheck={false} required maxLength={254} value={values.email} onChange={event => changeField('email', event.target.value)}/>
            </label>}
            <label htmlFor={`${id}-password`}>Пароль
              <Input id={`${id}-password`} name="password" type="password" autoComplete={activation ? 'new-password' : 'current-password'} minLength={10} maxLength={128} required aria-describedby={activation ? `${id}-password-hint` : undefined} value={values.password} onChange={event => changeField('password', event.target.value)}/>
              {activation && <span id={`${id}-password-hint`} className="rp-auth-hint">Не менее 10 символов</span>}
            </label>
          </fieldset>
          {message && <p className="rp-auth-error" role="alert">{message}</p>}
          <Button type="submit" disabled={locked}>{locked ? (activation ? 'Сохраняем…' : 'Входим…') : (activation ? 'Сохранить пароль и войти' : 'Войти')}</Button>
        </form>
      </section>
    </main>
  </div>;
}
