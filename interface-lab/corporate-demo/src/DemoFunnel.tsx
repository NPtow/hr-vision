import React, { useEffect, useId, useMemo, useRef, useState, type FormEvent } from 'react';
import { Dialog } from 'radix-ui';
import { motion, useReducedMotion } from 'motion/react';
import { ArrowLeft, ArrowRight, Check, MessageCircle, X } from 'lucide-react';
import { LOGO_URL } from './assets';
import { PersonPortrait } from './PersonPortrait';
import { candidateCountLabel, demoCriteria, demoTaskExamples, deriveRequirements, getDemoMatches, type DemoMatch, type DemoRequirement } from './matching-demo';
import './demo-funnel.css';

export type DemoFunnelProps = {
  open: boolean;
  setOpen: (value: boolean) => void;
  need: string;
  setNeed: (value: string) => void;
  onOpenProfile?: () => void;
};

type Stage = 'brief' | 'matches' | 'profile' | 'contact' | 'prepared';
const stageNames = ['Задача', 'Кандидаты', 'Подбор'];

function PersonAvatar({ match, large = false }: { match: DemoMatch; large?: boolean }) {
  return <PersonPortrait person={match.person} className={`df-avatar ${large ? 'df-avatar-large' : ''}`}/>;
}

function Criteria({ selected, onChange, compact = false }: { selected: string[]; onChange: (ids: string[]) => void; compact?: boolean }) {
  return <fieldset className={`df-criteria ${compact ? 'df-criteria-compact' : ''}`}>
    <legend>{compact ? 'Уточнить требования' : 'Что важно в опыте кандидата?'}</legend>
    <div className="df-chips">
      {demoCriteria.map(criterion => {
        const checked = selected.includes(criterion.id);
        return <button type="button" key={criterion.id} aria-pressed={checked} className={checked ? 'is-selected' : ''} onClick={() => onChange(checked ? selected.filter(id => id !== criterion.id) : [...selected, criterion.id])}>
          <span className="df-chip-mark" aria-hidden="true">{checked ? <Check size={14}/> : '+'}</span>{criterion.label}
        </button>;
      })}
    </div>
  </fieldset>;
}

function MatchingGraph({ requirements, matches }: { requirements: DemoRequirement[]; matches: DemoMatch[] }) {
  const reduced = useReducedMotion();
  return <div className="df-match-map" aria-label="Как задача связана с опытом кандидатов">
    <div className="df-map-origin"><span>Выбранные требования</span><div className="df-map-requirements">{requirements.length ? requirements.map(requirement => <span key={requirement.id}>{requirement.label}</span>) : <span>Уточнить требования</span>}</div></div>
    <div className="df-map-lines" aria-hidden="true"><svg viewBox="0 0 120 132" preserveAspectRatio="none">
      {[22, 66, 110].slice(0, matches.length).map((y, index) => <motion.path key={`${requirements.map(item => item.id).join('-')}-${index}`} d={`M 0 66 C 55 66 55 ${y} 120 ${y}`} initial={{ pathLength: reduced ? 1 : 0, opacity: reduced ? 1 : 0.4 }} animate={{ pathLength: 1, opacity: 1 }} transition={{ duration: reduced ? 0 : 0.5, delay: reduced ? 0 : index * 0.06 }}/>) }
    </svg></div>
    <div className="df-map-results">{matches.map(match => <div key={match.id}><span className={`df-map-dot ${match.person.color}`}/><span><strong>{match.person.name}</strong><span>{match.evidence[0].fact}</span></span></div>)}</div>
  </div>;
}

