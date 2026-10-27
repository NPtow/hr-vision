import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { Dialog } from 'radix-ui';
import { ArrowRight, BriefcaseBusiness, CalendarDays, Check, ChevronDown, ChevronRight, CircleUserRound, MapPin, Pencil, Send, Settings2, Sparkles, X } from 'lucide-react';
import type { Profile, ProfileProps } from './types';
import './profile.css';

type Group = 'identity' | 'about' | 'conditions' | 'connections';
type StringField = Exclude<keyof Profile, 'telegram' | 'autoApprove' | 'searching'>;
type Errors = Partial<Record<StringField, string>>;
const groups: Group[] = ['identity', 'about', 'conditions', 'connections'];
const groupTitles: Record<Group, string> = { identity: 'Личные данные', about: 'О себе', conditions: 'Условия работы', connections: 'Связь и предложения' };
const groupHints: Record<Group, string> = { identity: 'Как к вам обращаться и где вы живёте', about: 'Коротко о вашем опыте и следующем шаге', conditions: 'Что важно в следующей работе', connections: 'Как получать новости и выбирать вакансии' };
const groupIcons = { identity: CircleUserRound, about: Sparkles, conditions: BriefcaseBusiness, connections: Send };
const employmentOptions = ['Полная занятость', 'Частичная занятость', 'Проектная работа', 'Стажировка'];
const employmentNames: Record<string, string> = { 'full-time': 'Полная занятость', 'part-time': 'Частичная занятость', project: 'Проектная работа', internship: 'Стажировка' };
const money = (value: string) => value.trim() && Number.isFinite(Number(value)) ? Number(value).toLocaleString('ru-RU') : '—';
const dateLabel = (value: string) => { const date = new Date(`${value}T12:00:00`); return Number.isNaN(date.getTime()) ? 'Не указана' : date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }); };
const initials = (value: string) => value.split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join('');
const employmentLabel = (value: string) => employmentNames[value] || value || 'Не указана';
function validate(profile: Profile): Errors {
  const errors: Errors = {};
  if (!profile.name.trim()) errors.name = 'Укажите имя.';
  if (!profile.role.trim()) errors.role = 'Укажите желаемую должность.';
  if (!profile.city.trim()) errors.city = 'Укажите город.';
  if (!profile.employment) errors.employment = 'Выберите тип занятости.';
  if (profile.about.trim().length > 1500) errors.about = 'Не больше 1500 символов.';
  for (const key of ['salaryMin', 'salaryMax'] as const) {
    const value = Number(profile[key]);
    if (!profile[key].trim() || !Number.isSafeInteger(value) || value <= 0) errors[key] = 'Укажите сумму больше нуля.';
  }
  if (!errors.salaryMin && !errors.salaryMax && Number(profile.salaryMax) < Number(profile.salaryMin)) errors.salaryMax = 'Верхняя граница должна быть не ниже нижней.';
  if (!/^\d{4}-\d{2}-\d{2}$/.test(profile.startBy) || Number.isNaN(new Date(profile.startBy).getTime())) errors.startBy = 'Укажите дату выхода.';
  return errors;
}
function ProfileFields({ group, draft, update, errors, prefix }: { group: Group; draft: Profile; update: <K extends keyof Profile>(key: K, value: Profile[K]) => void; errors: Errors; prefix: string }) {
  function field(key: StringField, title: string, type = 'text', hint?: string) {
    const id = `${prefix}-${key}`;
    return <label className={`cex-profile-field ${key === 'name' || key === 'role' || key === 'area' || key === 'startBy' ? 'cex-profile-field-wide' : ''}`} key={key} htmlFor={id}><span>{title}</span><input id={id} data-profile-field={key} value={draft[key]} type={type} inputMode={type === 'number' ? 'numeric' : undefined} min={type === 'number' ? 1 : undefined} maxLength={type === 'text' ? 200 : undefined} aria-invalid={!!errors[key]} aria-describedby={errors[key] || hint ? `${id}-hint` : undefined} onChange={event => update(key, event.target.value)}/>{(errors[key] || hint) && <small id={`${id}-hint`} className={errors[key] ? 'cex-profile-field-error' : ''}>{errors[key] || hint}</small>}</label>;
  }
  if (group === 'identity') return <div className="cex-profile-fields">{field('name', 'Имя и фамилия')}{field('role', 'Желаемая должность')}{field('city', 'Город')}{field('area', 'Район или метро', 'text', 'Чтобы учитывать дорогу до работы')}</div>;
  if (group === 'about') return <div className="cex-profile-fields"><label className="cex-profile-field cex-profile-field-wide" htmlFor={`${prefix}-about`}><span>О себе</span><textarea id={`${prefix}-about`} data-profile-field="about" value={draft.about} maxLength={1500} rows={6} aria-invalid={!!errors.about} aria-describedby={`${prefix}-about-hint`} onChange={event => update('about', event.target.value)}/><small id={`${prefix}-about-hint`}>{errors.about || 'Текст подготовлен по вашему резюме. Вы можете его изменить.'}</small></label></div>;
  if (group === 'conditions') return <div className="cex-profile-fields">{field('salaryMin', 'Зарплата от, ₽ в месяц', 'number')}{field('salaryMax', 'До, ₽ в месяц', 'number')}<label className="cex-profile-field cex-profile-field-wide" htmlFor={`${prefix}-employment`}><span>Тип занятости</span><select id={`${prefix}-employment`} data-profile-field="employment" value={draft.employment} aria-invalid={!!errors.employment} onChange={event => update('employment', event.target.value)}><option value="">Выберите</option>{draft.employment && !employmentOptions.includes(draft.employment) && <option value={draft.employment}>{employmentLabel(draft.employment)}</option>}{employmentOptions.map(value => <option key={value}>{value}</option>)}</select>{errors.employment && <small className="cex-profile-field-error">{errors.employment}</small>}</label>{field('startBy', 'Выйду на работу не позднее', 'date')}</div>;
  return <div className="cex-profile-toggles"><label className="cex-profile-toggle"><span><strong>Telegram</strong><small>Статусы вакансий и приглашения на встречи</small></span><input type="checkbox" role="switch" aria-label="Telegram" checked={draft.telegram} onChange={event => update('telegram', event.target.checked)}/><span className="cex-profile-switch" aria-hidden="true"/></label><label className="cex-profile-toggle"><span><strong>Согласовывать вакансии автоматически</strong><small>По желанию. Время каждой встречи вы подтверждаете сами.</small></span><input type="checkbox" role="switch" aria-label="Согласовывать вакансии автоматически" checked={draft.autoApprove} onChange={event => update('autoApprove', event.target.checked)}/><span className="cex-profile-switch" aria-hidden="true"/></label></div>;
}
function ProfileReadGroup({ group, profile }: { group: Group; profile: Profile }) {
  if (group === 'about') return <p className="cex-profile-about">{profile.about || 'Расскажите о своём опыте и о том, какую работу ищете.'}</p>;
  const rows: [string, string][] = group === 'identity' ? [['Имя', profile.name], ['Должность', profile.role], ['Город', profile.city], ['Район или метро', profile.area || 'Не указан']] : group === 'conditions' ? [['Зарплата в месяц', `${money(profile.salaryMin)}–${money(profile.salaryMax)} ₽`], ['Занятость', employmentLabel(profile.employment)], ['Выход не позднее', dateLabel(profile.startBy)]] : [['Telegram', profile.telegram ? 'Подключён' : 'Не подключён'], ['Вакансии', profile.autoApprove ? 'Согласуются автоматически' : 'Согласовываю лично']];
  return <><dl className="cex-profile-values">{rows.map(([title, value]) => <div key={title}><dt>{title}</dt><dd>{value}</dd></div>)}</dl>{group === 'connections' && <p className="cex-profile-footnote">Время каждой встречи вы подтверждаете сами.</p>}</>;
}
function GroupIcon({ group }: { group: Group }) { const Icon = groupIcons[group]; return <Icon size={19}/>; }

