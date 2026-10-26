import { Fragment, useEffect, useRef, useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { selectionRequest, type SelectionVacancy } from './api';
import { OutreachError } from '../hh-outreach/api';
import { type ProfileEnvelope, type Importance, matchingLabels } from './matching-contract';
import { RequiredFieldsEditor } from './RequiredFieldsEditor';
import { RequirementDistribution } from './RequirementDistribution';
import { levelLabel } from './distribution-data';

export function VacancyRequirements({ vacancy, value, token, expire, onChange }: {
  vacancy: SelectionVacancy; value: ProfileEnvelope; token: string; expire: () => void; onChange: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [draft, setDraft] = useState(value.profile?.parameters ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef(false);
  const [editRevision, setEditRevision] = useState(value.revision);
  const [editProfileId, setEditProfileId] = useState(value.profile?.profile_id);
  useEffect(() => { if (!editing) setDraft(value.profile?.parameters ?? []); }, [value, editing]);
  async function command(save: boolean) {
    if (pending.current) return; pending.current = true; setBusy(true); setError('');
    try {
      const path = `vacancies/${encodeURIComponent(vacancy.id)}/profile`;
      await selectionRequest<ProfileEnvelope>(path + (save ? '/revisions' : ''), token,
        save ? { revision: editRevision, profileId: editProfileId, changes: draft.map(p => ({ parameter_id: p.parameter_id, required_level_id: p.required_level_id, importance: p.importance })) }
          : { revision: value.revision });
      setEditing(false); onChange();
    } catch (cause) { if (cause instanceof OutreachError && [401, 403].includes(cause.status)) expire(); else setError((cause as Error).message); }
    finally { pending.current = false; setBusy(false); }
  }
  const missingTarget = draft.some(p => typeof p.importance === 'number' && p.importance > 0 && !p.required_level_id);
  const requirementState = value.state === 'needs_review' && value.profile?.parameters.every(p => typeof p.importance === 'number')
    ? 'Готово к сравнению' : value.state === 'needs_review' ? 'Нужно уточнить важность навыков' : matchingLabels[value.state] ?? 'Статус неизвестен';
  return <details className="sl-requirements"><summary><h2 className="md-requirements-title">Требования вакансии</h2><span>{value.stale ? 'Нужно обновить' : requirementState}</span></summary>
    <div className="sl-requirements-body">
      {value.stale && <p role="status">Вакансия изменилась. Требования и сравнение нужно обновить.</p>}
      {!value.profile && <p className="sl-muted">Требования к навыкам ещё не рассчитаны.</p>}
      {value.profile && !editing && <><div className="sl-scroll"><table className="md-requirement-table" aria-label="Требования к навыкам"><thead><tr><th>Навык</th><th>Требуемый уровень</th><th>Важность</th></tr></thead><tbody>{value.profile.parameters.map(p => <Fragment key={p.parameter_id}><tr className="md-requirement-row" data-open={expanded === p.parameter_id}><td><button className="md-skill-toggle" aria-expanded={expanded === p.parameter_id} aria-controls={expanded === p.parameter_id ? `requirement-${p.parameter_id}` : undefined} onClick={() => setExpanded(current => current === p.parameter_id ? null : p.parameter_id)}><ChevronRight size={16}/>{p.title}</button></td><td data-label="Требуемый уровень">{levelLabel(p)}</td><td data-label="Важность">{importanceLabel(p.importance)}</td></tr>{expanded === p.parameter_id && <tr><td colSpan={3}><div id={`requirement-${p.parameter_id}`}><RequirementDistribution parameter={p}/></div></td></tr>}</Fragment>)}</tbody></table></div><button className="sl-open" disabled={busy || value.stale} onClick={() => { setDraft(value.profile!.parameters); setEditRevision(value.revision); setEditProfileId(value.profile!.profile_id); setEditing(true); }}>Изменить требования</button></>}
      {editing && <form onSubmit={e => { e.preventDefault(); void command(true); }}>
        <div className="sl-scroll"><table aria-label="Редактор требований"><thead><tr><th>Навык</th><th>Требуемый уровень</th><th>Важность</th></tr></thead><tbody>{draft.map((p, i) => <tr key={p.parameter_id}><td>{p.title}</td><td><label><span className="sl-sr">Уровень: {p.title}</span><select disabled={busy} value={p.required_level_id ?? ''} onChange={e => setDraft(d => d.map((v, j) => i === j ? { ...v, required_level_id: e.target.value || null } : v))}><option value="">Не определён</option>{p.levels.map(l => <option key={l.level_id} value={l.level_id}>{l.label}</option>)}</select></label></td><td><label><span className="sl-sr">Важность: {p.title}</span><select disabled={busy} value={p.importance} onChange={e => setDraft(d => d.map((v, j) => i === j ? { ...v, importance: e.target.value === 'insufficient_data' ? 'insufficient_data' : Number(e.target.value) as Importance } : v))}><option value="insufficient_data">Не определена</option>{([0, 1, 2, 3] as const).map(w => <option key={w} value={w}>{importanceLabel(w)}</option>)}</select></label></td></tr>)}</tbody></table></div>
        {missingTarget && <p role="status">Без целевого уровня сравнение учитывает навык по правилу «чем выше уровень, тем выше балл».</p>}
        <div className="sl-form-actions"><button type="button" disabled={busy} onClick={() => setEditing(false)}>Отмена</button><button className="sl-primary" disabled={busy || value.revision !== editRevision || value.profile?.profile_id !== editProfileId}>{busy ? 'Сохраняем…' : 'Сохранить требования'}</button></div>
      </form>}
      {!editing && value.canRun && <button className="sl-open" disabled={busy || ['queued', 'sent', 'response_saved'].includes(value.state)} onClick={() => void command(false)}>{busy ? 'Запускаем…' : value.profile ? 'Обновить требования из вакансии' : 'Определить требования'}</button>}
      {error && <p className="sl-error" role="alert">{error}</p>}
      {value.profile && <details className="sl-version"><summary>Версия требований</summary><p>Шкала: {value.profile.rubric_version}</p><p className="sl-hash">{value.profile.profile_id}</p><p>Ручная корректировка сохраняется отдельно от исходного ответа модели.</p></details>}
      <RequiredFieldsEditor kind="vacancy" id={vacancy.id} token={token} expire={expire} onSaved={onChange}/>
    </div>
  </details>;
}
const importanceLabel = (v: Importance) => ({ 0: '0 · Не требуется', 1: '1 · Желательно', 2: '2 · Важно', 3: '3 · Очень важно', insufficient_data: 'Нужно уточнить' })[v] ?? 'Нужно уточнить';