export function DemoFunnel({ open, setOpen, need, setNeed }: DemoFunnelProps) {
  const [stage, setStage] = useState<Stage>('brief');
  const [manualCriteria, setManualCriteria] = useState<string[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeEvidence, setActiveEvidence] = useState(0);
  const [contactName, setContactName] = useState('');
  const [contact, setContact] = useState('');
  const [triedBrief, setTriedBrief] = useState(false);
  const [triedContact, setTriedContact] = useState(false);
  const reduced = useReducedMotion();
  const id = useId();
  const titleRef = useRef<HTMLHeadingElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const selectedCriteria = manualCriteria ?? deriveRequirements(need).map(item => item.id);
  const results = useMemo(() => getDemoMatches(need, manualCriteria ?? undefined), [need, manualCriteria]);
  const selected = results.matches.find(match => match.id === selectedId) ?? results.matches[0];
  const stageIndex = stage === 'brief' ? 0 : stage === 'matches' || stage === 'profile' ? 1 : 2;
  const title = stage === 'brief' ? 'Какую задачу решаем?'
    : stage === 'matches' ? 'Вот кого стоит рассмотреть.'
    : stage === 'profile' ? selected?.person.name ?? 'Профиль кандидата'
    : stage === 'contact' ? 'Начнём с вашей задачи.'
    : 'Задача собрана.';

  useEffect(() => {
    setManualCriteria(null);
    setTriedBrief(false);
  }, [need]);

  useEffect(() => {
    if (!open) return;
    scrollRef.current?.scrollTo({ top: 0, behavior: 'instant' });
    titleRef.current?.focus({ preventScroll: true });
  }, [stage, open]);

  const editNeed = (value: string) => {
    setNeed(value);
    setManualCriteria(null);
    setTriedBrief(false);
  };
  const submitBrief = (event: FormEvent) => {
    event.preventDefault();
    setTriedBrief(true);
    if (need.trim().length < 8 || !results.matches.length) return;
    setStage('matches');
  };
  const openProfile = (match: DemoMatch) => {
    setSelectedId(match.id);
    setActiveEvidence(0);
    setStage('profile');
  };
  const back = () => {
    setStage(stage === 'profile' ? 'matches' : stage === 'contact' ? 'matches' : stage === 'prepared' ? 'contact' : 'brief');
  };
  const contactIsValid = /^(?:[^\s@]+@[^\s@]+\.[^\s@]+|@[\p{L}\p{N}_]{3,})$/u.test(contact.trim())
    || (/^\+?[\d\s()\-]+$/.test(contact.trim()) && contact.replace(/\D/g, '').length >= 7);
  const submitContact = (event: FormEvent) => {
    event.preventDefault();
    setTriedContact(true);
    if (contactName.trim().length < 2 || !contactIsValid) return;
    setStage('prepared');
  };

  return <Dialog.Root open={open} onOpenChange={setOpen}>
    <Dialog.Portal>
      <Dialog.Overlay asChild><motion.div className="demo-funnel-overlay" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: reduced ? 0 : 0.16 }}/></Dialog.Overlay>
      <Dialog.Content asChild aria-describedby={`${id}-description`} onOpenAutoFocus={event => {
        event.preventDefault();
        returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        titleRef.current?.focus({ preventScroll: true });
      }} onCloseAutoFocus={event => {
        event.preventDefault();
        if (returnFocusRef.current?.isConnected) returnFocusRef.current.focus({ preventScroll: true });
      }}>
        <motion.div className="demo-funnel" initial={{ opacity: 0, scale: reduced ? 1 : 0.98 }} animate={{ opacity: 1, scale: 1 }} transition={{ type: 'spring', bounce: 0, duration: reduced ? 0 : 0.28 }}>
          <header className="df-header">
            <img src={LOGO_URL} width="126" height="24" alt="HR Vision"/>
            <span className="df-demo-label">Демо</span>
            <Dialog.Close className="df-icon-button df-close" aria-label="Закрыть подбор"><X size={21}/></Dialog.Close>
          </header>
          <div className="df-scroll" ref={scrollRef}>
            <div className="df-wayfinding">
              {stage !== 'brief' ? <button type="button" className="df-back" onClick={back}><ArrowLeft size={16}/>{stage === 'profile' ? 'К кандидатам' : stage === 'prepared' ? 'Редактировать' : stage === 'contact' ? 'К кандидатам' : 'Изменить задачу'}</button> : <span/>}
              <ol aria-label="Этапы подбора">{stageNames.map((name, index) => <li key={name} aria-current={index === stageIndex ? 'step' : undefined} className={index === stageIndex ? 'is-current' : ''}>{name}</li>)}</ol>
            </div>
            <div className="df-heading">
              {stage === 'profile' && selected && <PersonAvatar match={selected} large/>}
              <div><Dialog.Title ref={titleRef} tabIndex={-1}>{title}</Dialog.Title>
                <Dialog.Description id={`${id}-description`} className={stage === 'brief' ? 'df-sr-only' : 'df-description'}>
                  {stage === 'brief' ? 'Опишите задачу и выберите важный опыт. Демо покажет связь требований с интервью вымышленных кандидатов.'
                    : stage === 'matches' ? 'Причина выбора — в опыте, который мы уже разобрали на интервью.'
                    : stage === 'profile' ? `${selected?.person.role} · ${selected?.person.exp}`
                    : stage === 'contact' ? 'Подготовим задачу и контакт. В этом демо заявка никуда не отправляется.'
                    : 'Задача и требования готовы. В демо контакты остаются на этом экране и никуда не отправляются.'}
                </Dialog.Description>
              </div>
            </div>

            {stage === 'brief' && <form className="df-brief" onSubmit={submitBrief} noValidate>
              <label className="df-field-label" htmlFor={`${id}-need`}>Задача бизнеса</label>
              <textarea id={`${id}-need`} value={need} onChange={event => editNeed(event.target.value)} placeholder="Например, вернуть клиентов и наладить повторные продажи" minLength={8} maxLength={500} rows={3} aria-invalid={triedBrief && need.trim().length < 8} aria-describedby={triedBrief && need.trim().length < 8 ? `${id}-need-error` : undefined}/>
              {triedBrief && need.trim().length < 8 && <p className="df-field-error" id={`${id}-need-error`} role="alert">Опишите задачу чуть подробнее — хотя бы в нескольких словах.</p>}
              <div className="df-examples" aria-label="Примеры задач">{demoTaskExamples.map(example => <button type="button" key={example.label} onClick={() => editNeed(example.text)}>{example.label}</button>)}</div>
              {deriveRequirements(need).length === 0 && <div className="df-clarify" role="status">Выберите близкий пример или отметьте нужный опыт ниже. Эти четыре сценария можно пройти в демо.</div>}
              <Criteria selected={selectedCriteria} onChange={setManualCriteria}/>
              {triedBrief && results.matches.length === 0 && <p className="df-field-error" role="alert">Выберите хотя бы одно требование к опыту.</p>}
              <div className="df-actions"><button type="submit" className="df-primary">Найти кандидата<ArrowRight size={18}/></button></div>
            </form>}

            {stage === 'matches' && <div className="df-results">
              <div className="df-task"><span>Ваша задача</span><p>{need}</p></div>
              <MatchingGraph requirements={results.requirements} matches={results.matches}/>
              <div className="df-result-count" role="status">{results.matches.length ? `${candidateCountLabel(results.matches.length)} с релевантным опытом` : 'Выберите требования — покажем кандидатов с таким опытом'}</div>
              <div className="df-candidate-grid">
                {results.matches.map(match => <article className="df-candidate-card" key={match.id}>
                  <div className="df-candidate-heading"><PersonAvatar match={match}/><div><h3>{match.person.name}</h3><p>{match.person.role}</p></div></div>
                  <div className="df-ready"><span/>Готов{match.person.initials === 'АМ' || match.person.initials === 'ЕО' || match.person.initials === 'МВ' || match.person.initials === 'АК' ? 'а' : ''} к разговору</div>
                  <h4>Почему предлагаем</h4>
                  <ul className="df-evidence-list">{match.evidence.slice(0, 2).map(item => <li key={item.criterionId}><Check size={16}/><span>{item.fact}</span></li>)}</ul>
                  {match.missing.length > 0 && <div className="df-missing">Уточнить: {match.missing.map(item => item.label.toLocaleLowerCase('ru-RU')).join(', ')}</div>}
                  <button className="df-secondary" type="button" onClick={() => openProfile(match)}>Смотреть интервью и оценку<ArrowRight size={16}/></button>
                </article>)}
              </div>
              <details className="df-refine"><summary>Изменить важный опыт</summary><Criteria compact selected={selectedCriteria} onChange={setManualCriteria}/></details>
              <div className="df-actions"><button type="button" className="df-primary" disabled={!results.matches.length} onClick={() => setStage('contact')}>Начать подбор под мою задачу<ArrowRight size={18}/></button></div>
            </div>}

            {stage === 'profile' && selected && <div className="df-profile">
              <div className="df-profile-grid">
                <section className="df-interview-panel" aria-label="Разобранное интервью">
                  <div className="df-panel-heading"><h3>Из интервью</h3><span>Проведено и оценено</span></div>
                  <div className="df-transcript" aria-live="polite"><span className="df-timecode">{selected.interview[activeEvidence]?.timecode}</span><blockquote>«{selected.interview[activeEvidence]?.quote}»</blockquote><span className="df-transcript-person">{selected.person.name}</span></div>
                  <div className="df-interview-chapters" aria-label="Фрагменты интервью">{selected.interview.map((item, index) => <button type="button" key={item.criterionId} className={activeEvidence === index ? 'is-active' : ''} aria-pressed={activeEvidence === index} onClick={() => setActiveEvidence(index)}><span>{item.label}</span><time>{item.timecode}</time></button>)}</div>
                </section>
                <section className="df-profile-reasons"><h3>Почему подходит к задаче</h3><ul className="df-evidence-list">{selected.evidence.map(item => <li key={item.criterionId}><Check size={18}/><span><strong>{item.label}</strong>{item.fact}</span></li>)}</ul><div className="df-follow-up"><span>Что ещё обсудить</span><p>{selected.followUp}</p>{selected.missing.length > 0 && <p>Пока не подтверждено: {selected.missing.map(item => item.label.toLocaleLowerCase('ru-RU')).join(', ')}.</p>}</div><div className="df-interest"><MessageCircle size={18}/><p>{selected.interest}</p></div></section>
              </div>
              <div className="df-task"><span>Ваша задача</span><p>{need}</p></div>
              <div className="df-actions"><button type="button" className="df-primary" onClick={() => setStage('contact')}>Начать подбор под мою задачу<ArrowRight size={18}/></button></div>
            </div>}

            {stage === 'contact' && <form className="df-contact" onSubmit={submitContact} noValidate>
              <div className="df-task"><div className="df-task-label"><span>Ваша задача</span><button type="button" onClick={() => setStage('brief')}>Изменить</button></div><p>{need}</p><div className="df-summary-chips">{results.requirements.map(requirement => <span key={requirement.id}>{requirement.label}</span>)}</div></div>
              <div className="df-contact-fields"><div><label className="df-field-label" htmlFor={`${id}-name`}>Как к вам обращаться</label><input id={`${id}-name`} value={contactName} onChange={event => setContactName(event.target.value)} placeholder="Ваше имя" autoComplete="given-name" maxLength={80} aria-invalid={triedContact && contactName.trim().length < 2} aria-describedby={triedContact && contactName.trim().length < 2 ? `${id}-name-error` : undefined}/>{triedContact && contactName.trim().length < 2 && <p className="df-field-error" id={`${id}-name-error`}>Укажите имя.</p>}</div><div><label className="df-field-label" htmlFor={`${id}-contact`}>Почта, телефон или Telegram</label><input id={`${id}-contact`} value={contact} onChange={event => setContact(event.target.value)} placeholder="you@company.ru или @username" autoComplete="off" autoCapitalize="none" spellCheck={false} maxLength={160} aria-invalid={triedContact && !contactIsValid} aria-describedby={triedContact && !contactIsValid ? `${id}-contact-error` : undefined}/>{triedContact && !contactIsValid && <p className="df-field-error" id={`${id}-contact-error`}>Введите почту, телефон или Telegram с @.</p>}</div></div>
              <div className="df-actions"><button type="submit" className="df-primary">Подготовить заявку<ArrowRight size={18}/></button></div>
            </form>}

            {stage === 'prepared' && <div className="df-prepared">
              <div className="df-prepared-check" aria-hidden="true"><Check size={30}/></div>
              <div className="df-task"><span>Ваша задача</span><p>{need}</p><div className="df-summary-chips">{results.requirements.map(requirement => <span key={requirement.id}>{requirement.label}</span>)}</div></div>
              <dl className="df-contact-summary"><div><dt>Контакт</dt><dd>{contactName}</dd></div><div><dt>Как связаться</dt><dd>{contact}</dd></div></dl>
              <div className="df-actions"><button type="button" className="df-secondary" onClick={() => setStage('contact')}>Редактировать заявку</button><button type="button" className="df-primary" onClick={() => setOpen(false)}>Вернуться на сайт</button></div>
            </div>}
          </div>
        </motion.div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
