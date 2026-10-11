import { useId, useState, type FormEvent } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import { localDate, localTime, timeZone, type CandidateProfile } from './api';

type WindowDraft = { id: string; date: string; start: string; end: string };
export const emptyProfile: CandidateProfile = { name: '', city: '', desiredRole: '', desiredSalary: '', workMode: 'any', about: '', geographies: [], availability: [], consentReuse: false, autoApprove: false };
export function editableProfile(source: CandidateProfile): CandidateProfile {
  return { name: source.name, city: source.city, desiredRole: source.desiredRole, desiredSalary: source.desiredSalary || '', workMode: source.workMode || 'any', about: source.about || '', telegram: source.telegram || '', geographies: [...source.geographies], availability: source.availability.map(window => ({ start: window.start, end: window.end })), consentReuse: source.consentReuse === true, autoApprove: source.autoApprove === true && source.consentReuse === true };
}

export function PreferencesForm({ initial, registering = false, busy, onSubmit, onBack }: {
  initial: CandidateProfile; registering?: boolean; busy: boolean; onSubmit: (profile: CandidateProfile) => Promise<void>; onBack?: () => void;
}) {
  const id = useId();
  const [profile, setProfile] = useState<CandidateProfile>(() => editableProfile(initial));
  const [cities, setCities] = useState(initial.geographies.join(', '));
  const [windows, setWindows] = useState<WindowDraft[]>(() => initial.availability.map((window, index) => ({ id: String(index), date: localDate(new Date(window.start)), start: localTime(window.start), end: localTime(window.end) })));
  const [validation, setValidation] = useState('');
  const update = <K extends keyof CandidateProfile>(key: K, value: CandidateProfile[K]) => setProfile(previous => ({ ...previous, [key]: value }));
  const addWindow = () => setWindows(previous => [...previous, { id: crypto.randomUUID(), date: '', start: '10:00', end: '18:00' }]);
  const editWindow = (windowId: string, key: 'date' | 'start' | 'end', value: string) => setWindows(previous => previous.map(window => window.id === windowId ? { ...window, [key]: value } : window));
  async function submit(event: FormEvent) {
    event.preventDefault();
    setValidation('');
    const geographies = [...new Set((cities || profile.city).split(',').map(city => city.trim()).filter(Boolean))];
    const availability = windows.map(window => ({ start: new Date(`${window.date}T${window.start}`), end: new Date(`${window.date}T${window.end}`) }));
    if (!availability.length) { setValidation('Добавьте хотя бы один интервал, когда вам удобно общаться с работодателями.'); return; }
    if (availability.some(window => !Number.isFinite(window.start.getTime()) || !Number.isFinite(window.end.getTime()) || window.end.getTime() - window.start.getTime() < 30 * 60000 || window.end.getTime() - window.start.getTime() > 12 * 3600000 || (registering && window.start <= new Date()))) {
      setValidation('Укажите интервал от 30 минут до 12 часов' + (registering ? ' в будущем.' : '.')); return;
    }
    if (registering && !profile.consentReuse) { setValidation('Для продолжения нужно ваше согласие на использование интервью.'); return; }
    await onSubmit({ ...editableProfile(profile), name: profile.name.trim(), city: profile.city.trim(), desiredRole: profile.desiredRole.trim(), geographies, availability: availability.map(window => ({ start: window.start.toISOString(), end: window.end.toISOString() })) });
  }
  return <form className="cp-preferences" onSubmit={submit}>
    <div className="cp-form-grid">
      {!registering && <label className="cp-full" htmlFor={`${id}-name`}>Как вас зовут<Input id={`${id}-name`} autoComplete="name" required maxLength={140} value={profile.name} onChange={event => update('name', event.target.value)}/></label>}
      <label htmlFor={`${id}-city`}>Город<Input id={`${id}-city`} required autoComplete="address-level2" placeholder="Где вы живёте" maxLength={120} value={profile.city} onChange={event => update('city', event.target.value)}/></label>
      <label htmlFor={`${id}-role`}>Желаемая должность<Input id={`${id}-role`} required placeholder="Кем хотите работать" maxLength={160} value={profile.desiredRole} onChange={event => update('desiredRole', event.target.value)}/></label>
      <label htmlFor={`${id}-salary`}>Зарплата от, ₽<Input id={`${id}-salary`} inputMode="numeric" placeholder="По желанию" maxLength={80} value={profile.desiredSalary || ''} onChange={event => update('desiredSalary', event.target.value)}/></label>
      <label htmlFor={`${id}-mode`}>Формат работы<select id={`${id}-mode`} value={profile.workMode || 'any'} onChange={event => update('workMode', event.target.value as CandidateProfile['workMode'])}><option value="any">Любой</option><option value="office">В офисе</option><option value="hybrid">Гибридный</option><option value="remote">Удалённо</option></select></label>
      <label className="cp-full" htmlFor={`${id}-cities`}>Где готовы работать<Input id={`${id}-cities`} placeholder={profile.city || 'Города через запятую'} maxLength={500} value={cities} onChange={event => setCities(event.target.value)}/></label>
      <label className="cp-full" htmlFor={`${id}-about`}>Коротко о себе<Textarea id={`${id}-about`} placeholder="Опыт и что важно в следующей работе" maxLength={1500} value={profile.about} onChange={event => update('about', event.target.value)}/></label>
    </div>
    <fieldset className="cp-availability">
      <legend>Когда вам удобно встречаться</legend>
      <p className="cp-muted">Работодатель предложит время в этих интервалах. Вы подтвердите его отдельно. {timeZone()}.</p>
      <div className="cp-window-list">{windows.map((window, index) => <div className="cp-window" key={window.id}>
        <label htmlFor={`${id}-date-${window.id}`}>Дата<Input id={`${id}-date-${window.id}`} type="date" min={registering ? localDate() : undefined} required value={window.date} onChange={event => editWindow(window.id, 'date', event.target.value)}/></label>
        <label htmlFor={`${id}-start-${window.id}`}>С<Input id={`${id}-start-${window.id}`} type="time" required step="900" value={window.start} onChange={event => editWindow(window.id, 'start', event.target.value)}/></label>
        <label htmlFor={`${id}-end-${window.id}`}>До<Input id={`${id}-end-${window.id}`} type="time" required step="900" value={window.end} onChange={event => editWindow(window.id, 'end', event.target.value)}/></label>
        <Button type="button" variant="ghost" size="icon" aria-label={`Удалить интервал ${index + 1}`} onClick={() => setWindows(previous => previous.filter(item => item.id !== window.id))}><Trash2 size={17}/></Button>
      </div>)}</div>
      <Button type="button" variant="secondary" disabled={windows.length >= 40} onClick={addWindow}><Plus size={17}/>Добавить время</Button>
    </fieldset>
    <div className="cp-consent-box">
      <label className="cp-check"><input type="checkbox" checked={profile.consentReuse} onChange={event => setProfile(previous => ({ ...previous, consentReuse: event.target.checked, autoApprove: event.target.checked ? previous.autoApprove : false }))} required={registering}/><span>Согласен на запись интервью и использование его материалов для участия в подходящих вакансиях.</span></label>
      <div className="cp-auto-option"><label className="cp-check"><input type="checkbox" role="switch" checked={profile.autoApprove} disabled={!profile.consentReuse} onChange={event => update('autoApprove', event.target.checked)}/><span><strong>Согласовывать вакансии автоматически</strong><small>По желанию. Только в указанных городах и подходящем формате. Время каждой встречи вы подтверждаете сами.</small></span></label></div>
    </div>
    {validation && <p role="alert" className="cp-error">{validation}</p>}
    <div className="cp-form-actions">{onBack && <Button type="button" variant="ghost" disabled={busy} onClick={onBack}>Назад</Button>}<Button type="submit" disabled={busy}>{busy ? 'Сохраняем…' : registering ? 'Создать кабинет' : 'Сохранить изменения'}</Button></div>
  </form>;
}
