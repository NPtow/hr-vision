import { useEffect, useId, useRef, useState, type ComponentProps, type ReactNode } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { ArrowLeft, BriefcaseBusiness, CalendarDays, MapPin, Pencil, UserRound } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { PreferencesForm } from './PreferencesForm';
import { ResumeUpload } from './ResumeUpload';
import type { CandidatePreferences, CandidateState } from './api';
import '@fontsource-variable/inter';
import './candidate-profile.css';

export type CandidateProfileProps = {
  candidate: CandidateState;
  busy: boolean;
  onSave: (patch: Partial<CandidatePreferences>) => Promise<boolean>;
  onUpload: ComponentProps<typeof ResumeUpload>['onUpload'];
  onSearchChange: (searching: boolean) => Promise<boolean>;
  contacts: ReactNode;
  onBack: () => void;
};

const employmentLabels: Record<CandidatePreferences['employmentType'], string> = {
  '': 'Не указана', 'full-time': 'Полная занятость', 'part-time': 'Частичная занятость',
  project: 'Проектная работа', internship: 'Стажировка',
};
function salaryLabel(min: number | null, max: number | null) {
  const amount = (value: number) => value.toLocaleString('ru-RU');
  if (min != null && max != null) return `${amount(min)}–${amount(max)} ₽`;
  if (min != null) return `От ${amount(min)} ₽`;
  if (max != null) return `До ${amount(max)} ₽`;
  return 'Не указана';
}
function startDateLabel(value: string) {
  if (!value) return 'Не указана';
  const date = new Date(`${value}T12:00:00`);
  return Number.isNaN(date.getTime()) ? 'Не указана' : date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' });
}
const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join('').toUpperCase();

