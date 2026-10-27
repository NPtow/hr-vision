import { useId, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { AnimatePresence, LayoutGroup, MotionConfig, motion, useReducedMotion } from 'motion/react';
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUpRight, BriefcaseBusiness, CalendarDays, Check, CheckCheck, ChevronDown, Clock3, MapPin, Video } from 'lucide-react';
import { stageLabels, type SearchFilter, type SearchProps, type Stage, type Vacancy, type VacancyAction } from './types';
import './search.css';

const needsResponse = (job: Vacancy) => !job.archived && ['new', 'proposed', 'completed', 'offer'].includes(job.stage);
const isFinished = (job: Vacancy) => !!job.archived || ['accepted', 'declined'].includes(job.stage);
const filterFor = (job: Vacancy): SearchFilter => isFinished(job) ? 'history' : job.stage === 'new' ? 'new' : 'process';
const statusLabel = (job: Vacancy) => job.archived && !['accepted', 'declined'].includes(job.stage) ? 'В истории' : stageLabels[job.stage];
const companyStyle = (job: Vacancy) => ({ '--cex-company': job.color } as CSSProperties);
const stageCopy: Record<Stage, string> = {
  new: 'Посмотрите условия и решите, хотите ли познакомиться с командой.',
  applied: 'Вы согласились участвовать. Следующий шаг — приглашение от команды.',
  proposed: 'Команда предложила время. Встреча состоится, только если вы его подтвердите.',
  confirmed: 'Всё готово к знакомству. Встреча появится и в вашем календаре.',
  completed: 'Теперь ваша очередь решить, хотите ли вы продолжить общение.',
  followup: 'Ваш интерес передан команде. Когда появится ответ, вы увидите его здесь.',
  offer: 'Команда хочет работать с вами. Проверьте предложение перед ответом.',
  accepted: 'Вы приняли предложение. Дальше — договориться с командой о выходе.',
  declined: 'Вы решили не продолжать. История останется здесь.',
};

function Company({ job, small = false }: { job: Vacancy; small?: boolean }) {
  return <span className={`cex-search-logo${small ? ' cex-search-logo-small' : ''}`} style={companyStyle(job)} aria-hidden="true">{job.initials}</span>;
}

function Status({ job }: { job: Vacancy }) {
  return <span className={`cex-search-status cex-search-status-${job.stage}`}>
    {job.stage === 'accepted' ? <CheckCheck size={13}/> : needsResponse(job) ? <span className="cex-search-status-dot"/> : <span className="cex-search-status-dash"/>}
    {statusLabel(job)}
  </span>;
}

function Facts({ job, extended = false }: { job: Vacancy; extended?: boolean }) {
  return <div className={`cex-search-facts${extended ? ' cex-search-facts-extended' : ''}`}>
    <span className="cex-search-salary">{job.salary}</span>
    <span><MapPin size={14}/>{job.location}</span>
    <span><BriefcaseBusiness size={14}/>{job.format}</span>
  </div>;
}

