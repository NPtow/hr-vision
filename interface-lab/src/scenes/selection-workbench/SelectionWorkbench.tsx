import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUpRight, ChevronRight, ListFilter, Search, Table2 } from 'lucide-react';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import { useOperatorSession } from '../hh-outreach/session';
import { OutreachError } from '../hh-outreach/api';
import { selectionRequest, type SelectionPerson, type SelectionVacancy } from './api';
import { loadCandidateDirectory } from './directory';
import { candidateAssessmentState, AssessmentEntry, assessmentLabels, HRInterviewBadge } from './review-state';
import { CandidateDetail, InlineCell } from './CandidateDetail';
import { hasHRInterview } from './matching-contract';
import { VacancyDetail } from './VacancyDetail';
import './selection-workbench.css';

type Tab = 'candidates' | 'vacancies';
type StatusFilters = { interview: 'ready' | 'all' | 'pending' | 'unknown'; telegram: string; email: string; assessment: string };
const defaultFilters: StatusFilters = { interview: 'ready', telegram: 'all', email: 'all', assessment: 'all' };
const originLink = (workspace: string) => {
  const url = new URL(location.href); url.searchParams.set('workspace', workspace);
  ['vacancyId', 'tab', 'candidateId'].forEach(key => url.searchParams.delete(key)); return url.pathname + url.search;
};

export function SelectionWorkbench({ outreachHref }: { outreachHref?: string }) {
  const auth = useOperatorSession();
  return <main className="sl-root"><header className="sl-topbar"><img src={logo} alt="HR Vision"/><span>Верстак</span><a href={outreachHref || originLink('outreach')}>Рассылка<ArrowUpRight size={15}/></a></header>
    {auth.status === 'ready' ? <Workbench key={auth.token} token={auth.token} expire={auth.expire}/> : <section className="sl-access"><h1>{auth.status === 'checking' ? 'Проверяем доступ…' : 'Вход для команды'}</h1>{auth.status !== 'checking' && <><p>{auth.error || 'Откройте рабочую ссылку команды HR Vision.'}</p><button onClick={() => void auth.check()}>Проверить доступ</button></>}</section>}
  </main>;
}