export function CandidateProfile({ candidate, busy, onSave, onUpload, onSearchChange, contacts, onBack }: CandidateProfileProps) {
  const [editing, setEditing] = useState(false);
  const [stopRequested, setStopRequested] = useState(false);
  const [operation, setOperation] = useState<'preferences' | 'search' | null>(null);
  const [localError, setLocalError] = useState('');
  const lock = useRef(false);
  const editor = useRef<HTMLElement>(null);
  const profileRoot = useRef<HTMLElement>(null);
  const alive = useRef(true);
  const id = useId();
  const reducedMotion = useReducedMotion();
  const working = busy || operation !== null;
  const searching = candidate.searching !== false;

  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { setEditing(false); setStopRequested(false); setLocalError(''); }, [candidate.id]);
  useEffect(() => { if (!searching) setStopRequested(false); }, [searching]);
  useEffect(() => {
    if (editing) editor.current?.querySelector<HTMLTextAreaElement>('textarea')?.focus();
  }, [editing]);

  function openEditor() { setEditing(true); setLocalError(''); }
  function closeEditor() { setEditing(false); setLocalError(''); requestAnimationFrame(() => profileRoot.current?.querySelector<HTMLButtonElement>('.cp-profile-edit')?.focus()); }
  async function savePreferences(patch: Partial<CandidatePreferences>) {
    if (busy || lock.current) return;
    lock.current = true; setOperation('preferences'); setLocalError('');
    try { if (await onSave(patch) && alive.current) closeEditor(); }
    catch (error) { if (alive.current) setLocalError(error instanceof Error ? error.message : 'Не удалось сохранить изменения. Попробуйте ещё раз.'); }
    finally { lock.current = false; if (alive.current) setOperation(null); }
  }
  async function changeSearch(next: boolean) {
    if (busy || lock.current) return;
    lock.current = true; setOperation('search'); setLocalError('');
    try { if (await onSearchChange(next) && alive.current) setStopRequested(false); }
    catch (error) { if (alive.current) setLocalError(error instanceof Error ? error.message : 'Не удалось изменить статус поиска. Попробуйте ещё раз.'); }
    finally { lock.current = false; if (alive.current) setOperation(null); }
  }
  return <section ref={profileRoot} className="cp-profile" aria-labelledby={`${id}-title`}>
    <header className="cp-profile-heading">
      <div><Button type="button" variant="ghost" size="sm" className="cp-profile-back" disabled={working} onClick={onBack}><ArrowLeft size={15}/>К поиску</Button><h1 id={`${id}-title`}>Профиль</h1></div>
      {!editing && <Button type="button" variant="ghost" size="sm" className="cp-profile-edit" disabled={working} onClick={openEditor}><Pencil size={14}/>Редактировать</Button>}
    </header>

    <section className={`cp-profile-search ${searching ? 'is-searching' : ''}`} aria-labelledby={`${id}-search-title`}>
      <div className="cp-profile-search-row"><h2 id={`${id}-search-title`}>{searching ? 'Ищу работу' : 'Поиск остановлен'}</h2><label className="cp-profile-switch"><input type="checkbox" role="switch" aria-label="Ищу работу" checked={searching && !stopRequested} disabled={working} onChange={event => { setLocalError(''); if (searching) setStopRequested(!event.target.checked); else if (event.target.checked) void changeSearch(true); }}/><span aria-hidden="true"/></label></div>
      <AnimatePresence initial={false}>{stopRequested && searching && <motion.div className="cp-profile-stop-confirm" role="group" aria-labelledby={`${id}-confirm-title`} initial={{ height: reducedMotion ? 'auto' : 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: reducedMotion ? 'auto' : 0, opacity: 0 }} transition={{ duration: .18 }}><div><h3 id={`${id}-confirm-title`}>Остановить поиск?</h3><p>Текущие вакансии, встречи и офферы перейдут в историю. Возобновление поиска не вернёт их в работу.</p><div className="cp-profile-confirm-actions"><Button type="button" variant="ghost" disabled={working} onClick={() => setStopRequested(false)}>Продолжить поиск</Button><Button type="button" className="cp-profile-stop-button" disabled={working} onClick={() => void changeSearch(false)}>{operation === 'search' ? 'Останавливаем…' : 'Остановить поиск'}</Button></div></div></motion.div>}</AnimatePresence>
      {operation === 'search' && !stopRequested && <p className="cp-profile-search-progress" role="status">Возобновляем поиск…</p>}
    </section>

    {localError && <p className="cp-profile-error" role="alert">{localError}</p>}

    <div className="cp-profile-layout">
      <aside className="cp-profile-sidebar">
        <section className="cp-profile-person" aria-label="Личные данные">
          <span className="cp-profile-avatar" aria-hidden="true">{initials(candidate.name) || <UserRound size={28}/>}</span>
          {candidate.name && <h2>{candidate.name}</h2>}
          {candidate.desiredRole && <p className="cp-profile-role">{candidate.desiredRole}</p>}
          <div className="cp-profile-location"><MapPin size={15}/><div><span>{candidate.city || 'Город не указан'}</span>{candidate.residence && <small>{candidate.residence}</small>}</div></div>
          <dl className="cp-profile-approval"><dt>Вакансии</dt><dd>{candidate.autoApprove && candidate.consentReuse ? 'Согласовываются автоматически' : 'Согласовываю лично'}</dd></dl>
        </section>
      </aside>

      <div className="cp-profile-main">
        {editing ? <motion.section ref={editor} className="cp-profile-card cp-profile-editor" aria-labelledby={`${id}-editor-title`} initial={{ opacity: 0, y: reducedMotion ? 0 : 5 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .16 }}><div className="cp-profile-card-heading"><h2 id={`${id}-editor-title`}>Редактирование</h2></div><fieldset className="cp-profile-editor-fields" disabled={working}><PreferencesForm key={candidate.id} initial={candidate} busy={working} onSubmit={savePreferences}/></fieldset><Button type="button" variant="ghost" className="cp-profile-cancel" disabled={working} onClick={closeEditor}>Отмена</Button></motion.section> : <section className="cp-profile-card cp-profile-details">
          <section className="cp-profile-about-card"><div className="cp-profile-card-heading"><h2>О себе</h2></div><p className={`cp-profile-about ${candidate.about ? '' : 'is-empty'}`}>{candidate.about || 'Не заполнено'}</p></section>
          <section><div className="cp-profile-card-heading"><h2>Условия работы</h2></div><dl className="cp-profile-conditions"><div className="cp-profile-salary"><dt>Зарплата в месяц</dt><dd>{salaryLabel(candidate.salaryMin, candidate.salaryMax)}</dd></div><div><dt><BriefcaseBusiness size={15}/>Занятость</dt><dd>{employmentLabels[candidate.employmentType] || 'Не указана'}</dd></div><div><dt><CalendarDays size={15}/>Выход не позднее</dt><dd>{startDateLabel(candidate.latestStartDate)}</dd></div></dl></section>
        </section>}
        <section className="cp-profile-card cp-profile-resume-card"><ResumeUpload compact resume={candidate.resume} busy={working} onUpload={onUpload}/></section>
      </div>
      <section className="cp-profile-contacts-card">{contacts}</section>
    </div>
  </section>;
}
