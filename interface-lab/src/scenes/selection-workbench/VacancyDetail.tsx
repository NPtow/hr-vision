import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Search, Send } from 'lucide-react';
import { OutreachError } from '../hh-outreach/api';
import { selectionRequest, type SelectionPerson, type SelectionVacancy } from './api';
import { loadCandidateDirectory, recommendationPoolLimitMessage, MAX_RECOMMENDATION_CANDIDATES } from './directory';
import { hasHRInterview, scoreLabel, matchingLabels, type ProfileEnvelope, type RecommendationsEnvelope, type PublishReceipt, type RecommendationItem } from './matching-contract';
import { VacancyRequirements } from './VacancyRequirements';
import { MatchExplanation } from './MatchExplanation';
import { AssessmentEntry } from './review-state';
const gateReasons: Record<string, string> = { ready: '', unknown_requires_approval: 'Нужно ваше подтверждение', already_added: 'Приглашение уже создано', no_delivery_channel: 'Нет подключённых каналов', not_eligible: 'Пока нельзя пригласить', unknown_conditions: 'Подтвердите неизвестные условия', unknown_required_fields: 'Подтвердите неизвестные условия', needs_approval: 'Подтвердите неизвестные условия', no_channels: 'Нет подключённых каналов', no_contact: 'Нет подключённых каналов', already_invited: 'Приглашение уже создано', stale: 'Обновите сравнение', not_searching: 'Поиск работы остановлен', missing_assessment: 'Ожидает оценки' };
const channelLabel = (channels: string[]) => channels.map(c => c === 'email' ? 'Почта' : 'Telegram').join(' + ');

