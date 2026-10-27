import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import { localDate, type CandidatePreferences, type MissingPreference } from './api';

type PreferenceDraft = { salaryMin: string; salaryMax: string; latestStartDate: string; consentReuse: boolean; autoApprove: boolean; about: string; city: string; residence: string; employmentType: CandidatePreferences['employmentType'] };
const preferenceDraft = (source: CandidatePreferences): PreferenceDraft => ({ salaryMin: source.salaryMin == null ? '' : String(source.salaryMin), salaryMax: source.salaryMax == null ? '' : String(source.salaryMax), latestStartDate: source.latestStartDate || '', consentReuse: source.consentReuse === true, autoApprove: source.autoApprove === true && source.consentReuse === true, about: source.about || '', city: source.city || '', residence: source.residence || '', employmentType: source.employmentType || '' });
const preferenceFields: MissingPreference[] = ['salaryMin', 'salaryMax', 'latestStartDate'];

export function PreferencesForm({ initial, missingFields, busy, onSubmit }: {
  initial: CandidatePreferences; missingFields?: MissingPreference[]; busy: boolean; onSubmit: (profile: Partial<CandidatePreferences>) => Promise<void>;
}) {
  const id = useId();
  const onboarding = missingFields !== undefined;
  const [editing, setEditing] = useState<MissingPreference[]>([]);
  const visible = missingFields ? [...new Set([...missingFields, ...editing])] : preferenceFields;
  const [draft, setDraft] = useState(() => preferenceDraft(initial));
  const [validation, setValidation] = useState('');
  const touched = useRef(new Set<keyof PreferenceDraft>());
  useEffect(() => {
    const incoming = preferenceDraft(initial);
    setDraft(previous => Object.fromEntries(Object.entries(incoming).map(([key, value]) => [key, touched.current.has(key as keyof PreferenceDraft) ? previous[key as keyof PreferenceDraft] : value])) as PreferenceDraft);
  }, [initial.salaryMin, initial.salaryMax, initial.latestStartDate, initial.consentReuse, initial.autoApprove, initial.about, initial.city, initial.residence, initial.employmentType]);
  const update = <K extends keyof PreferenceDraft>(key: K, value: PreferenceDraft[K]) => {
    touched.current.add(key); setDraft(previous => ({ ...previous, [key]: value })); setValidation('');
  };
  const canSummarizeMin = onboarding && !visible.includes('salaryMin') && initial.salaryMin != null;
  const canSummarizeMax = onboarding && !visible.includes('salaryMax') && initial.salaryMax != null;
  const canSummarizeDate = onboarding && !visible.includes('latestStartDate') && !!initial.latestStartDate;
  async function submit(event: FormEvent) {
    event.preventDefault();
    setValidation('');
    const isChanged = (key: keyof PreferenceDraft) => onboarding || touched.current.has(key);
    const patch: Partial<CandidatePreferences> = {};
    const min = draft.salaryMin ? Number(draft.salaryMin) : null;
    const max = draft.salaryMax ? Number(draft.salaryMax) : null;
    const salaryChanged = onboarding || touched.current.has('salaryMin') || touched.current.has('salaryMax');
    if (salaryChanged && ((onboarding && (min == null || max == null)) || (min != null && (!Number.isSafeInteger(min) || min <= 0)) || (max != null && (!Number.isSafeInteger(max) || max <= 0)))) { setValidation('Укажите желаемую зарплату от и до в рублях.'); return; }
    if (salaryChanged && min != null && max != null && min > max) { setValidation('Верхняя граница зарплаты должна быть не меньше нижней.'); return; }
    if (isChanged('latestStartDate') && ((onboarding && !draft.latestStartDate) || (draft.latestStartDate && (!/^\d{4}-\d{2}-\d{2}$/.test(draft.latestStartDate) || draft.latestStartDate < localDate())))) { setValidation('Укажите дату выхода на работу — сегодня или позже.'); return; }
    if (isChanged('about')) patch.about = draft.about.trim();
    if (isChanged('city')) patch.city = draft.city.trim();
    if (isChanged('residence')) patch.residence = draft.residence.trim();
    if (isChanged('employmentType')) patch.employmentType = draft.employmentType;
    if (visible.includes('salaryMin') && isChanged('salaryMin')) patch.salaryMin = min;
    if (visible.includes('salaryMax') && isChanged('salaryMax')) patch.salaryMax = max;
    if (visible.includes('latestStartDate') && isChanged('latestStartDate')) patch.latestStartDate = draft.latestStartDate;
    if (onboarding) patch.resumeReviewed = true;
    if (touched.current.has('autoApprove')) patch.autoApprove = draft.autoApprove && draft.consentReuse;
    if (!onboarding && touched.current.has('consentReuse')) patch.consentReuse = draft.consentReuse;
    await onSubmit(patch);
  }
  return <form className="cp-preferences" onSubmit={submit}>
    <div className="cp-form-grid">
      <label className="cp-full" htmlFor={`${id}-about`}>О себе<Textarea id={`${id}-about`} maxLength={1500} rows={3} value={draft.about} onChange={event => update('about', event.target.value)} /></label>
      <label htmlFor={`${id}-city`}>Город<Input id={`${id}-city`} autoComplete="address-level2" maxLength={120} value={draft.city} onChange={event => update('city', event.target.value)} /></label>
      <label htmlFor={`${id}-employment`}>Тип занятости<select id={`${id}-employment`} value={draft.employmentType} onChange={event => update('employmentType', event.target.value as CandidatePreferences['employmentType'])}><option value="">Выберите</option><option value="full-time">Полная занятость</option><option value="part-time">Частичная занятость</option><option value="project">Проектная работа</option><option value="internship">Стажировка</option></select></label>
      <label className="cp-full" htmlFor={`${id}-residence`}>Район или метро<Input id={`${id}-residence`} maxLength={200} aria-describedby={`${id}-residence-hint`} value={draft.residence} onChange={event => update('residence', event.target.value)} /><span className="cp-field-hint" id={`${id}-residence-hint`}>Чтобы понимать расстояние до работы</span></label>
      {(canSummarizeMin || canSummarizeMax || canSummarizeDate) && <div className="cp-extracted-preferences cp-full">
        {(canSummarizeMin || canSummarizeMax) && <div><div><span>{canSummarizeMin && canSummarizeMax ? 'Желаемая зарплата' : canSummarizeMin ? 'Желаемая зарплата от' : 'Желаемая зарплата до'}</span><strong>{canSummarizeMin ? initial.salaryMin!.toLocaleString('ru-RU') : ''}{canSummarizeMin && canSummarizeMax ? '–' : ''}{canSummarizeMax ? initial.salaryMax!.toLocaleString('ru-RU') : ''} ₽</strong></div><Button type="button" variant="ghost" size="sm" aria-label="Изменить желаемую зарплату" onClick={() => setEditing(previous => [...previous, ...(canSummarizeMin ? ['salaryMin' as const] : []), ...(canSummarizeMax ? ['salaryMax' as const] : [])])}>Изменить</Button></div>}
        {canSummarizeDate && <div><div><span>Выход на работу не позднее</span><strong>{new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }).format(new Date(`${initial.latestStartDate}T12:00:00`))}</strong></div><Button type="button" variant="ghost" size="sm" aria-label="Изменить дату выхода на работу" onClick={() => setEditing(previous => [...previous, 'latestStartDate'])}>Изменить</Button></div>}
      </div>}
      {visible.includes('salaryMin') && <label htmlFor={`${id}-salary-min`}>Желаемая зарплата от, ₽<Input id={`${id}-salary-min`} type="number" inputMode="numeric" min="1" step="1" required={onboarding} value={draft.salaryMin} onChange={event => update('salaryMin', event.target.value)} /></label>}
      {visible.includes('salaryMax') && <label htmlFor={`${id}-salary-max`}>Желаемая зарплата до, ₽<Input id={`${id}-salary-max`} type="number" inputMode="numeric" min="1" step="1" required={onboarding} value={draft.salaryMax} onChange={event => update('salaryMax', event.target.value)} /></label>}
      {visible.includes('latestStartDate') && <label className="cp-full" htmlFor={`${id}-latest-start`}>Выйду на работу не позднее<Input id={`${id}-latest-start`} type="date" min={onboarding ? localDate() : undefined} required={onboarding} value={draft.latestStartDate} onChange={event => update('latestStartDate', event.target.value)} /></label>}
    </div>
    <div className="cp-consent-box">
      {!onboarding && <label className="cp-check"><input type="checkbox" checked={draft.consentReuse} onChange={event => { update('consentReuse', event.target.checked); if (!event.target.checked) update('autoApprove', false); }}/><span>Согласен на запись интервью и использование его материалов для участия в подходящих вакансиях.</span></label>}
      <div className="cp-auto-option"><label className="cp-check"><input type="checkbox" role="switch" checked={draft.autoApprove} disabled={!draft.consentReuse} onChange={event => update('autoApprove', event.target.checked)}/><span><strong>Согласовывать вакансии автоматически</strong><small>По желанию. Время каждой встречи вы подтверждаете сами.</small></span></label></div>
    </div>
    {validation && <p role="alert" className="cp-error">{validation}</p>}
    <div className="cp-form-actions"><Button type="submit" disabled={busy}>{busy ? 'Сохраняем…' : onboarding ? 'Продолжить' : 'Сохранить изменения'}</Button></div>
  </form>;
}
