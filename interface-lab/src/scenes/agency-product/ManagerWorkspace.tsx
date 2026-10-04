import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, CalendarDays, Check, CheckCheck, ChevronRight, CircleHelp, FileText, Layers3, MapPin, Search, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog';
import { candidates as initialCandidates, type CandidateId, type JourneyProps } from './model';

type Section = 'tasks' | 'shortlist' | 'meetings';
export function ManagerWorkspace({ state, update, notify }: JourneyProps) {
  const sharedProfile = state.profileSaved && state.shareConfirmed;
  const candidates = initialCandidates.map(c => c.id === 'anna' && sharedProfile ? { ...c, name: state.profile.name, salary: state.profile.expectations, availability: state.profile.availability } : c);
  const [section, setSection] = useState<Section>('tasks');
  const [selected, setSelected] = useState<CandidateId>('anna');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [mobileDetail, setMobileDetail] = useState(false);
  const [dialog, setDialog] = useState<'decline' | 'fragment' | null>(null);
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [fragment, setFragment] = useState(0);
  const person = candidates.find(c => c.id === selected)!;
  const decision = state.decisions[selected];
  const confirmed = candidates.filter(c => c.id === 'anna' && state.decisions[c.id].kind === 'invite' && state.candidateInterest === 'interested' && state.candidateReply === 'confirmed' && state.meetingSlot);
  const heading = useRef<HTMLHeadingElement>(null);
  const previousSection = useRef(section);
  useEffect(() => {
    if (section === 'meetings' && !confirmed.length) { setSection('shortlist'); return; }
    if (previousSection.current !== section) { heading.current?.focus(); previousSection.current = section; }
  }, [section, confirmed.length]);
  const listed = candidates.filter(c => c.name.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru')) && (filter === 'all' || state.decisions[c.id].kind === filter));
  function decide(kind: 'invite' | 'review' | 'decline', reason?: string) {
    update({ decisions: { ...state.decisions, [selected]: { ...decision, kind, reason } }, ...(selected === 'anna' ? { candidateReply: 'pending', meetingSlot: '' } : {}) });
    notify(kind === 'invite' ? 'Ваш интерес отмечен. Теперь нужно согласовать встречу с кандидатом.' : kind === 'decline' ? 'Решение сохранено вместе с причиной.' : 'Кандидат снова на рассмотрении.');
  }
  function openDialog(value: typeof dialog) { setDialog(value); setText(''); setError(''); }
  const choose = (id: CandidateId) => { setSelected(id); setMobileDetail(true); };
  function saveDialog() {
    if (!text.trim()) { setError('Укажите, что не подошло. Это поможет уточнить подбор.'); return; }
    if (dialog === 'decline') decide('decline', text.trim());
    setDialog(null);
  }
  return <div className="ag-manager ag-manager-task-first">
    <main className="ag-manager-main">
      {section === 'tasks' ? <section className="ag-task-home">
        <div className="ag-task-home-heading"><span className="ag-kicker">СФЕРА · ВАША КОМАНДА</span><h1 ref={heading} tabIndex={-1}>Задачи найма</h1><p>Выберите задачу, чтобы рассмотреть кандидатов.</p></div>
        <article className="ag-hiring-task">
          <div className="ag-task-index" aria-hidden="true">01</div>
          <div className="ag-task-description"><span className="ag-kicker">КЛИЕНТСКИЙ ОТДЕЛ</span><h2>Вернуть клиентов <br/>к повторным покупкам</h2><p>Менеджер по работе с клиентами</p><span className="ag-task-progress">{confirmed.length ? 'Есть назначенная встреча' : 'Подборка готова к рассмотрению'} · {candidates.length} кандидата</span></div>
          <Button onClick={() => setSection('shortlist')}>Открыть подборку<ArrowRight size={16}/></Button>
        </article>
      </section> : <>
      <nav className="ag-task-breadcrumb" aria-label="Где вы находитесь"><button onClick={() => setSection('tasks')}>Задачи найма</button><ChevronRight size={13}/>{section === 'meetings' ? <><button onClick={() => setSection('shortlist')}>Подборка</button><ChevronRight size={13}/><span aria-current="page">Назначенная встреча</span></> : <span aria-current="page">Подборка</span>}</nav>
      <div className="ag-heading"><div><h1 ref={heading} tabIndex={-1}>{section === 'meetings' ? 'Назначенная встреча' : 'Менеджер по работе с клиентами'}</h1><p>Задача: вернуть клиентов к повторным покупкам.</p></div></div>
      {section === 'shortlist' && <details className="ag-inline-brief"><summary>Задача и условия</summary><div><p>После первой сделки часть клиентов перестаёт покупать. Нужен человек, который разберётся в причинах и выстроит регулярную работу с базой.</p><dl><div><dt>Результат</dt><dd>План развития базы, повторные контакты и измеримый результат в CRM.</dd></div><div><dt>Что важно</dt><dd>Развитие B2B-клиентов, переговоры и ответственность за результат.</dd></div><div><dt>Условия</dt><dd>150–180 тыс. ₽ на руки + бонус · Москва · гибрид</dd></div><div><dt>KPI</dt><dd>Количественные цели согласуем перед оффером.</dd></div></dl></div></details>}
      {section === 'shortlist' && confirmed.length > 0 && <div className="ag-confirmed-meeting"><CalendarDays size={19}/><div><strong>Встреча с {confirmed[0].name}</strong><p>{state.meetingSlot}</p></div><Button variant="outline" onClick={() => setSection('meetings')}>Открыть встречу<ArrowRight size={15}/></Button></div>}
      {section === 'shortlist' && <>
        <div className="ag-section-heading"><h2>Первая подборка <span>3</span></h2><span><span className="ag-dot"/>Подготовлена для вашей задачи</span></div>
        <div className={`ag-shortlist ${mobileDetail ? 'detail-open' : ''}`}>
          <section className="ag-list" aria-label="Кандидаты">
            <label className="ag-search"><Search size={16}/><Input aria-label="Найти кандидата" value={query} onChange={e => setQuery(e.target.value)} placeholder="Найти кандидата"/></label>
            <div className="ag-list-filters" role="group" aria-label="Статус рассмотрения">{[['all','Все'],['review','На рассмотрении'],['invite','Приглашены']].map(([value,label]) => <button key={value} aria-pressed={filter === value} className={filter === value ? 'active' : ''} onClick={() => setFilter(value)}>{label}</button>)}</div>
            <div className="ag-people">{listed.map(c => <button key={c.id} className={`ag-person ${selected === c.id ? 'selected' : ''}`} aria-pressed={selected === c.id} onClick={() => choose(c.id)} aria-label={`Открыть: ${c.name}`}>
              <div className="ag-person-top"><span className={`ag-avatar ${c.id}`}>{c.initials}</span><div><strong>{c.name}</strong><span>{c.experience} · {c.city}</span></div><ChevronRight size={16}/></div>
              <p>{c.summary}</p><div className="ag-person-bottom"><span>{c.salary}</span><span className={`ag-mini-status ${state.decisions[c.id].kind}`}>{state.decisions[c.id].kind === 'invite' ? 'На встречу' : state.decisions[c.id].kind === 'decline' ? 'Не подходит' : 'Рассмотреть'}</span></div>
            </button>)}</div>
            {!listed.length && <div className="ag-empty"><Search size={22}/><strong>Нет кандидатов</strong><p>Попробуйте другое имя или статус.</p><Button variant="outline" onClick={() => { setQuery(''); setFilter('all'); }}>Сбросить фильтры</Button></div>}
            <div className="ag-list-foot"><CheckCheck size={15}/><span>У каждого есть материалы интервью.<br/>Неизвестное отмечено отдельно.</span></div>
          </section>
          <section className="ag-person-detail" aria-label={`Карточка: ${person.name}`} key={person.id}>
            <button className="ag-mobile-back" onClick={() => setMobileDetail(false)}><ArrowLeft size={16}/>К подборке</button>
            <div className="ag-detail-intro"><span className={`ag-avatar large ${person.id}`}>{person.initials}</span><div><div className="ag-detail-name"><h2>{person.name}</h2><span className="ag-new-label">В подборке</span></div><p>{person.role}</p><div className="ag-detail-meta"><span><MapPin size={13}/>{person.city}</span><span>{person.experience}</span></div></div></div>
            <Tabs defaultValue="overview" className="ag-detail-tabs">
              <TabsList aria-label="Материалы кандидата"><TabsTrigger value="overview">Обзор</TabsTrigger><TabsTrigger value="experience">Опыт</TabsTrigger><TabsTrigger value="conditions">Условия</TabsTrigger></TabsList>
              <TabsContent value="overview">
                <div className="ag-why"><span className="ag-kicker"><Layers3 size={14}/>ПОЧЕМУ ПРЕДЛАГАЕМ</span><h3>{person.summary}</h3><div className="ag-tags">{person.tags.map(tag => <span key={tag}>{tag}</span>)}</div></div>
                <div className="ag-subheading"><h3>На чём основана рекомендация</h3><span>Из интервью</span></div>
                <div className="ag-evidence">{person.evidence.map((item,index) => <button key={item.title} aria-label={`Прочитать фрагмент: ${item.title}`} onClick={() => { setFragment(index); openDialog('fragment'); }}><span className="ag-evidence-icon"><FileText size={17}/></span><div><strong>{item.title}</strong><p>{item.detail}</p><span>{item.source}</span></div><ChevronRight size={17}/></button>)}</div>
                <div className="ag-unknown"><CircleHelp size={17}/><div><strong>Что стоит уточнить</strong><p>{person.unknown}</p></div></div>
              </TabsContent>
              <TabsContent value="experience"><div className="ag-tab-body"><h3>Опыт работы</h3><p className="ag-muted">Сведения из профиля кандидата. Рекомендации работодателей ещё не запрашивали.</p><div className="ag-history">{person.history.map(job => <div key={job.company}><span className="ag-company-mark">{job.company[0]}</span><div><strong>{job.company}</strong><p>{job.role}</p><span>{job.years}</span></div></div>)}</div><h3>Что ищет в новой роли</h3><p>{person.motivation}</p></div></TabsContent>
              <TabsContent value="conditions"><div className="ag-tab-body"><h3>Ожидания кандидата</h3><dl className="ag-facts"><div><dt>Доход на руки</dt><dd>{person.salary}{!(person.id === 'anna' && sharedProfile) && ' + бонус'}</dd></div><div><dt>Готовность к переходу</dt><dd>{person.availability}</dd></div><div><dt>Формат</dt><dd>Гибрид · обсуждается</dd></div><div><dt>Интерес к вашей роли</dt><dd>{person.id === 'anna' ? state.candidateInterest === 'interested' ? 'Подтверждён кандидатом' : state.candidateInterest === 'declined' ? 'Кандидат отказался от роли' : 'Уточняем' : 'Уточняем'}</dd></div></dl><div className="ag-unknown"><CircleHelp size={17}/><p>Условия и доступность нужно подтвердить перед встречей. Кандидат может рассматривать другие предложения.</p></div></div></TabsContent>
            </Tabs>
            {selected === 'anna' && sharedProfile && <div className="ag-saved-note"><FileText size={15}/><div><strong>Кандидат обновил профиль</strong><p>{state.profile.experience}</p><small>Самоописание кандидата. Оценка требует отдельной проверки.</small></div></div>}
            {decision.kind !== 'review' && <div className={`ag-decision ${decision.kind}`} role="status"><span>{decision.kind === 'invite' ? <Check size={18}/> : <X size={18}/>}</span><div><strong>{decision.kind === 'invite' ? 'Вы хотите встретиться' : 'Не подходит для этой роли'}</strong><p>{decision.kind === 'decline' ? decision.reason : selected === 'anna' && state.candidateInterest === 'declined' ? 'Кандидат отказался от роли. Можно вернуться к другим людям в подборке.' : selected === 'anna' && state.candidateReply === 'confirmed' ? `Встреча согласована: ${state.meetingSlot}` : selected === 'anna' && state.candidateReply === 'reschedule' ? 'Кандидат попросил другое время. Координатор уточнит доступность.' : selected === 'anna' && state.candidateInterest === 'interested' ? 'Кандидату интересна роль. Следующий шаг: согласовать время.' : 'Интерес кандидата и время встречи ещё нужно подтвердить.'}</p></div></div>}
            <footer className="ag-decision-bar">{decision.kind === 'review' ? <><Button onClick={() => decide('invite')}><CalendarDays size={16}/>Пригласить на встречу</Button><Button variant="ghost" onClick={() => openDialog('decline')}>Не подходит</Button></> : <><Button variant="outline" onClick={() => decide('review')}>Вернуть к рассмотрению</Button>{confirmed.some(c => c.id === selected) && <Button onClick={() => setSection('meetings')}>Открыть встречу<ArrowRight size={15}/></Button>}</>}</footer>
          </section>
        </div>
      </>}
      {section === 'meetings' && <section className="ag-full-section">{confirmed.map(c => <article className="ag-meeting" key={c.id}><div className="ag-meeting-person"><span className={`ag-avatar ${c.id}`}>{c.initials}</span><div><h2>{c.name}</h2><p>{c.role}</p></div></div><div className="ag-meeting-state"><CalendarDays size={18}/><span>{state.meetingSlot}</span></div><p className="ag-muted">Время подтверждено обеими сторонами.</p><p className="ag-meeting-not-connected">Видеокомната пока не подключена.</p><Button variant="outline" onClick={() => { choose(c.id); setSection('shortlist'); }}>Вернуться к кандидату<ArrowRight size={15}/></Button></article>)}</section>}
      </>}
    </main>
    <Dialog open={dialog !== null} onOpenChange={open => { if (!open) setDialog(null); }}><DialogContent><DialogHeader><DialogTitle>{dialog === 'fragment' ? person.evidence[fragment]?.title : 'Что не подошло?'}</DialogTitle><DialogDescription>{dialog === 'fragment' ? `${person.name} · ${person.evidence[fragment]?.source}. Демонстрационный текст, не запись реального интервью.` : `Причина отказа по ${person.name} поможет сделать следующую подборку точнее.`}</DialogDescription></DialogHeader>{dialog === 'fragment' ? <><blockquote className="ag-fragment">«{person.evidence[fragment]?.fragment}»</blockquote><p className="ag-muted">Это слова кандидата. Результаты ещё не подтверждены независимыми источниками.</p><DialogFooter><Button variant="outline" onClick={() => setDialog(null)}>Понятно</Button></DialogFooter></> : <><label htmlFor="manager-note">Причина<Textarea id="manager-note" value={text} onChange={e => { setText(e.target.value); setError(''); }} placeholder="Например, нужен опыт в нашей отрасли" aria-invalid={!!error} aria-describedby={error ? 'manager-note-error' : undefined}/></label>{error && <p id="manager-note-error" className="ag-error" role="alert">{error}</p>}<DialogFooter><Button variant="outline" onClick={() => setDialog(null)}>Отмена</Button><Button onClick={saveDialog}>Сохранить решение</Button></DialogFooter></>}</DialogContent></Dialog>
  </div>;
}