export function ProfileVariants({ variant, profile, save }: ProfileProps) {
  const [draft, setDraft] = useState<Profile>({ ...profile });
  const [editing, setEditing] = useState<Group | 'all' | null>(null);
  const [active, setActive] = useState<Group>('identity');
  const [expanded, setExpanded] = useState<Group | null>('about');
  const [tab, setTab] = useState<'account' | 'work'>('account');
  const [errors, setErrors] = useState<Errors>({});
  const [notice, setNotice] = useState('');
  const host = useRef<HTMLDivElement>(null);
  const drawerOpener = useRef<HTMLButtonElement | null>(null);
  const prefix = useId();
  const reducedMotion = useReducedMotion();
  const dirty = JSON.stringify(draft) !== JSON.stringify(profile);
  useEffect(() => { setDraft({ ...profile }); }, [profile]);
  useEffect(() => { setEditing(null); setErrors({}); setNotice(''); setDraft({ ...profile }); }, [variant]);
  function update<K extends keyof Profile>(key: K, value: Profile[K]) { setDraft(previous => ({ ...previous, [key]: value })); setErrors(previous => ({ ...previous, [key]: undefined })); setNotice(''); }
  function begin(group: Group | 'all', opener?: HTMLButtonElement) { if (!editing) setDraft({ ...profile }); setEditing(group); setErrors({}); setNotice(''); if (opener) drawerOpener.current = opener; }
  function cancel() { setDraft({ ...profile }); setEditing(null); setErrors({}); setNotice(''); }
  function submit(event: FormEvent) {
    event.preventDefault();
    const nextErrors = validate(draft);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) {
      const first = Object.keys(nextErrors)[0] as StringField;
      const targetGroup: Group = ['name', 'role', 'city', 'area'].includes(first) ? 'identity' : first === 'about' ? 'about' : 'conditions';
      if (editing !== 'all') setEditing(targetGroup);
      setExpanded(targetGroup); setActive(targetGroup); setTab(targetGroup === 'conditions' ? 'work' : 'account');
      requestAnimationFrame(() => host.current?.querySelector<HTMLElement>(`[data-profile-field="${first}"]`)?.focus());
      return;
    }
    const value = { ...draft, name: draft.name.trim(), role: draft.role.trim(), city: draft.city.trim(), area: draft.area.trim(), about: draft.about.trim() };
    save(value); setDraft(value); setEditing(null); setNotice('Изменения сохранены');
  }
  const actions = <div className="cex-profile-actions"><span>{dirty ? 'Есть изменения' : 'Все изменения сохранены'}</span><button type="button" className="cex-profile-button cex-profile-button-quiet" onClick={cancel}>Отмена</button><button className="cex-profile-button cex-profile-button-primary" type="submit" disabled={!dirty}>Сохранить</button></div>;
  function fields(group: Group) { return <ProfileFields group={group} draft={draft} update={update} errors={errors} prefix={`${prefix}-${group}`}/>; }
  function editButton(group: Group | 'all', label = 'Изменить') { return <button type="button" className="cex-profile-edit" onClick={event => begin(group, event.currentTarget)} aria-label={`${label}: ${group === 'all' ? 'профиль' : groupTitles[group]}`}><Pencil size={14}/><span>{label}</span></button>; }
  function editor(group: Group | 'all') { return <motion.form className="cex-profile-editor" noValidate onSubmit={submit} initial={{ opacity: 0, y: reducedMotion ? 0 : 5 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .16 }}>{group === 'all' ? groups.map(item => <section className="cex-profile-editor-section" key={item}><h3>{groupTitles[item]}</h3>{fields(item)}</section>) : fields(group)}{Object.values(errors).some(Boolean) && <p className="cex-profile-error" role="alert">Проверьте отмеченные поля.</p>}{actions}</motion.form>; }
  function block(group: Group, extra = '') { return <section className={`cex-profile-card ${extra}`} key={group}><div className="cex-profile-section-heading"><div><h2>{groupTitles[group]}</h2></div>{editing !== group && editButton(group)}</div>{editing === group ? editor(group) : <ProfileReadGroup group={group} profile={profile}/>}</section>; }
  const identity = <div className="cex-profile-identity"><span className="cex-profile-avatar" aria-hidden="true">{initials(profile.name)}</span><div><h2>{profile.name}</h2><p>{profile.role}</p><span className="cex-profile-location"><MapPin size={14}/>{profile.city}{profile.area ? ` · ${profile.area}` : ''}</span></div></div>;
  const searchStatus = <form className={`cex-profile-search-status ${draft.searching ? 'is-searching' : ''}`} noValidate onSubmit={submit}><label className="cex-profile-toggle"><span><strong>Ищу работу</strong><small>При остановке поиска все процессы уйдут в историю</small></span><input type="checkbox" role="switch" aria-label="Ищу работу" checked={draft.searching} onChange={event => update('searching', event.target.checked)}/><span className="cex-profile-switch" aria-hidden="true"/></label>{draft.searching !== profile.searching && <div className="cex-profile-search-actions"><button type="button" className="cex-profile-button cex-profile-button-quiet" onClick={() => update('searching', profile.searching)}>Отмена</button><button type="submit" className="cex-profile-button cex-profile-button-primary">Сохранить</button></div>}</form>;
  const heading = <><header className="cex-profile-heading"><div><span className="cex-profile-eyebrow">Личный кабинет</span><h1>Мой профиль</h1><p>Ваш опыт и пожелания к следующей работе</p></div>{[0, 3, 7].includes(variant) && variant !== 3 && editButton('all', 'Редактировать')}</header>{searchStatus}</>;
  let content;
  switch (variant) {
    case 0:
      content = <div className="cex-profile-document">{heading}<div className="cex-profile-document-header">{identity}</div>{editing === 'all' ? editor('all') : groups.map(group => <section className="cex-profile-document-section" key={group}><div className="cex-profile-section-heading"><h2>{groupTitles[group]}</h2>{editing !== group && editButton(group)}</div>{editing === group ? editor(group) : <ProfileReadGroup group={group} profile={profile}/>}</section>)}</div>;
      break;
    case 1:
      content = <div className="cex-profile-settings">{heading}{identity}<div className="cex-profile-settings-list">{groups.map(group => <section className="cex-profile-setting" key={group}><button className="cex-profile-setting-title" onClick={() => editing === group ? cancel() : begin(group)} aria-expanded={editing === group}><span className="cex-profile-icon"><GroupIcon group={group}/></span><span><strong>{groupTitles[group]}</strong><small>{groupHints[group]}</small></span><ChevronDown size={18} className={editing === group ? 'cex-profile-rotated' : ''}/></button>{editing === group ? editor(group) : <div className="cex-profile-setting-value"><ProfileReadGroup group={group} profile={profile}/></div>}</section>)}</div></div>;
      break;
    case 2:
      content = <>{heading}<div className="cex-profile-identity-layout"><aside className="cex-profile-person-card">{identity}{editing === 'identity' ? editor('identity') : <>{editButton('identity', 'Изменить данные')}<div className="cex-profile-person-divider"/><span className="cex-profile-eyebrow">Связь</span><p className="cex-profile-connection"><Send size={16}/>{profile.telegram ? 'Telegram подключён' : 'Telegram не подключён'}</p>{editButton('connections', 'Настроить связь')}</>}{editing === 'connections' && editor('connections')}</aside><div className="cex-profile-stack">{block('about')}{block('conditions')}<p className="cex-profile-footnote">{profile.autoApprove ? 'Подходящие вакансии согласуются автоматически.' : 'Вы сами выбираете подходящие вакансии.'} Время встреч подтверждаете лично.</p></div></div></>;
      break;
    case 3:
      content = <>{heading}<div className="cex-profile-preview-layout"><aside className="cex-profile-paper"><span className="cex-profile-eyebrow">Профиль</span><h2>{draft.name || 'Ваше имя'}</h2><p className="cex-profile-paper-role">{draft.role || 'Желаемая должность'}</p><p className="cex-profile-location"><MapPin size={14}/>{draft.city}{draft.area ? ` · ${draft.area}` : ''}</p><div className="cex-profile-person-divider"/><h3>О себе</h3><p className="cex-profile-about">{draft.about}</p><div className="cex-profile-person-divider"/><ProfileReadGroup group="conditions" profile={draft}/></aside><section className="cex-profile-form-panel"><h2>Редактировать профиль</h2>{editor('all')}</section></div></>;
      break;
    case 4:
      content = <div className="cex-profile-accordion-layout">{heading}{identity}<div className="cex-profile-accordions">{groups.map((group, index) => <section className={`cex-profile-accordion ${expanded === group ? 'is-open' : ''}`} key={group}><button className="cex-profile-accordion-trigger" aria-expanded={expanded === group} onClick={() => setExpanded(expanded === group ? null : group)}><span className="cex-profile-section-number">0{index + 1}</span><span>{groupTitles[group]}</span><ChevronDown size={18}/></button><AnimatePresence initial={false}>{expanded === group && <motion.div className="cex-profile-accordion-body" initial={{ height: reducedMotion ? 'auto' : 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: reducedMotion ? 'auto' : 0, opacity: 0 }} transition={{ duration: .18 }}>{editing === group ? editor(group) : <><ProfileReadGroup group={group} profile={profile}/>{editButton(group)}</>}</motion.div>}</AnimatePresence></section>)}</div></div>;
      break;
    case 5:
      content = <>{heading}<div className="cex-profile-bento"><section className="cex-profile-card cex-profile-bento-person">{identity}{editing === 'identity' ? editor('identity') : editButton('identity')}</section>{block('about', 'cex-profile-bento-about')}{block('conditions', 'cex-profile-bento-conditions')}{block('connections', 'cex-profile-bento-connections')}</div></>;
      break;
    case 6:
      content = <>{heading}<div className="cex-profile-inspector"><div className="cex-profile-section-list" aria-label="Разделы профиля">{groups.map(group => <button key={group} className={active === group ? 'is-active' : ''} onClick={() => { setActive(group); if (editing) setEditing(group); }}><GroupIcon group={group}/><span>{groupTitles[group]}</span><ChevronRight size={16}/></button>)}<div className="cex-profile-list-person"><span className="cex-profile-avatar">{initials(profile.name)}</span><strong>{profile.name}</strong></div></div><section className="cex-profile-inspector-main"><div className="cex-profile-section-heading"><div><span className="cex-profile-eyebrow">Профиль</span><h2>{groupTitles[active]}</h2><p>{groupHints[active]}</p></div>{editing !== active && editButton(active)}</div>{editing === active ? editor(active) : <ProfileReadGroup group={active} profile={profile}/>}</section></div></>;
      break;
    case 7:
      content = <div className="cex-profile-personal">{heading}<div className="cex-profile-personal-intro"><span className="cex-profile-avatar">{initials(profile.name)}</span><h2>{profile.name}</h2><p>{profile.role}</p><span className="cex-profile-location"><MapPin size={14}/>{profile.city}{profile.area ? ` · ${profile.area}` : ''}</span></div>{editing === 'all' ? editor('all') : <>{block('about')}{block('conditions')}{block('identity')}{block('connections')}</>}</div>;
      break;
    case 8:
      content = <div className="cex-profile-tabbed">{heading}{identity}<div className="cex-profile-tabs" role="tablist" aria-label="Настройки профиля">{(['account', 'work'] as const).map(value => <button type="button" role="tab" id={`${prefix}-tab-${value}`} aria-controls={`${prefix}-panel-${value}`} aria-selected={tab === value} tabIndex={tab === value ? 0 : -1} key={value} onKeyDown={event => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { const next = value === 'account' ? 'work' : 'account'; setTab(next); requestAnimationFrame(() => document.getElementById(`${prefix}-tab-${next}`)?.focus()); } }} onClick={() => setTab(value)}>{value === 'account' ? <CircleUserRound size={17}/> : <Settings2 size={17}/>}<span>{value === 'account' ? 'Личные данные' : 'Поиск работы'}</span></button>)}</div><section role="tabpanel" id={`${prefix}-panel-${tab}`} aria-labelledby={`${prefix}-tab-${tab}`} className="cex-profile-tab-panel">{(tab === 'account' ? ['identity', 'about'] as Group[] : ['conditions', 'connections'] as Group[]).map(group => block(group))}</section></div>;
      break;
    default:
      content = <>{heading}<div className="cex-profile-summary">{identity}<div className="cex-profile-summary-grid">{groups.map(group => <button type="button" className={`cex-profile-summary-tile cex-profile-summary-${group}`} key={group} onClick={event => begin(group, event.currentTarget)}><span className="cex-profile-summary-label"><GroupIcon group={group}/>{groupTitles[group]}<ArrowRight size={17}/></span>{group === 'about' ? <p>{profile.about}</p> : group === 'conditions' ? <><strong>{money(profile.salaryMin)}–{money(profile.salaryMax)} ₽</strong><p>{employmentLabel(profile.employment)}</p><small><CalendarDays size={14}/>До {dateLabel(profile.startBy)}</small></> : group === 'identity' ? <><strong>{profile.name}</strong><p>{profile.city}{profile.area ? ` · ${profile.area}` : ''}</p></> : <><strong>{profile.telegram ? 'Telegram подключён' : 'Подключить Telegram'}</strong><p>{profile.autoApprove ? 'Автосогласование включено' : 'Вакансии согласую лично'}</p></>}</button>)}</div></div><Dialog.Root open={!!editing} onOpenChange={open => { if (!open) cancel(); }}><Dialog.Portal container={host.current}><Dialog.Overlay className="cex-profile-drawer-overlay"/><Dialog.Content className="cex-profile-drawer" aria-describedby={`${prefix}-drawer-description`} onCloseAutoFocus={event => { event.preventDefault(); drawerOpener.current?.focus(); }}><div className="cex-profile-drawer-heading"><div><Dialog.Title>{editing && editing !== 'all' ? groupTitles[editing] : 'Мой профиль'}</Dialog.Title><Dialog.Description id={`${prefix}-drawer-description`}>Изменения появятся в профиле после сохранения.</Dialog.Description></div><Dialog.Close className="cex-profile-close" aria-label="Закрыть без сохранения"><X size={19}/></Dialog.Close></div>{editing && editor(editing)}</Dialog.Content></Dialog.Portal></Dialog.Root></>;
  }
  return <div ref={host} className={`cex-profile cex-profile-v${variant}`} data-profile-variant={variant}>{content}<AnimatePresence>{notice && <motion.div key="saved" className="cex-profile-notice" role="status" initial={{ opacity: 0, y: reducedMotion ? 0 : 5 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}><Check size={16}/>{notice}<button aria-label="Скрыть сообщение" onClick={() => setNotice('')}><X size={15}/></button></motion.div>}</AnimatePresence></div>;
}
