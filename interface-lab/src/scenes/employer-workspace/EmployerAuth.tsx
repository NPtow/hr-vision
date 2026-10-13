import { useId, useRef, useState, type FormEvent } from 'react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import logo from './assets/hr-vision-logo.svg';
import { COMPANY_SITE_URL } from '../../lib/employer-entry';
import '../agency-product/theme.css';
import './employer-auth.css';

export type EmployerAuthMode = 'register' | 'login';
export type EmployerAuthValues = {
  email: string;
  password: string;
  name?: string;
  workspaceName?: string;
};

export function EmployerAuth({ busy, error, onSubmit, onModeChange, initialMode = 'register' }: {
  busy: boolean;
  error: string;
  onSubmit: (mode: EmployerAuthMode, values: EmployerAuthValues) => Promise<boolean>;
  onModeChange?: () => void;
  initialMode?: EmployerAuthMode;
}) {
  const [mode, setMode] = useState<EmployerAuthMode>(initialMode);
  const [values, setValues] = useState({ name: '', workspaceName: '', email: '', password: '' });
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState('');
  const pending = useRef(false);
  const id = useId();
  const isRegistration = mode === 'register';
  const locked = busy || submitting;
  const message = error || formError;

  function changeMode() {
    if (locked || pending.current) return;
    setMode(current => current === 'register' ? 'login' : 'register');
    setValues(current => ({ ...current, password: '' }));
    setFormError('');
    onModeChange?.();
  }

  function changeField(field: keyof typeof values, value: string) {
    setValues(current => ({ ...current, [field]: value }));
    setFormError('');
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (locked || pending.current) return;
    const name = values.name.trim();
    const workspaceName = values.workspaceName.trim();
    if (isRegistration && !name) {
      setFormError('Укажите ваше имя.');
      return;
    }
    pending.current = true;
    setSubmitting(true);
    setFormError('');
    try {
      const saved = await onSubmit(mode, {
        email: values.email.trim(),
        password: values.password,
        ...(isRegistration ? { name, ...(workspaceName ? { workspaceName } : {}) } : {}),
      });
      if (saved) setValues(current => ({ ...current, password: '' }));
    } catch {
      setFormError(isRegistration ? 'Не удалось создать кабинет. Попробуйте ещё раз.' : 'Не удалось войти. Попробуйте ещё раз.');
    } finally {
      pending.current = false;
      setSubmitting(false);
    }
  }

  return <div className="agency-product-theme ew-auth">
    <header className="ew-auth-header">
      <a href={COMPANY_SITE_URL} aria-label="На сайт HR Vision"><img className="ew-auth-logo" src={logo} alt="HR Vision"/></a>
      <Button type="button" variant="ghost" disabled={locked} onClick={changeMode}>{isRegistration ? 'Войти' : 'Создать кабинет'}</Button>
    </header>
    <main className="ew-auth-main">
      <section className="ew-auth-card" aria-labelledby={`${id}-title`}>
        <h1 id={`${id}-title`}>{isRegistration ? 'Регистрация работодателя' : 'Вход в кабинет'}</h1>
        <form className="ew-auth-form" onSubmit={submit} aria-busy={locked}>
          <fieldset disabled={locked}>
            {isRegistration && <>
              <label htmlFor={`${id}-name`}>Ваше имя
                <Input id={`${id}-name`} name="name" autoComplete="name" required maxLength={140} value={values.name} onChange={event => changeField('name', event.target.value)}/>
              </label>
              <label htmlFor={`${id}-company`}><span>Компания <span className="ew-auth-optional">необязательно</span></span>
                <Input id={`${id}-company`} name="organization" autoComplete="organization" maxLength={200} value={values.workspaceName} onChange={event => changeField('workspaceName', event.target.value)}/>
              </label>
            </>}
            <label htmlFor={`${id}-email`}>Рабочая почта
              <Input id={`${id}-email`} name="email" type="email" autoComplete="username" autoCapitalize="none" spellCheck={false} required maxLength={254} value={values.email} onChange={event => changeField('email', event.target.value)}/>
            </label>
            <label htmlFor={`${id}-password`}>Пароль
              <Input id={`${id}-password`} name="password" type="password" autoComplete={isRegistration ? 'new-password' : 'current-password'} minLength={10} maxLength={128} required aria-describedby={isRegistration ? `${id}-password-hint` : undefined} value={values.password} onChange={event => changeField('password', event.target.value)}/>
              {isRegistration && <span id={`${id}-password-hint`} className="ew-auth-hint">Не менее 10 символов</span>}
            </label>
          </fieldset>
          {message && <p className="ew-auth-error" role="alert">{message}</p>}
          <Button type="submit" disabled={locked}>{locked ? (isRegistration ? 'Создаём кабинет…' : 'Входим…') : (isRegistration ? 'Создать кабинет' : 'Войти')}</Button>
        </form>
        <p className="ew-auth-switch">{isRegistration ? 'Уже есть аккаунт?' : 'Первый раз здесь?'} <button type="button" disabled={locked} onClick={changeMode}>{isRegistration ? 'Войти' : 'Зарегистрироваться'}</button></p>
      </section>
    </main>
  </div>;
}
