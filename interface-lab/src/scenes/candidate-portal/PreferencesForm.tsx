import { useId, useState, type FormEvent } from 'react';
import { Plus, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import type { CandidatePreferences, MissingPreference } from './api';

export function PreferencesForm({ initial, missingFields, busy, onSubmit }: {
  initial: CandidatePreferences; missingFields?: MissingPreference[]; busy: boolean; onSubmit: (profile: Partial<CandidatePreferences>) => Promise<void>;
}) {
  const id = useId();
  const onboarding = missingFields !== undefined;
  const [draft, setDraft] = useState(() => ({ desiredRole: initial.desiredRole || '', salaryMin: initial.salaryMin == null ? '' : String(initial.salaryMin), city: initial.city || '', employmentType: initial.employmentType || '', workMode: initial.workMode || 'any', preferredCompanies: initial.preferredCompanies || [], anyCompany: initial.anyCompany === true }));
  const [adding, setAdding] = useState(false);
  const [company, setCompany] = useState('');
  const [validation, setValidation] = useState('');
  function addCompany(value: string) {
    const clean = value.trim();
    if (!clean) return;
    setDraft(previous => ({ ...previous, anyCompany: false, preferredCompanies: [...new Set([...previous.preferredCompanies, clean])].slice(0, 20) }));
    setCompany(''); setAdding(false); setValidation('');
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    const salaryMin = Number(draft.salaryMin);
    if (!Number.isSafeInteger(salaryMin) || salaryMin <= 0) { setValidation('Укажите минимальную зарплату в рублях.'); return; }
    if (!draft.anyCompany && !draft.preferredCompanies.length) { setValidation('Выберите компании или отметьте «Не важно».'); return; }
    await onSubmit({ ...draft, desiredRole: draft.desiredRole.trim(), city: draft.city.trim(), salaryMin, salaryMax: null, employmentType: draft.employmentType as CandidatePreferences['employmentType'], workMode: draft.workMode as CandidatePreferences['workMode'], ...(onboarding ? { resumeReviewed: true } : {}) });
  }
  return <form className="cp-preferences cp-preferences-v2" onSubmit={submit}>
    <fieldset disabled={busy}>
      <div className="cp-form-grid">
        <label className="cp-full" htmlFor={`${id}-role`}>Желаемая должность<Input id={`${id}-role`} required maxLength={200} value={draft.desiredRole} onChange={event => setDraft({ ...draft, desiredRole: event.target.value })}/></label>
        <label htmlFor={`${id}-salary`}>Зарплата от, ₽ в месяц<Input id={`${id}-salary`} required type="number" inputMode="numeric" min="1" step="1" value={draft.salaryMin} onChange={event => setDraft({ ...draft, salaryMin: event.target.value })}/></label>
        <label htmlFor={`${id}-employment`}>Занятость<select id={`${id}-employment`} required value={draft.employmentType} onChange={event => setDraft({ ...draft, employmentType: event.target.value as CandidatePreferences['employmentType'] })}><option value="">Выберите</option><option value="full-time">Полная занятость</option><option value="part-time">Частичная занятость</option><option value="project">Проектная работа</option><option value="internship">Стажировка</option></select></label>
        <label htmlFor={`${id}-city`}>Локация<Input id={`${id}-city`} required autoComplete="address-level2" maxLength={120} placeholder="Город" value={draft.city} onChange={event => setDraft({ ...draft, city: event.target.value })}/></label>
        <label htmlFor={`${id}-mode`}>Формат работы<select id={`${id}-mode`} value={draft.workMode} onChange={event => setDraft({ ...draft, workMode: event.target.value as CandidatePreferences['workMode'] || 'any' })}><option value="any">Не важно</option><option value="office">Офис</option><option value="hybrid">Гибрид</option><option value="remote">Удалённо</option></select></label>
      </div>
      <div className="cp-companies"><div className="cp-companies-heading"><span id={`${id}-companies`}>Предпочитаемые компании</span><label className="cp-check"><input type="checkbox" checked={draft.anyCompany} onChange={event => setDraft({ ...draft, anyCompany: event.target.checked, preferredCompanies: [] })}/><span>Не важно</span></label></div>
        <div className="cp-company-options" role="group" aria-labelledby={`${id}-companies`}>
          {[...new Set(['Яндекс', 'Сбер', 'Ozon', ...draft.preferredCompanies])].map(name => <button key={name} type="button" className="cp-company-option" aria-pressed={draft.preferredCompanies.includes(name)} disabled={draft.anyCompany} onClick={() => draft.preferredCompanies.includes(name) ? setDraft({ ...draft, preferredCompanies: draft.preferredCompanies.filter(item => item !== name) }) : addCompany(name)}>{name}{draft.preferredCompanies.includes(name) && <X size={13} aria-hidden="true"/>}</button>)}
          {!adding && <button type="button" className="cp-company-option cp-company-add" disabled={draft.anyCompany || draft.preferredCompanies.length >= 20} aria-label="Добавить компанию" onClick={() => setAdding(true)}><Plus size={18}/></button>}
        </div>
        {adding && <div className="cp-company-entry"><Input aria-label="Название компании" autoFocus maxLength={120} value={company} onChange={event => setCompany(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); addCompany(company); } if (event.key === 'Escape') setAdding(false); }}/><Button type="button" variant="ghost" disabled={!company.trim()} onClick={() => addCompany(company)}>Добавить</Button></div>}
      </div>
      {validation && <p role="alert" className="cp-error">{validation}</p>}
      <div className="cp-form-actions"><Button type="submit" disabled={busy}>{busy ? 'Сохраняем…' : onboarding ? 'Продолжить' : 'Сохранить изменения'}</Button></div>
    </fieldset>
  </form>;
}
