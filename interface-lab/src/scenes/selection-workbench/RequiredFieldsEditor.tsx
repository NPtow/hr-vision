import { useEffect, useRef, useState } from 'react';
import { selectionRequest } from './api';
import { OutreachError } from '../hh-outreach/api';
import type { CandidateFields, CandidateFieldsResponse, FieldOptions, RequiredFieldsResponse, VacancyFields, FieldEvidence } from './matching-contract';
import { formatIds, vacancyConditionLabel, vacancyIndustryLabel, candidateIndustryLabel, factPathLabels, conflictValueLabel, withResolvedFact, factsUpdateBody } from './required-fields-values';

type Response = RequiredFieldsResponse | CandidateFieldsResponse;
const emptyOptions: FieldOptions = { locations: [], employmentTypes: [], industries: [] };
const fieldTitle = { location: 'География работы', employment: 'Формат занятости', industry_experience: 'Опыт в отрасли' };

/** Structured facts only. A missing option catalog never becomes guessed IDs from free text. */
export function RequiredFieldsEditor({ kind, id, token, expire, onSaved }: {
  kind: 'vacancy' | 'candidate'; id: string; token: string; expire: () => void; onSaved?: () => void;
}) {
  const [open, setOpen] = useState(false), [editing, setEditing] = useState(false);
  const [value, setValue] = useState<Response | null>(null), [draft, setDraft] = useState<VacancyFields | CandidateFields | null>(null);
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(false), [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [newIndustry, setNewIndustry] = useState<string | null>(null), [newMonths, setNewMonths] = useState('');
  const [resolvedPaths, setResolvedPaths] = useState<string[]>([]);
  const pending = useRef(false);
  const path = `${kind === 'vacancy' ? 'vacancies' : 'candidates'}/${encodeURIComponent(id)}/required-fields`;
  useEffect(() => {
    if (!open) return; let active = true; setLoading(true); setError('');
    void selectionRequest<Response>(path, token).then(result => {
      if (!active) return;
      if (('vacancyId' in result ? result.vacancyId : result.candidateId) !== id) throw new Error('Ответ относится к другой карточке.');
      setValue(result); setDraft(result.fields); setResolvedPaths([]);
    }).catch(cause => { if (!active) return; if (cause instanceof OutreachError && [401, 403].includes(cause.status)) expire(); else setError((cause as Error).message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [open, id, path, token, expire, retry]);
  async function save() {
    if (!value || !draft || pending.current) return; pending.current = true; setBusy(true); setError('');
    try {
      const result = await selectionRequest<Response>(path, token, factsUpdateBody(value.revision, draft, resolvedPaths));
      if (('vacancyId' in result ? result.vacancyId : result.candidateId) !== id) throw new Error('Ответ относится к другой карточке.');
      setValue(result); setDraft(result.fields); setEditing(false); setResolvedPaths([]); onSaved?.();
    } catch (cause) { if (cause instanceof OutreachError && [401, 403].includes(cause.status)) expire(); else setError((cause as Error).message); }
    finally { pending.current = false; setBusy(false); }
  }
  const options = value?.options ?? emptyOptions;
  const fields = editing ? draft : value?.fields;
  const vacancyFields = kind === 'vacancy' ? fields as VacancyFields | null : null;
  const candidateFields = kind === 'candidate' ? fields as CandidateFields | null : null;
  function resolveConflict(path: string, choice: unknown) {
    if (busy || !value?.conflicts || !Object.hasOwn(value.conflicts, path)) return;
    try {
      setDraft(withResolvedFact(editing && draft ? draft : value.fields, path, choice));
      setResolvedPaths(paths => [...new Set([...paths, path])]); setEditing(true); setError('');
    } catch (cause) { setError((cause as Error).message); }
  }
  return <details className="sl-facts" open={open} onToggle={e => setOpen(e.currentTarget.open)}><summary>{kind === 'vacancy' ? 'Обязательные условия' : 'Условия поиска работы'}</summary>
    {loading ? <p role="status">Загружаем условия…</p> : <>
      {fields && <form onSubmit={e => { e.preventDefault(); void save(); }}>
        {vacancyFields && <div className="sl-fact-grid">{(['location', 'employment'] as const).map(key => <div key={key}><h4>{fieldTitle[key]}</h4>
          {editing ? <><RequiredChoice label={`${fieldTitle[key]}: обязательность`} disabled={busy} value={vacancyFields[key].required} onChange={required => setDraft({ ...vacancyFields, [key]: { ...vacancyFields[key], required } })}/>
            <MultiChoice label={fieldTitle[key]} value={vacancyFields[key].allowed_ids} options={key === 'location' ? options.locations : options.employmentTypes} disabled={busy} onChange={allowed_ids => setDraft({ ...vacancyFields, [key]: { ...vacancyFields[key], allowed_ids } })}/></>
            : <p>{vacancyConditionLabel(vacancyFields[key], key === 'location' ? options.locations : options.employmentTypes)}</p>}</div>)}
          <div><h4>Опыт в отрасли</h4>{editing ? <><RequiredChoice label="Опыт в отрасли: обязательность" disabled={busy} value={vacancyFields.industry_experience.required} onChange={required => setDraft({ ...vacancyFields, industry_experience: { ...vacancyFields.industry_experience, required } })}/>
            <IndustryChoice value={vacancyFields.industry_experience.industry_id} options={options.industries} disabled={busy} onChange={industry_id => setDraft({ ...vacancyFields, industry_experience: { ...vacancyFields.industry_experience, industry_id } })}/>
              <label>Минимум месяцев<input type="number" min="0" step="1" disabled={busy} value={vacancyFields.industry_experience.minimum_months ?? ''} onChange={e => setDraft({ ...vacancyFields, industry_experience: { ...vacancyFields.industry_experience, minimum_months: e.target.value === '' ? null : Number(e.target.value) } })}/></label></>
              : <p>{vacancyIndustryLabel(vacancyFields.industry_experience, options.industries)}</p>}</div>
        </div>}
        {candidateFields && <div className="sl-fact-grid"><div><h4>Где готов работать</h4>{editing ? <MultiChoice label="География поиска" value={candidateFields.work_locations} options={options.locations} disabled={busy} onChange={work_locations => setDraft({ ...candidateFields, work_locations })}/> : <p>{formatIds(candidateFields.work_locations, options.locations)}</p>}</div>
          <div><h4>Формат занятости</h4>{editing ? <MultiChoice label="Занятость кандидата" value={candidateFields.employment_types} options={options.employmentTypes} disabled={busy} onChange={employment_types => setDraft({ ...candidateFields, employment_types })}/> : <p>{formatIds(candidateFields.employment_types, options.employmentTypes)}</p>}</div>
          <div><h4>Опыт в отраслях</h4>{!editing || !Object.keys(candidateFields.industry_months ?? {}).length ? <p>{candidateIndustryLabel(candidateFields.industry_months, options.industries)}</p> : Object.entries(candidateFields.industry_months ?? {}).map(([industry, months]) => <label key={industry}>{formatIds([industry], options.industries)}<input type="number" min="0" step="1" disabled={busy} aria-label={`Месяцев: ${formatIds([industry], options.industries)}`} value={months} onChange={e => { if (e.target.value !== '') setDraft({ ...candidateFields, industry_months: { ...candidateFields.industry_months, [industry]: Number(e.target.value) } }); }}/></label>)}
            {editing && <><IndustryChoice value={newIndustry} options={options.industries.filter(o => !(o.id in (candidateFields.industry_months ?? {})))} disabled={busy} label="Добавить отрасль" onChange={setNewIndustry}/>{newIndustry && <div><label>Подтверждённый стаж, месяцев<input type="number" min="0" max="1200" step="1" value={newMonths} disabled={busy} onChange={e => setNewMonths(e.target.value)}/></label><button type="button" disabled={busy || newMonths === '' || !Number.isInteger(Number(newMonths)) || Number(newMonths) < 0 || Number(newMonths) > 1200} onClick={() => { setDraft({ ...candidateFields, industry_months: { ...candidateFields.industry_months, [newIndustry]: Number(newMonths) } }); setNewIndustry(null); setNewMonths(''); }}>Добавить опыт</button></div>}<button type="button" className="sl-open" disabled={busy} onClick={() => setDraft({ ...candidateFields, industry_months: null })}>Опыт не уточнён</button></>}
          </div></div>}
        <FieldConflicts evidence={value ?? {}} options={options} resolvedPaths={resolvedPaths} onResolve={resolveConflict} disabled={busy}/>
        {editing ? <div className="sl-form-actions"><button type="button" disabled={busy} onClick={() => { setEditing(false); setDraft(value!.fields); setNewIndustry(null); setNewMonths(''); setResolvedPaths([]); }}>Отмена</button><button className="sl-primary" disabled={busy || !!newIndustry}>{busy ? 'Сохраняем…' : 'Сохранить условия'}</button></div>
          : <button type="button" className="sl-open" onClick={() => setEditing(true)}>Изменить условия</button>}
      </form>}
    </>}
    {error && <><p className="sl-error" role="alert">{error}</p>{!editing && <button onClick={() => setRetry(v => v + 1)}>Повторить</button>}</>}
  </details>;
}
export function FieldConflicts({ evidence, options, resolvedPaths = [], onResolve, disabled = false }: { evidence: FieldEvidence; options: FieldOptions; resolvedPaths?: string[]; onResolve?: (path: string, choice: unknown) => void; disabled?: boolean }) {
  return <>{Object.entries(evidence.conflicts ?? {}).map(([path, conflict]) => <div className="sl-fact-conflict" role="status" key={path}>
    <h4>{factPathLabels[path] ?? 'Условие'}</h4><p>Ответ изменился — проверьте условие.</p>
    <dl><dt>Ваше уточнение</dt><dd>{conflictValueLabel(path, conflict.manualValue, options)}</dd>
      <dt>Новые данные</dt><dd>{conflictValueLabel(path, conflict.sourceValue, options)}</dd></dl>
    {onResolve && <div className="sl-conflict-actions"><button type="button" disabled={disabled} onClick={() => onResolve(path, conflict.sourceValue)}>Принять новые данные</button>
      <button type="button" disabled={disabled} onClick={() => onResolve(path, conflict.manualValue)}>Оставить моё уточнение</button>
      <button type="button" disabled={disabled} onClick={() => onResolve(path, null)}>Оставить неуточнённым</button></div>}
    {resolvedPaths.includes(path) && <p>Уточнение выбрано. Сохраните условия.</p>}
  </div>)}</>;
}
function RequiredChoice({ label, value, disabled, onChange }: { label: string; value: boolean | null; disabled: boolean; onChange: (v: boolean | null) => void }) {
  return <label><span className="sl-sr">{label}</span><select disabled={disabled} value={value === null ? 'unknown' : value ? 'yes' : 'no'} onChange={e => onChange(e.target.value === 'unknown' ? null : e.target.value === 'yes')}><option value="unknown">Не уточнено</option><option value="yes">Обязательно</option><option value="no">Не требуется</option></select></label>;
}
function MultiChoice({ label, value, options, disabled, onChange }: { label: string; value: string[] | null; options: { id: string; label: string }[]; disabled: boolean; onChange: (v: string[] | null) => void }) {
  const all = [...options, ...(value ?? []).filter(id => !options.some(o => o.id === id)).map(id => ({ id, label: id }))];
  return <div><label>{label}<select aria-label={label} multiple disabled={disabled || !all.length} value={value ?? []} onChange={e => onChange([...e.target.selectedOptions].map(o => o.value))}>{all.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}</select></label>{!all.length && <small>Варианты пока не загружены.</small>}<label className="sl-check-label"><input type="checkbox" disabled={disabled} checked={value === null} onChange={e => onChange(e.target.checked ? null : [])}/>Не уточнено</label></div>;
}
function IndustryChoice({ label = 'Отрасль', value, options, disabled, onChange }: { label?: string; value: string | null; options: { id: string; label: string }[]; disabled: boolean; onChange: (v: string | null) => void }) {
  return <label>{label}<select aria-label={label} value={value ?? ''} disabled={disabled || !options.length && !value} onChange={e => onChange(e.target.value || null)}><option value="">Не выбрана</option>{value && !options.some(o => o.id === value) && <option value={value}>{value}</option>}{options.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}</select>{!options.length && <small>Варианты пока не загружены.</small>}</label>;
}