function Journey({ job, numbered = false }: { job: Vacancy; numbered?: boolean }) {
  const reduced = useReducedMotion();
  const events = job.history || [];
  if (!events.length) return null;
  return <ol className={`cex-search-journey${numbered ? ' cex-search-journey-numbered' : ''}`} aria-label="История вакансии">
    <AnimatePresence initial={false}>{events.map((event, index) => <motion.li layout={reduced ? false : 'position'} initial={{ opacity: 0, x: reduced ? 0 : 8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} key={event.id} className={event.tone === 'green' ? 'is-done' : index === events.length - 1 && !isFinished(job) ? 'is-current' : ''}>
      <span className="cex-search-step-dot">{event.tone === 'green' ? <Check size={10}/> : numbered ? index + 1 : null}</span><span>{event.label}{event.detail && <small>{event.detail}</small>}</span>
    </motion.li>)}</AnimatePresence>
  </ol>;
}

function Actions({ job, act, openCalendar }: { job: Vacancy; act: SearchProps['act']; openCalendar: () => void }) {
  const [reviewOffer, setReviewOffer] = useState(false);
  if (job.archived) return <p className="cex-search-outcome cex-search-outcome-muted"><Check size={17}/>Процесс сохранён в истории</p>;
  const action = (kind: VacancyAction) => act(job.id, kind);
  const button = (label: string, kind: VacancyAction, secondary = false, icon?: ReactNode) => <button type="button" className={secondary ? 'cex-search-button cex-search-button-secondary' : 'cex-search-button'} onClick={() => action(kind)}>{label}{icon}</button>;
  if (job.stage === 'new') return <div className="cex-search-actions">{button('Хочу участвовать', 'accept', false, <ArrowRight size={16}/>)}{button('Не подходит', 'decline', true)}</div>;
  if (job.stage === 'proposed') return <div className="cex-search-actions">{button('Подтвердить время', 'confirm', false, <Check size={16}/>)}{button('Другое время', 'reschedule', true)}</div>;
  if (job.stage === 'confirmed') return <div className="cex-search-actions">{button('Войти во встречу', 'join', false, <Video size={16}/>)}<button type="button" className="cex-search-button cex-search-button-secondary" onClick={openCalendar}><CalendarDays size={16}/>В календарь</button></div>;
  if (job.stage === 'completed') return <div className="cex-search-actions">{button('Хочу продолжить', 'continue', false, <ArrowRight size={16}/>)}{button('Не продолжать', 'decline', true)}</div>;
  if (job.stage === 'offer') return reviewOffer ? <div className="cex-search-offer-review" role="group" aria-label="Подтверждение ответа на оффер">
    <p><strong>Принимаете предложение {job.company}?</strong><span>{job.role} · {job.salary}. Команда получит ваш ответ.</span></p>
    <div className="cex-search-actions">{button('Да, принимаю', 'acceptOffer', false, <Check size={16}/>)}<button type="button" className="cex-search-button cex-search-button-secondary" onClick={() => setReviewOffer(false)}>Вернуться к условиям</button></div>
  </div> : <div className="cex-search-actions"><button type="button" className="cex-search-button" onClick={() => setReviewOffer(true)}>Принять оффер<ArrowUpRight size={16}/></button>{button('Отклонить', 'decline', true)}</div>;
  if (job.stage === 'accepted') return <p className="cex-search-outcome"><CheckCheck size={18}/>Предложение принято</p>;
  if (job.stage === 'declined') return <p className="cex-search-outcome cex-search-outcome-muted"><Check size={17}/>Ваш ответ сохранён</p>;
  return <p className="cex-search-waiting"><Clock3 size={16}/>{job.stage === 'applied' ? 'Ждём приглашения от команды' : 'Ждём решения команды'}</p>;
}

function Detail({ job, act, openCalendar, heading = true, compact = false, journey = true }: { job: Vacancy; act: SearchProps['act']; openCalendar: () => void; heading?: boolean; compact?: boolean; journey?: boolean }) {
  return <div className={`cex-search-detail${compact ? ' cex-search-detail-compact' : ''}`}>
    {heading && <><div className="cex-search-detail-top"><div className="cex-search-company"><Company job={job}/><span>{job.company}</span></div><Status job={job}/></div><h2>{job.role}</h2><Facts job={job} extended/></>}
    {compact ? <details className="cex-search-about-disclosure"><summary><span>О работе</span><ChevronDown size={15}/></summary><p>{job.description}</p></details> : <div className="cex-search-about"><h3>{job.stage === 'offer' ? 'Ваше предложение' : 'О работе'}</h3><p>{job.description}</p></div>}
    {job.stage === 'offer' && <dl className="cex-search-offer-terms"><div><dt>Доход</dt><dd>{job.salary}</dd></div><div><dt>Формат</dt><dd>{job.format}</dd></div><div><dt>Команда</dt><dd>{job.company}</dd></div></dl>}
    {journey && <Journey job={job}/>}
    <div className={`cex-search-next cex-search-next-${job.stage}`}>
      <div className="cex-search-next-copy"><span className="cex-search-eyebrow">{job.archived ? 'История вакансии' : job.stage === 'accepted' ? 'Новая глава' : needsResponse(job) ? 'Ваш следующий шаг' : 'Сейчас'}</span><h3>{statusLabel(job)}</h3><p>{job.archived ? 'Процесс перенесён в историю при остановке поиска. Все договорённости и события сохранены.' : stageCopy[job.stage]}</p></div>
      {['proposed', 'confirmed', 'completed'].includes(job.stage) && <div className="cex-search-meeting"><CalendarDays size={19}/><span><strong>{job.meetingDay} октября · {job.meetingTime}</strong><small>Онлайн · московское время</small></span></div>}
      <Actions key={`${job.id}-${job.stage}`} job={job} act={act} openCalendar={openCalendar}/>
    </div>
  </div>;
}

function Reveal({ children, active }: { children: ReactNode; active: boolean }) {
  const reduced = useReducedMotion();
  return <AnimatePresence initial={false}>{active && <motion.div className="cex-search-reveal" initial={{ opacity: 0, height: reduced ? 'auto' : 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: reduced ? 'auto' : 0 }} transition={{ duration: reduced ? 0 : .24, ease: [.2, .75, .25, 1] }}><div>{children}</div></motion.div>}</AnimatePresence>;
}

function Row({ job, selected, onSelect, number }: { job: Vacancy; selected: boolean; onSelect: () => void; number?: number }) {
  return <button type="button" className={`cex-search-row${selected ? ' is-selected' : ''}`} onClick={onSelect} aria-pressed={selected} aria-label={`${job.role}, ${job.company}. ${statusLabel(job)}. Открыть детали`}>
    {number !== undefined ? <span className="cex-search-row-number">{String(number).padStart(2, '0')}</span> : <Company job={job} small/>}
    <span className="cex-search-row-title"><span className="cex-search-row-company">{job.company}</span><strong>{job.role}</strong><span className="cex-search-row-salary">{job.salary}</span></span>
    <span className="cex-search-row-status"><Status job={job}/></span><ArrowUpRight className="cex-search-row-arrow" size={17}/>
  </button>;
}

function CardHeader({ job, selected, select, compact = false }: { job: Vacancy; selected: boolean; select: SearchProps['select']; compact?: boolean }) {
  return <button type="button" onClick={() => select(job.id)} className={`cex-search-card-trigger${compact ? ' cex-search-card-trigger-compact' : ''}`} aria-expanded={selected} aria-label={`Детали вакансии ${job.role} в ${job.company}`}>
    <span className="cex-search-card-top"><Company job={job}/><ArrowUpRight size={18}/></span><span className="cex-search-card-company">{job.company}</span><strong className="cex-search-card-role">{job.role}</strong><Facts job={job}/><Status job={job}/>
  </button>;
}

function Heading({ title, subtitle, children }: { title: string; subtitle: string; children?: ReactNode }) {
  return <header className="cex-search-heading"><div><p className="cex-search-eyebrow">Поиск работы</p><h1>{title}</h1><p className="cex-search-intro">{subtitle}</p></div>{children}</header>;
}

function Empty({ filter }: { filter: SearchFilter }) {
  return <div className="cex-search-empty"><BriefcaseBusiness size={30}/><h2>{filter === 'new' ? 'Новые возможности ещё впереди' : filter === 'process' ? 'Здесь будут ваши знакомства' : 'История пока чиста'}</h2><p>{filter === 'new' ? 'Когда появятся предложения, вы сможете сравнить их здесь.' : filter === 'process' ? 'Согласитесь участвовать в новой вакансии — и продолжите путь здесь.' : 'Завершённые и приостановленные процессы останутся в этой вкладке.'}</p></div>;
}

export function SearchVariants(props: SearchProps) {
  const { variant, vacancies, selectedId, filter, setFilter, select, act, openCalendar } = props;
  const reduced = useReducedMotion();
  const scope = useId();
  const rail = useRef<HTMLDivElement>(null);
  const jobs = vacancies.filter(job => filterFor(job) === filter);
  const selected = jobs.find(job => job.id === selectedId) || jobs[0];
  const replies = jobs.filter(needsResponse);
  const active = jobs.filter(job => !isFinished(job));
  const others = jobs.filter(job => job.id !== selected?.id);
  const detail = (job = selected, options: { heading?: boolean; compact?: boolean; journey?: boolean } = {}) => job ? <Detail job={job} act={act} openCalendar={openCalendar} {...options}/> : null;
  const row = (job: Vacancy, index?: number) => <Row key={job.id} job={job} selected={job.id === selected?.id} onSelect={() => select(job.id)} number={index}/>;
  const cards = (items: Vacancy[], className = '') => items.map(job => <motion.article layout={reduced ? false : 'position'} key={job.id} className={`cex-search-card ${className} ${job.id === selected?.id ? 'is-selected' : ''}`}><CardHeader job={job} selected={job.id === selected?.id} select={select}/><Reveal active={job.id === selected?.id}>{detail(job, { heading: false, compact: true })}</Reveal></motion.article>);
  const offerWord = new Intl.PluralRules('ru').select(jobs.length);
  const summary = `${jobs.length} ${offerWord === 'one' ? 'предложение' : offerWord === 'few' ? 'предложения' : 'предложений'} · ${replies.length} ${replies.length === 1 ? 'ждёт' : 'ждут'} вашего ответа`;
  let content: ReactNode;

  if (!selected) content = <><Heading title="Ваш следующий шаг" subtitle="Предложения, встречи и решения — в одном месте."/><Empty filter={filter}/></>;
  else if (variant === 0) content = <>
    <Heading title="Хорошая работа начинается здесь." subtitle={summary}/>
    <motion.section layout className="cex-search-focus" aria-label="Выбранная вакансия"><div className="cex-search-focus-main">{detail(selected, { journey: false })}</div><aside className="cex-search-focus-aside"><span className="cex-search-eyebrow">История знакомства</span><Journey job={selected} numbered/><p>Каждый следующий шаг — с вашим участием.</p><span className="cex-search-focus-watermark" aria-hidden="true">↗</span></aside></motion.section>
    {others.length > 0 && <section className="cex-search-other"><div className="cex-search-section-heading"><h2>Другие возможности</h2><span>{others.length}</span></div><div className="cex-search-other-grid">{others.map(job => row(job))}</div></section>}
  </>;
  else if (variant === 1) content = <>
    <Heading title="Выбирайте своё." subtitle="Сравните предложения, не теряя контекст."/>
    <div className="cex-search-split"><section className="cex-search-master" aria-label="Список вакансий"><div className="cex-search-section-heading"><h2>Все предложения</h2><span>{jobs.length}</span></div>{jobs.map(job => row(job))}</section><section className="cex-search-inspector" aria-label="Детали выбранной вакансии"><AnimatePresence mode="wait" initial={false}><motion.div key={selected.id} initial={{ opacity: 0, x: reduced ? 0 : 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: reduced ? 0 : -8 }} transition={{ duration: reduced ? 0 : .16 }}>{detail()}</motion.div></AnimatePresence></section></div>
  </>;
  else if (variant === 2) content = <>
    <Heading title="Место для нового." subtitle={summary}><span className="cex-search-heading-count">{String(active.length).padStart(2, '0')}</span></Heading>
    <div className="cex-search-mosaic">{cards(jobs, 'cex-search-mosaic-card')}</div>
  </>;
  else if (variant === 3) {
    content = <><Heading title="Ваш поиск — по делу." subtitle="Все договорённости и следующие шаги в одном списке."/>
      <section className="cex-search-ledger"><div className="cex-search-ledger-head"><span>Роль и команда</span><span>Условия</span><span>Следующий шаг</span></div>{jobs.map(job => <motion.article layout="position" key={job.id} className={`cex-search-ledger-item ${selected.id === job.id ? 'is-selected' : ''}`}><button type="button" className="cex-search-ledger-trigger" onClick={() => select(job.id)} aria-expanded={selected.id === job.id}><span className="cex-search-ledger-role"><Company job={job} small/><span><strong>{job.role}</strong><small>{job.company}</small></span></span><span className="cex-search-ledger-terms"><strong>{job.salary}</strong><small>{job.format}</small></span><Status job={job}/><ChevronDown size={17}/></button><Reveal active={selected.id === job.id}>{detail(job, { heading: false })}</Reveal></motion.article>)}</section>
    </>;
  } else if (variant === 4) content = <>
    <Heading title="У каждого предложения — свой путь." subtitle="Вы всегда знаете, где находитесь и что будет дальше."/>
    <div className="cex-search-routes">{jobs.map((job, index) => <motion.article layout="position" key={job.id} className={`cex-search-route ${job.id === selected.id ? 'is-selected' : ''}`}><div className="cex-search-route-top"><button type="button" onClick={() => select(job.id)} className="cex-search-route-name" aria-expanded={job.id === selected.id}><span className="cex-search-route-number">{String(index + 1).padStart(2, '0')}</span><Company job={job} small/><span><small>{job.company}</small><strong>{job.role}</strong></span><ArrowUpRight size={18}/></button><Journey job={job} numbered/></div><Reveal active={job.id === selected.id}><div className="cex-search-route-open"><div><Facts job={job}/><p>{job.description}</p></div>{detail(job, { heading: false, compact: true, journey: false })}</div></Reveal></motion.article>)}</div>
  </>;
  else if (variant === 5) {
    const columns = [...new Set(jobs.map(statusLabel))];
    content = <><Heading title="Поиск в движении." subtitle="Только реальные договорённости. Каждый процесс растёт вместе с вами."/><div className={`cex-search-board ${columns.length === 1 ? 'cex-search-board-single' : ''}`} style={{ '--cex-search-columns': Math.min(columns.length, 4) } as CSSProperties}>{columns.map((title, index) => { const items = jobs.filter(job => statusLabel(job) === title); return <section className="cex-search-column" key={title}><header><span className="cex-search-column-number">0{index + 1}</span><h2>{title}</h2><span>{items.length}</span></header><div className="cex-search-column-cards">{cards(items, 'cex-search-board-card')}</div></section>; })}</div></>;
  } else if (variant === 6) content = <>
    <Heading title="Всё важное. По порядку." subtitle={summary}/><div className="cex-search-accordion">{jobs.map((job, index) => <motion.article layout="position" key={job.id} className={job.id === selected.id ? 'is-selected' : ''}><h2><button type="button" aria-expanded={job.id === selected.id} aria-controls={`${scope}-${job.id}`} onClick={() => select(job.id)}><span className="cex-search-accordion-index">{String(index + 1).padStart(2, '0')}</span><span className="cex-search-accordion-name"><small>{job.company}</small><strong>{job.role}</strong></span><Status job={job}/><span className="cex-search-accordion-chevron"><ChevronDown size={21}/></span></button></h2><Reveal active={job.id === selected.id}><div id={`${scope}-${job.id}`} className="cex-search-accordion-body"><div className="cex-search-accordion-story"><Company job={job}/><Facts job={job} extended/><p>{job.description}</p><Journey job={job}/></div>{detail(job, { heading: false, compact: true, journey: false })}</div></Reveal></motion.article>)}</div>
  </>;
  else if (variant === 7) content = <>
    <header className="cex-search-editorial-heading"><span className="cex-search-eyebrow">Ваша следующая глава</span><h1>Работа.<br/><span>С продолжением.</span></h1><p>Не просто вакансии.<br/>Знакомства, которые могут стать чем-то большим.</p></header>
    <div className="cex-search-editorial"><section className="cex-search-spotlight"><div className="cex-search-spotlight-label"><span>В фокусе</span><ArrowDown size={17}/></div><div className="cex-search-spotlight-company"><Company job={selected}/><span>{selected.company}</span><Status job={selected}/></div><h2>{selected.role}</h2><Facts job={selected} extended/><p className="cex-search-spotlight-description">{selected.description}</p>{detail(selected, { heading: false, compact: true })}</section><section className="cex-search-editorial-index"><div className="cex-search-section-heading"><h2>Ваши возможности</h2><span>{jobs.length}</span></div>{jobs.map((job, i) => row(job, i + 1))}<div className="cex-search-editorial-note"><span>{replies.length}</span><p>предложения<br/>ждут вашего решения</p></div></section></div>
  </>;
  else if (variant === 8) content = <>
    <Heading title="Посмотрите, что дальше." subtitle="Выберите предложение, чтобы продолжить разговор."><div className="cex-search-rail-controls"><button type="button" aria-label="Предыдущие вакансии" onClick={() => rail.current?.scrollBy({ left: -330, behavior: reduced ? 'instant' : 'smooth' })}><ArrowLeft size={20}/></button><button type="button" aria-label="Следующие вакансии" onClick={() => rail.current?.scrollBy({ left: 330, behavior: reduced ? 'instant' : 'smooth' })}><ArrowRight size={20}/></button></div></Heading>
    <div className="cex-search-rail" ref={rail} aria-label="Предложения о работе">{jobs.map((job, index) => <motion.article layout="position" key={job.id} className={`cex-search-rail-card ${job.id === selected.id ? 'is-selected' : ''}`}><span className="cex-search-rail-number">{String(index + 1).padStart(2, '0')}</span><CardHeader job={job} selected={job.id === selected.id} select={select}/>{job.id === selected.id && <motion.div layoutId="rail-underline" className="cex-search-rail-selected"/>}</motion.article>)}</div>
    <section className="cex-search-rail-detail"><div className="cex-search-rail-context"><span className="cex-search-eyebrow">{selected.company}</span><h2>{statusLabel(selected)}</h2><p>{selected.description}</p><Journey job={selected}/></div>{detail(selected, { heading: false, compact: true, journey: false })}</section>
  </>;
  else {
    const groups = [
      { name: 'Нужен ваш ответ', subtitle: 'Решения, которые двигают поиск вперёд', jobs: replies, icon: <ArrowUpRight size={20}/>, key: 'response' },
      { name: 'Всё идёт своим чередом', subtitle: 'Договорённости и ответы от команд', jobs: jobs.filter(job => !needsResponse(job) && !isFinished(job)), icon: <Clock3 size={20}/>, key: 'waiting' },
      { name: 'История решений', subtitle: 'То, что уже определилось', jobs: jobs.filter(isFinished), icon: <CheckCheck size={20}/>, key: 'finished' },
    ];
    content = <><Heading title="Что можно сделать сегодня?" subtitle="Мы собрали следующие шаги. Решения остаются за вами."/><div className="cex-search-action-groups">{groups.filter(group => group.jobs.length > 0).map(group => <section className={`cex-search-action-group cex-search-action-group-${group.key}`} key={group.key}><header><span className="cex-search-group-icon">{group.icon}</span><div><h2>{group.name}<span>{group.jobs.length}</span></h2><p>{group.subtitle}</p></div></header><div className="cex-search-group-items">{group.jobs.map(job => <motion.article layout="position" key={job.id} className={selected.id === job.id ? 'is-selected' : ''}>{row(job)}<Reveal active={selected.id === job.id}>{detail(job, { heading: false, compact: true })}</Reveal></motion.article>)}</div></section>)}</div></>;
  }

  const filters: { value: SearchFilter; label: string }[] = [{ value: 'new', label: 'Новые' }, { value: 'process', label: 'В процессе' }, { value: 'history', label: 'История' }];
  return <MotionConfig reducedMotion="user" transition={{ duration: reduced ? 0 : .26, ease: [.2, .75, .25, 1] }}><LayoutGroup id={`${scope}-search-${variant}`}><div className={`cex-search cex-search-v${Math.max(0, Math.min(9, variant))}`}>
    <div className="cex-search-tabs" role="tablist" aria-label="Вакансии по статусу">{filters.map((item, index) => <button type="button" key={item.value} role="tab" id={`${scope}-tab-${item.value}`} aria-selected={filter === item.value} aria-controls={`${scope}-results`} tabIndex={filter === item.value ? 0 : -1} onClick={() => setFilter(item.value)} onKeyDown={event => { let next = index; if (event.key === 'ArrowRight') next = (index + 1) % filters.length; else if (event.key === 'ArrowLeft') next = (index + filters.length - 1) % filters.length; else if (event.key === 'Home') next = 0; else if (event.key === 'End') next = filters.length - 1; else return; event.preventDefault(); setFilter(filters[next].value); (event.currentTarget.parentElement?.querySelectorAll('button')[next] as HTMLButtonElement | undefined)?.focus(); }}><span>{item.label}</span><span className="cex-search-tab-count">{vacancies.filter(job => filterFor(job) === item.value).length}</span>{filter === item.value && <motion.span layoutId="active-filter" className="cex-search-tab-indicator"/>}</button>)}</div>
    <div id={`${scope}-results`} role="tabpanel" aria-labelledby={`${scope}-tab-${filter}`}>{content}</div>
  </div></LayoutGroup></MotionConfig>;
}