export function VacancyDetail({ vacancy, people, token, expire, onBack, onCandidate, candidateInputRevision = 0 }: { vacancy: SelectionVacancy; people: SelectionPerson[]; token: string; expire: () => void; onBack: () => void; onCandidate: (id: string) => void; candidateInputRevision?: number }) {
  const [vacancyPeople, setVacancyPeople] = useState(people);
  const [profile, setProfile] = useState<ProfileEnvelope | null>(null), [data, setData] = useState<RecommendationsEnvelope | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [directoryUnavailable, setDirectoryUnavailable] = useState(true);
  const [search, setSearch] = useState(''), [selected, setSelected] = useState(new Set<string>()), [approved, setApproved] = useState(new Set<string>());
  const [expanded, setExpanded] = useState('');
  const [verifiedCandidateRevision, setVerifiedCandidateRevision] = useState(candidateInputRevision);
  const candidateInputsChanged = verifiedCandidateRevision !== candidateInputRevision;
  const generation = useRef(0), pending = useRef(false);
  const command = useRef<{ key: string; fingerprint: string } | null>(null);
  const path = `vacancies/${encodeURIComponent(vacancy.id)}`;
  const inFlight = useRef<{ generation: number; promise: Promise<void> } | null>(null);
  const failures = useRef(0), unauthorized = useRef(false), pollingState = useRef('not_started');
  const load = useCallback(async (background = false) => {
    const seq = generation.current;
    // All reads settle before another batch begins, including partial HTTP failure.
    while (inFlight.current) {
      const previous = inFlight.current;
      await previous.promise;
      if (seq !== generation.current || unauthorized.current) return;
      if (previous.generation === seq) return;
    }
    if (unauthorized.current) return;
    const request = (async () => {
      setRefreshing(true); if (!background) setLoading(true);
      try {
        const values = await Promise.allSettled([
          selectionRequest<ProfileEnvelope>(path + '/profile', token),
          selectionRequest<RecommendationsEnvelope>(path + '/recommendations', token),
          loadCandidateDirectory(token, vacancy.id),
        ]);
        if (seq !== generation.current) return;
        const denied = values.find(value => value.status === 'rejected' && value.reason instanceof OutreachError && [401, 403].includes(value.reason.status));
        if (denied?.status === 'rejected') throw denied.reason;
        const [pValue, rValue, directoryValue] = values;
        if (pValue.status === 'rejected') throw pValue.reason;
        if (rValue.status === 'rejected') throw rValue.reason;
        if (directoryValue.status === 'rejected') throw directoryValue.reason;
        const p = pValue.value, r = rValue.value, directory = directoryValue.value;
        if (p.vacancyId !== vacancy.id || r.vacancyId !== vacancy.id || p.profile && (p.profile.vacancy_id !== vacancy.id || p.profile.contract_version !== 'vacancy-level-importance-v1') || r.result && (r.result.vacancy_id !== vacancy.id || r.result.contract_version !== 'recommendations-v1' || r.result.recommendation_id !== r.recommendationId)) throw new Error('Не удалось подтвердить результат для этой вакансии.');
        failures.current = 0; pollingState.current = r.automation?.state ?? r.state; setError('');
        setProfile(p); setData(r); setVacancyPeople(directory.candidates); setDirectoryUnavailable(false); setVerifiedCandidateRevision(candidateInputRevision);
      } catch (cause) {
        if (seq !== generation.current) return;
        failures.current++;
        if (cause instanceof OutreachError && [401, 403].includes(cause.status)) { unauthorized.current = true; expire(); }
        else { setDirectoryUnavailable(true); setError((cause as Error).message); }
      } finally {
        if (seq === generation.current) { setLoading(false); setRefreshing(false); }
      }
    })();
    inFlight.current = { generation: seq, promise: request };
    try { await request; } finally { if (inFlight.current?.promise === request) inFlight.current = null; }
  }, [path, token, expire, vacancy.id, candidateInputRevision]);
  useEffect(() => {
    let active = true, polling = false, timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = async () => {
      if (!active || polling || document.hidden || unauthorized.current) return;
      polling = true;
      try { await load(true); } finally { polling = false; }
      if (!active || document.hidden || unauthorized.current) return;
      const delay = failures.current ? Math.min(60000, 3000 * 2 ** Math.min(failures.current, 5))
        : pollingState.current === 'ready' ? 15000 : 3000;
      timer = setTimeout(() => void schedule(), delay);
    };
    const visibility = () => { clearTimeout(timer); if (!document.hidden) void schedule(); };
    document.addEventListener('visibilitychange', visibility);
    void schedule();
    return () => { active = false; clearTimeout(timer); generation.current++; document.removeEventListener('visibilitychange', visibility); };
  }, [load]);
  useEffect(() => {
    setSelected(new Set()); setApproved(new Set()); setExpanded(''); command.current = null;
  }, [candidateInputRevision]);
  useEffect(() => {
    const byId = new Map(people.map(person => [person.id, person]));
    // Keep vacancy-specific workflow and delivery data from its own directory.
    setVacancyPeople(current => current.map(person => {
      const updated = byId.get(person.id);
      return updated ? { ...person, name: updated.name, city: updated.city, desiredRole: updated.desiredRole, about: updated.about, revision: updated.revision } : person;
    }));
  }, [people]);
  const profileUsable = !!profile?.profile && ['ready', 'completed', 'needs_review'].includes(profile.state)
    && profile.profile.parameters.every(parameter => typeof parameter.importance === 'number');
  const validResult = !candidateInputsChanged && !!(data?.result && profile?.profile && profileUsable && !data.stale && !profile.stale && ['ready', 'completed'].includes(data.state) && data.revision === profile.revision && profile.revision === vacancy.contentHash && data.result.vacancy_profile_id === profile.profile.profile_id && data.result.rubric_sha256 === profile.profile.rubric_sha256);
  useEffect(() => { setSelected(new Set()); setApproved(new Set()); setExpanded(''); command.current = null; }, [data?.recommendationId, data?.revision, profile?.profile?.profile_id, validResult]);
  const poolCount = vacancyPeople.filter(hasHRInterview).length;
  const poolError = poolCount > MAX_RECOMMENDATION_CANDIDATES ? recommendationPoolLimitMessage(poolCount) : '';
  const rows = useMemo(() => {
    const eligible = vacancyPeople.filter(hasHRInterview), byId = new Map(eligible.map(p => [p.id, p]));
    const items = data?.result?.items ?? [], seen = new Set(items.map(i => i.candidate_id));
    const matched = items.filter(i => i.status !== 'filtered' && i.eligibility !== 'fail' && byId.has(i.candidate_id));
    // The saved result owns the order. Never compute a substitute score or rank.
    const ordered = [...matched.filter(i => i.status === 'scored'), ...matched.filter(i => i.status !== 'scored')];
    return [...ordered.map(item => ({ person: byId.get(item.candidate_id)!, item: item as RecommendationItem | null })), ...eligible.filter(p => !seen.has(p.id)).map(person => ({ person, item: null as RecommendationItem | null }))]
      .filter(({ person: p }) => [p.name, p.desiredRole, p.city].join(' ').toLocaleLowerCase('ru').includes(search.toLocaleLowerCase('ru')));
  }, [vacancyPeople, data, search]);
  const gate = (id: string) => data?.invitationEligibility.find(v => v.candidateId === id);
  const selectable = (id: string, item: RecommendationItem | null) => {
    const g = gate(id);
    return validResult && !directoryUnavailable && !loading && !error && !!item && item.status === 'scored' && item.eligibility !== 'fail' && !!g?.canInvite && g.channels.length > 0 && (item.eligibility !== 'unknown' || approved.has(id));
  };
  const selectableIds = rows.filter(({ person, item }) => selectable(person.id, item)).map(r => r.person.id);
  const chosen = rows.filter(({ person, item }) => selected.has(person.id) && selectable(person.id, item));
  const toggle = (id: string) => setSelected(current => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  async function publish() {
    if (pending.current || loading || directoryUnavailable || !data || !profile || !validResult || !chosen.length) return;
    pending.current = true; setBusy('Отправляем…'); setError(''); setNotice('');
    try {
        const ids = chosen.map(r => r.person.id).sort(), unknownApprovals = chosen.filter(r => r.item?.eligibility === 'unknown').map(r => r.person.id).sort();
        const fingerprint = JSON.stringify([vacancy.id, data.revision, data.recommendationId, ids, unknownApprovals]);
        if (command.current?.fingerprint !== fingerprint) command.current = { key: crypto.randomUUID(), fingerprint };
        const receipt = await selectionRequest<PublishReceipt>('publish', token, { vacancyId: vacancy.id, candidateIds: ids, requestId: command.current.key, recommendationId: data.recommendationId, revision: data.revision, unknownApprovals });
        setSelected(new Set()); setApproved(new Set()); command.current = null;
        setNotice(`Заявки приняты: ${receipt.added.length}. На проверке перед передачей: ${receipt.pendingVerification?.length ?? 0}. Профиль появится у работодателя после согласия кандидата. Доставка выполняется отдельно.`);
      await load();
    } catch (cause) { if (cause instanceof OutreachError && [401, 403].includes(cause.status)) expire(); else setError((cause as Error).message); }
    // Preserve the exact request ID on an ambiguous response; retries are idempotent.
    finally { pending.current = false; setBusy(''); }
  }
  async function retryNomination(id: string) {
    if (pending.current || !validResult || !data || error) return;
    pending.current = true; setBusy('Повторяем проверку…'); setError('');
    try {
      await selectionRequest(`nominations/${encodeURIComponent(id)}/retry`, token, { revision: data.revision, recommendationId: data.recommendationId });
      setNotice('Повторная проверка запрошена. Доставка ещё не подтверждена.'); await load();
    } catch (cause) { if (cause instanceof OutreachError && [401, 403].includes(cause.status)) expire(); else setError((cause as Error).message); }
    finally { pending.current = false; setBusy(''); }
  }
  const automaticState = data?.automation?.state ?? data?.state ?? profile?.state ?? 'not_started';
  const automaticLabel = data?.automation?.errorCode === 'importance_required' ? 'Уточните важность навыков в требованиях вакансии' : matchingLabels[automaticState] ?? 'Проверяем соответствие';
  const retryAt = data?.automation?.retryAt;
  const retryDate = typeof retryAt === 'number' && Number.isFinite(retryAt) && retryAt > 0 ? new Date(retryAt * 1000) : null;
  const retryLabel = automaticState === 'backoff' && retryDate && !Number.isNaN(retryDate.getTime())
    ? new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' }).format(retryDate) : null;
  const stale = candidateInputsChanged || data?.stale || profile?.stale || !!data?.result && !validResult;
  return <section className="sl-vacancy-detail" aria-label={`Вакансия ${vacancy.title}`}>
    <button className="sl-back" onClick={onBack} disabled={!!busy}><ArrowLeft size={17}/>Все вакансии</button>
    <div className="sl-vacancy-heading"><div><p>{vacancy.company}</p><h1>{vacancy.title}</h1><p>{[vacancy.city, vacancy.salary].filter(Boolean).join(' · ')}</p></div></div>
    {profile && <VacancyRequirements vacancy={vacancy} value={profile} token={token} expire={expire} onChange={() => void load()}/>}
    <div className="sl-surface" aria-busy={loading || !!busy}><div className="sl-toolbar"><label className="sl-search"><Search size={17}/><span className="sl-sr">Найти кандидата в вакансии</span><input placeholder="Найти кандидата" value={search} onChange={e => { setSearch(e.target.value); setSelected(new Set()); }}/></label>
      <button className="sl-send" disabled={!chosen.length || !!busy || loading || !!error} onClick={() => void publish()}><Send size={16}/>{busy || `Отправить приглашения${chosen.length ? ' · ' + chosen.length : ''}`}</button><button className="sl-refresh" disabled={loading || refreshing || !!busy} onClick={() => void load(true)}>Обновить</button></div>
      <p className="sl-muted" role="status">Сравнение обновляется автоматически. {automaticLabel}.{retryLabel && ` Следующая попытка: ${retryLabel}.`}{error && ' Повторим проверку после временной ошибки.'}</p>
      {error && <p className="sl-error" role="alert">{error}</p>}{!directoryUnavailable && poolError && <p className="sl-error" role="alert">{poolError}</p>}{notice && <p className="sl-notice" role="status">{notice}</p>}{stale && <p className="sl-warning" role="status">Данные изменились. Соответствие пересчитывается автоматически. Прежние баллы не показаны как актуальные.</p>}
      {loading ? <div className="sl-empty" role="status">Проверяем требования и соответствие…</div> : directoryUnavailable ? <div className="sl-empty">Каталог не загружен. Нажмите «Обновить», чтобы повторить загрузку целиком.</div> : rows.length ? <><div className="sl-scroll"><table className="sl-recommendations"><thead><tr><th className="sl-check"><input type="checkbox" aria-label="Выбрать доступных кандидатов" disabled={!!busy || !selectableIds.length} checked={!!selectableIds.length && selectableIds.every(id => selected.has(id))} onChange={e => setSelected(e.target.checked ? new Set(selectableIds) : new Set())}/></th><th>Кандидат</th><th>Оценка навыков</th><th>Соответствие</th><th>Условия</th><th>Приглашение</th><th/></tr></thead><tbody>{rows.map(({ person: p, item }) => {
        const g = gate(p.id), canSelect = selectable(p.id, item), pendingState = data?.pendingCandidates.find(c => c.candidateId === p.id)?.state;
        const label = item ? item.status === 'scored' ? scoreLabel(item) : 'Недостаточно данных' : pendingState ? matchingLabels[pendingState] ?? 'Ожидает оценки' : automaticLabel;
        return <Fragment key={p.id}><tr className="md-recommendation-row" data-selected={selected.has(p.id) && canSelect}>
          <td className="sl-check"><input type="checkbox" aria-label={`Пригласить: ${p.name}`} disabled={!canSelect || !!busy} checked={selected.has(p.id) && canSelect} onChange={() => toggle(p.id)}/></td>
          <td><button className="sl-person" onClick={() => onCandidate(p.id)}>{p.name || 'Без имени'}</button><small>{p.desiredRole}</small></td><td><AssessmentEntry person={p} onOpen={() => onCandidate(p.id)}/></td><td>{error ? 'Не удалось проверить' : stale ? 'Нужно обновить' : <><strong>{label}</strong>{item?.status === 'scored' && item.rank !== null && <small>Место {item.rank} · навыков учтено: {item.coverage.included_parameters} из {item.coverage.total_parameters}</small>}</>}</td>
          <td>{item?.eligibility === 'unknown' ? <><span className="sl-status">Нужно уточнить</span><label className="sl-check-label"><input type="checkbox" aria-label={`Подтвердить неизвестные условия: ${p.name}`} disabled={!validResult || !!busy || !!error} checked={approved.has(p.id)} onChange={e => { setApproved(v => { const next = new Set(v); e.target.checked ? next.add(p.id) : next.delete(p.id); return next; }); setSelected(v => { const next = new Set(v); next.delete(p.id); return next; }); }}/>Пригласить с неуточнёнными условиями</label></> : item?.eligibility === 'pass' ? <span className="sl-status sl-green">Подтверждены</span> : 'Не проверены'}</td>
          <td>{g?.channels.length ? channelLabel(g.channels) : 'Нет доступных каналов'}{p.nomination && <div className="sl-nomination"><small>{({ waiting_profile: 'Ждёт входа в кабинет', waiting_consent: 'Ждёт согласия кандидата', pending_verification: 'Проверяется перед передачей', verification_failed: 'Проверка не завершена', blocked: 'Передача остановлена: данные изменились', proposed: 'Передано работодателю' } as Record<string, string>)[p.nomination.state] ?? 'Статус передачи неизвестен'}</small>{p.nomination.error && <small>Не удалось подтвердить передачу.{p.nomination.attempts ? ` Попыток: ${p.nomination.attempts}.` : ''}</small>}{p.nomination.state === 'verification_failed' && <button className="sl-open" disabled={!validResult || !!error || !!busy} onClick={() => void retryNomination(p.nomination!.id)}>Повторить проверку</button>}</div>}{g?.reason && gateReasons[g.reason] !== '' && <small>{gateReasons[g.reason] ?? 'Нужно проверить возможность приглашения'}</small>}{p.deliveries.length > 0 && <small>{p.deliveries.map(d => `${d.channel === 'email' ? 'Почта' : 'Telegram'}: ${d.status === 'delivered' ? 'принято сервисом' : d.status === 'pending' ? 'в очереди' : d.status === 'error' ? 'ошибка' : d.status}`).join(' · ')}</small>}</td>
          <td>{item && validResult && !error && <button className="sl-open" aria-expanded={expanded === p.id} aria-controls={expanded === p.id ? `match-${p.id}` : undefined} onClick={() => setExpanded(v => v === p.id ? '' : p.id)}>Почему такой балл</button>}</td>
        </tr>{expanded === p.id && item && data?.result && profile?.profile && validResult && !error && <tr className="md-recommendation-explanation"><td colSpan={7}><div id={`match-${p.id}`}><MatchExplanation item={item} result={data.result} profile={profile.profile}/></div></td></tr>}</Fragment>;
      })}</tbody></table></div><footer className="sl-table-footer"><span>{rows.length} кандидатов с готовым HR-интервью</span><span>Отправка по доступным каналам. Передача профиля — после согласия.</span></footer></> : <div className="sl-empty"><h3>{search.trim() ? 'По вашему запросу никто не найден' : 'Нет кандидатов по условиям вакансии'}</h3>{search.trim() ? <button onClick={() => setSearch('')}>Сбросить поиск</button> : <p>В список входят кандидаты с записью и готовым текстом HR-интервью.</p>}</div>}
    </div>
  </section>;
}