function Workbench({ token, expire }: { token: string; expire: () => void }) {
  const query = new URLSearchParams(location.search);
  const [tab, setTab] = useState<Tab>(query.get('tab') === 'vacancies' ? 'vacancies' : 'candidates');
  const [vid, setVid] = useState(query.get('tab') === 'vacancies' ? query.get('vacancyId') || '' : '');
  const [detail, setDetail] = useState(query.get('candidateId') || '');
  const [vacancies, setVacancies] = useState<SelectionVacancy[]>([]);
  const [people, setPeople] = useState<SelectionPerson[]>([]);
  const [search, setSearch] = useState('');
  const [filters, setFilters] = useState(defaultFilters);
  const [filtersOpen, setFiltersOpen] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [editError, setEditError] = useState('');
  const [candidateInputRevision, setCandidateInputRevision] = useState(0);
  const matchingInputsChanged = useCallback(() => setCandidateInputRevision(value => value + 1), []);
  const generation = useRef(0);
  const saving = useRef(false);
  const load = useCallback(async () => {
    const seq = ++generation.current; setLoading(true); setError('');
    try {
      const [list, data] = await Promise.all([
        selectionRequest<{ vacancies: SelectionVacancy[] }>('vacancies', token),
        loadCandidateDirectory(token),
      ]);
      if (seq === generation.current) { setVacancies(list.vacancies); setPeople(data.candidates); }
    } catch (cause) {
      if (seq !== generation.current) return;
      setPeople([]); setVacancies([]);
      if (cause instanceof OutreachError && [401, 403].includes(cause.status)) expire();
      else setError((cause as Error).message);
    } finally { if (seq === generation.current) setLoading(false); }
  }, [token, expire]);
  useEffect(() => { void load(); return () => { generation.current++; }; }, [load]);
  useEffect(() => {
    const url = new URL(location.href); url.searchParams.set('tab', tab);
    if (tab === 'vacancies' && vid) url.searchParams.set('vacancyId', vid); else url.searchParams.delete('vacancyId');
    if (detail) url.searchParams.set('candidateId', detail); else url.searchParams.delete('candidateId');
    history.replaceState(history.state, '', url);
  }, [tab, vid, detail]);
  const rows = useMemo(() => people.filter(p =>
    (filters.interview === 'all' || (filters.interview === 'ready' ? hasHRInterview(p)
      : filters.interview === 'unknown' ? !p.hrInterview || p.hrInterview.state === 'unavailable'
      : !!p.hrInterview && ['missing', 'incomplete', 'processing'].includes(p.hrInterview.state)))
    && (filters.telegram === 'all' || p.telegramConnected === (filters.telegram === 'yes'))
    && (filters.email === 'all' || p.emailConnected === (filters.email === 'yes'))
    && (filters.assessment === 'all' || candidateAssessmentState(p).state === filters.assessment)
    && [p.name, p.desiredRole, p.city, p.email].join(' ').toLocaleLowerCase('ru').includes(search.toLocaleLowerCase('ru'))), [people, search, filters]);
  const activeFilters = Object.values(filters).filter(value => value !== 'all').length;
  const person = people.find(p => p.id === detail);
  const vacancy = vacancies.find(v => v.id === vid);
  const openPerson = (id: string) => { setDetail(id); };
  async function save(p: SelectionPerson, fields: Partial<Pick<SelectionPerson, 'name' | 'desiredRole' | 'city' | 'about'>>) {
    if (saving.current) return; saving.current = true; setBusy(true); setEditError('');
    try {
      const value = await selectionRequest<{ candidate: SelectionPerson }>('candidates/update', token, { candidateId: p.id, revision: p.revision, fields });
      if (value.candidate?.id !== p.id) throw new Error('Получена карточка другого кандидата.');
      // Directory spelling is editable without remounting the active shortlist.
      setPeople(current => current.map(person => person.id === p.id ? { ...person, ...value.candidate } : person));
    }
    catch (cause) { if (cause instanceof OutreachError && [401, 403].includes(cause.status)) expire(); else setEditError((cause as Error).message); }
    finally { saving.current = false; setBusy(false); }
  }
  return <div className="sl-workspace">
    {!(tab === 'vacancies' && vacancy) && <div className="sl-heading"><h1>База подбора</h1></div>}
    <nav className="sl-tabs" aria-label="Разделы базы">{(['candidates', 'vacancies'] as const).map(key => <button key={key} aria-current={key === tab ? 'page' : undefined} onClick={() => { setTab(key); setDetail(''); setVid(''); setSearch(''); }}>{key === 'candidates' ? 'Кандидаты' : 'Вакансии'}<span>{loading || error ? '—' : key === 'candidates' ? people.filter(hasHRInterview).length : vacancies.length}</span></button>)}</nav>
    {error && <p className="sl-error" role="alert">{error}</p>}
    {editError && <p className="sl-error" role="alert">{editError}</p>}
    {tab === 'vacancies' && vid && !loading && !error ? vacancy
      ? <VacancyDetail key={vid} vacancy={vacancy} people={people} token={token} expire={expire} onBack={() => setVid('')} onCandidate={openPerson} candidateInputRevision={candidateInputRevision}/>
      : <div className="sl-empty"><p>Вакансия недоступна.</p><button onClick={() => setVid('')}>К вакансиям</button></div>
      : <section className="sl-surface" aria-label={tab === 'candidates' ? 'Кандидаты' : 'Вакансии'} aria-busy={loading || busy}>
        <div className="sl-toolbar"><label className="sl-search"><Search size={17}/><span className="sl-sr">Поиск</span><input placeholder={tab === 'vacancies' ? 'Найти вакансию' : 'Найти кандидата'} value={search} onChange={e => setSearch(e.target.value)}/></label>
          {tab === 'candidates' && <button className="sl-status-toggle" aria-expanded={filtersOpen} aria-controls="sl-status-filters" data-active={activeFilters > 0} onClick={() => setFiltersOpen(!filtersOpen)}><ListFilter size={16}/>Фильтры{activeFilters > 0 && <span>{activeFilters}</span>}</button>}
          <button className="sl-refresh" disabled={loading || busy} onClick={() => void load()}>Обновить</button></div>
        {tab === 'candidates' && filtersOpen && <div className="sl-status-filters" id="sl-status-filters" role="group" aria-label="Фильтры кандидатов">
          <label>HR-интервью<select aria-label="HR-интервью" value={filters.interview} onChange={e => setFilters({ ...filters, interview: e.target.value as StatusFilters['interview'] })}><option value="ready">Проведено · запись и текст</option><option value="all">Все кандидаты</option><option value="pending">Материалы не готовы</option><option value="unknown">Не удалось проверить</option></select></label>
          <label>Telegram<select aria-label="Telegram" value={filters.telegram} onChange={e => setFilters({ ...filters, telegram: e.target.value })}><option value="all">Любой статус</option><option value="yes">Подключён</option><option value="no">Не подключён</option></select></label>
          <label>Почта<select aria-label="Почта" value={filters.email} onChange={e => setFilters({ ...filters, email: e.target.value })}><option value="all">Любой статус</option><option value="yes">Подключена</option><option value="no">Не подключена</option></select></label>
          <label>Оценка навыков<select aria-label="Оценка навыков" value={filters.assessment} onChange={e => setFilters({ ...filters, assessment: e.target.value })}><option value="all">Любой статус</option>{Object.entries(assessmentLabels).filter(([key]) => people.some(p => candidateAssessmentState(p).state === key)).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
          <button className="sl-filter-reset" onClick={() => { setFilters(defaultFilters); setSearch(''); }}>По умолчанию</button>
        </div>}
        {loading ? <div className="sl-empty" role="status">Загружаем данные…</div> : error ? <div className="sl-empty">Каталог не загружен. Нажмите «Обновить», чтобы повторить загрузку целиком.</div> : tab === 'vacancies' ? <div className="sl-scroll"><table><thead><tr><th>Вакансия</th><th>Работодатель</th><th>Город</th><th>Условия</th><th/></tr></thead><tbody>{vacancies.filter(v => [v.title, v.company, v.city].join(' ').toLocaleLowerCase('ru').includes(search.toLocaleLowerCase('ru'))).map(v => <tr key={v.id}><td><button className="sl-person" onClick={() => setVid(v.id)}>{v.title}</button></td><td>{v.company}</td><td>{v.city || '—'}</td><td>{v.salary || '—'}</td><td><button className="sl-open" aria-label={`Открыть вакансию: ${v.title}`} onClick={() => setVid(v.id)}>Открыть<ChevronRight size={15}/></button></td></tr>)}</tbody></table>{!vacancies.length && <div className="sl-empty">Нет вакансий</div>}</div>
        : rows.length ? <><div className="sl-scroll"><table><thead><tr><th>Кандидат</th><th>Роль</th><th>Город</th><th>HR-интервью</th><th>Оценка навыков</th><th>Telegram</th><th>Почта</th></tr></thead><tbody>{rows.map(p => <tr key={p.id}>
          <td><button className="sl-person" onClick={() => openPerson(p.id)}>{p.name || 'Без имени'}</button></td>
          <td><InlineCell label={`Роль: ${p.name}`} value={p.desiredRole} disabled={busy} onSave={value => void save(p, { desiredRole: value })}/></td>
          <td><InlineCell label={`Город: ${p.name}`} value={p.city} disabled={busy} onSave={value => void save(p, { city: value })}/></td>
          <td><HRInterviewBadge person={p}/></td>
          <td><AssessmentEntry person={p} onOpen={() => openPerson(p.id)}/></td>
          <td><span className={'sl-status ' + (p.telegramConnected ? 'sl-green' : '')}>{p.telegramConnected ? 'Подключён' : 'Не подключён'}</span></td>
          <td><span className={'sl-status ' + (p.emailConnected ? 'sl-green' : '')}>{p.emailConnected ? 'Подключена' : 'Не подключена'}</span></td>
        </tr>)}</tbody></table></div><footer className="sl-table-footer"><span>{rows.length} в таблице · всего {people.length}</span></footer></>
        : <div className="sl-empty"><Table2 size={24}/><h2>Нет кандидатов по выбранным фильтрам</h2>{people.some(p => !p.hrInterview || p.hrInterview.state === 'unavailable') && <p>Для части базы готовность HR-интервью пока не подтверждена.</p>}<button onClick={() => { setFilters({ ...defaultFilters, interview: 'all' }); setSearch(''); }}>Показать всех кандидатов</button></div>}
      </section>}
    {!loading && !error && person && <CandidateDetail key={person.id} person={person} token={token} expire={expire} busy={busy} onClose={() => setDetail('')} onSave={fields => void save(person, fields)} onMatchingInputsChanged={matchingInputsChanged}/>}
  </div>;
}
