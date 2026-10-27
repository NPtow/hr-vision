import { useEffect, useId, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { Dialog } from 'radix-ui';
import { ArrowLeft, ArrowUpRight, Check, ChevronDown, X } from 'lucide-react';
import { EmployerResumePreview } from '../../employer-workspace/EmployerResumePreview';
import type { CandidateAssessmentView, AssessmentCriterion, AssessmentEvidence } from '../assessment-view';
import './profile-variants.css';

export type AssessmentProfileVariant = 'brief' | 'evidence' | 'plan';
export type AssessmentProfilesProps = {
  variant: AssessmentProfileVariant;
  data: CandidateAssessmentView;
  runId?: number;
  persistFeedback?: boolean;
  realData?: boolean;
  resumeUrl?: string;
  onSource?: (evidence: AssessmentEvidence) => void;
  onInvite?: (agenda: string[]) => void;
  onReject?: (reason: string) => void;
};

type HumanFeedback = { text: string; confirmed: boolean };
type Overlay = 'resume' | 'invite' | 'reject' | null;
const spring = { type: 'spring' as const, bounce: 0, duration: .32 };

function basisLabel(criterion: AssessmentCriterion, realData = false) {
  if (criterion.state === 'unknown' && !realData) return 'В интервью пока нет ответа';
  if (criterion.basis === 'observed') return 'Действие на интервью';
  if (criterion.basis === 'narrated') return 'Рассказ о прошлом опыте';
  if (criterion.basis === 'hypothetical') return 'Ответ на ситуацию';
  if (criterion.basis === 'reflected') return 'Объяснение своего подхода';
  return 'Материалы интервью';
}

/** These views interpret an adapter contract. They never call an assessment engine or send invitations. */
export function AssessmentProfiles(props: AssessmentProfilesProps) {
  return <Profile key={`${props.variant}:${props.data.candidateId ?? props.data.candidate.name}:${props.data.applicationId ?? props.data.vacancyTitle}:${props.data.revisionId}:${props.runId ?? 0}`} {...props}/>;
}

function Profile({ variant, data, onSource, onInvite, onReject, persistFeedback = false, realData = false, resumeUrl }: AssessmentProfilesProps) {
  const reducedMotion = useReducedMotion();
  const id = useId();
  const available = data.criteria.filter(criterion => criterion.state !== 'not_applicable' && (!realData || criterion.evidence.length > 0));
  const supported = available.filter(criterion => criterion.state === 'supported');
  const gaps = available.filter(criterion => criterion.state !== 'supported');
  const [selected, setSelected] = useState(available[0]?.id ?? '');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [agenda, setAgenda] = useState<string[]>(gaps.map(criterion => criterion.id));
  const [questionEdits, setQuestionEdits] = useState<Record<string, string>>({});
  const feedbackKey = `hrv-assessment-profile-feedback:${data.candidateId ?? data.candidate.name}:${data.revisionId}`;
  const [feedback, setFeedback] = useState<Record<string, HumanFeedback>>(() => {
    if (!persistFeedback) return {};
    try { return JSON.parse(localStorage.getItem(feedbackKey) || '{}'); } catch { return {}; }
  });
  useEffect(() => {
    if (!persistFeedback) return;
    try { localStorage.setItem(feedbackKey, JSON.stringify(feedback)); } catch { /* Local annotations stay usable without storage. */ }
  }, [feedback, feedbackKey, persistFeedback]);
  const [feedbackFor, setFeedbackFor] = useState<string | null>(null);
  const [feedbackDraft, setFeedbackDraft] = useState('');
  const [overlay, setOverlay] = useState<Overlay>(null);
  const [source, setSource] = useState<AssessmentEvidence | null>(null);
  const [slot, setSlot] = useState('');
  const [reason, setReason] = useState('');
  const [outcome, setOutcome] = useState<'invite' | 'reject' | null>(null);
  const [editingQuestion, setEditingQuestion] = useState<string | null>(null);
  const active = available.find(criterion => criterion.id === selected) ?? available[0];
  const chosenQuestions = available.filter(criterion => agenda.includes(criterion.id))
    .map(criterion => questionEdits[criterion.id] || criterion.nextQuestion).filter(Boolean);
  const transition = reducedMotion ? { duration: 0 } : spring;

  function showSource(evidence: AssessmentEvidence) {
    if (onSource) onSource(evidence);
    else setSource(evidence);
  }
  function toggleAgenda(criterion: AssessmentCriterion) {
    setAgenda(current => current.includes(criterion.id) ? current.filter(item => item !== criterion.id) : [...current, criterion.id]);
  }
  function openFeedback(criterion: AssessmentCriterion) {
    setFeedbackFor(criterion.id);
    setFeedbackDraft(feedback[criterion.id]?.text ?? '');
  }
  function proposeFeedback() {
    if (!feedbackFor || !feedbackDraft.trim()) return;
    setFeedback(current => ({ ...current, [feedbackFor]: { text: feedbackDraft.trim(), confirmed: false } }));
    setFeedbackFor(null);
  }
  function feedbackBlock(criterion: AssessmentCriterion) {
    const note = feedback[criterion.id];
    return <div className="apv-feedback-area">
      {note && <div className="apv-human-note">
        <span>{note.confirmed ? 'Ваше уточнение' : 'Предложение уточнения'}</span>
        <p>{note.text}</p>
        {!note.confirmed && <button className="apv-text-button" onClick={() => setFeedback(current => ({ ...current, [criterion.id]: { ...note, confirmed: true } }))}>Подтвердить уточнение</button>}
        {note.confirmed && <small>Сохранено отдельно от исходного вывода и реплик.</small>}
      </div>}
      {feedbackFor === criterion.id ? <motion.div className="apv-feedback-editor" initial={{ opacity: 0, y: reducedMotion ? 0 : 6 }} animate={{ opacity: 1, y: 0 }} transition={transition}>
        <label htmlFor={`${id}-feedback`}>Что в этом выводе нужно уточнить?</label>
        <textarea id={`${id}-feedback`} autoFocus value={feedbackDraft} maxLength={1600} onChange={event => setFeedbackDraft(event.target.value)} placeholder="Например: этот пример был о повторной продаже, а не о новом клиенте."/>
        <div className="apv-inline-actions"><button className="apv-secondary" disabled={!feedbackDraft.trim()} onClick={proposeFeedback}>Предложить уточнение</button><button className="apv-text-button" onClick={() => setFeedbackFor(null)}>Отмена</button></div>
      </motion.div> : <button className="apv-text-button apv-correct" onClick={() => openFeedback(criterion)}>{note ? 'Изменить уточнение' : 'Уточнить этот вывод'}</button>}
    </div>;
  }
  function evidenceBlock(criterion: AssessmentCriterion, compact = false) {
    return <div className={`apv-evidence${compact ? ' apv-evidence-compact' : ''}`}>
      <p className="apv-basis">{basisLabel(criterion, realData)}</p>
      {criterion.evidence.length ? criterion.evidence.map(evidence => <div className="apv-quote" key={evidence.id}>
        <p>«{evidence.text}»</p>
        <button className="apv-source" onClick={() => showSource(evidence)}>{evidence.sourceKind === 'resume' ? 'Резюме' : 'Интервью'}{evidence.time ? ` · ${evidence.time}` : ''}<ArrowUpRight size={13} aria-hidden="true"/></button>
      </div>) : <p className="apv-no-evidence">{criterion.missingReason || 'Тема не прозвучала в доступном интервью.'}</p>}
      {criterion.limitation && <p className="apv-limitation">{criterion.limitation}</p>}
      {feedbackBlock(criterion)}
    </div>;
  }
  function openAction(action: 'invite' | 'reject') {
    setOutcome(null);
    setSlot('');
    setOverlay(action);
  }
  const actions = <div className="apv-decision-actions"><button className="apv-text-button" onClick={() => openAction('reject')}>Отказать</button>{variant !== 'plan' ? <button className="apv-primary" onClick={() => openAction('invite')}>Позвать на встречу<ArrowUpRight size={17} aria-hidden="true"/></button> : <button className="apv-primary apv-plan-mobile-invite" onClick={() => openAction('invite')}>{realData ? 'Подготовить встречу' : 'Выбрать время'}<ArrowUpRight size={17} aria-hidden="true"/></button>}</div>;
  const facts = <dl className="apv-facts">
    <div><dt>Опыт</dt><dd>{data.candidate.experience}</dd></div>
    <div><dt>Город</dt><dd>{data.candidate.city}</dd></div>
    <div><dt>Ожидания</dt><dd>{data.candidate.salary}</dd></div>
  </dl>;

  return <article className={`apv apv-${variant}`} aria-label={`Профиль: ${data.candidate.name}`}>
    <header className="apv-header">
      <div className="apv-heading">
        <div className="apv-avatar" aria-hidden="true">{data.candidate.name.split(' ').map(part => part[0]).slice(0, 2).join('')}</div>
        <div><p className="apv-eyebrow">По вакансии «{data.vacancyTitle}»</p><h1>{data.candidate.name}</h1><p className="apv-role">{data.candidate.role}</p></div>
      </div>
      <div className="apv-contact"><span>{data.candidate.contact}</span><button className="apv-secondary apv-resume-trigger" onClick={() => setOverlay('resume')}>Резюме<ArrowUpRight size={14} aria-hidden="true"/></button></div>
    </header>

    {variant === 'brief' && <div className="apv-brief-layout">
      <main className="apv-brief-main">
        <section className="apv-intro"><p className="apv-eyebrow">Для следующего решения</p><h2>На что опереться.<br/><span>О чём поговорить.</span></h2><p>Реплики из интервью помогают подготовить встречу. Выводы можно открыть, проверить и дополнить своим наблюдением.</p></section>
        <div className="apv-brief-groups">
          <section><h3>Есть материал</h3><div className="apv-brief-list">{supported.map(criterion => <div key={criterion.id} className="apv-brief-item">
            <button className="apv-finding-trigger" aria-expanded={expanded === criterion.id} onClick={() => setExpanded(expanded === criterion.id ? null : criterion.id)}><span><strong>{criterion.label}</strong><small>{basisLabel(criterion, realData)}</small></span><ChevronDown size={16} aria-hidden="true" style={{ transform: expanded === criterion.id ? 'rotate(180deg)' : undefined }}/></button>
            <p>{criterion.summary}</p>
            <AnimatePresence initial={false}>{expanded === criterion.id && <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }} transition={transition} className="apv-expand">{evidenceBlock(criterion, true)}</motion.div>}</AnimatePresence>
          </div>)}</div></section>
          <section><h3>Стоит уточнить</h3><div className="apv-gap-list">{gaps.map(criterion => <div key={criterion.id} className="apv-gap-item">
            <span className={`apv-state apv-state-${criterion.state}`}>{criterion.stateLabel}</span><h4>{criterion.label}</h4><p>{criterion.summary || (realData ? '' : criterion.missingReason)}</p>
            <button className="apv-text-button apv-gap-disclosure" aria-expanded={expanded === criterion.id} onClick={() => setExpanded(expanded === criterion.id ? null : criterion.id)}>{expanded === criterion.id ? 'Свернуть основание' : 'Основание и уточнение'}<ChevronDown size={13} aria-hidden="true" style={{ transform: expanded === criterion.id ? 'rotate(180deg)' : undefined }}/></button>
            <AnimatePresence initial={false}>{expanded === criterion.id && <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }} transition={transition} className="apv-expand">{evidenceBlock(criterion, true)}</motion.div>}</AnimatePresence>
            <button className="apv-add-question" aria-pressed={agenda.includes(criterion.id)} onClick={() => toggleAgenda(criterion)}>{agenda.includes(criterion.id) ? <><Check size={14} aria-hidden="true"/>В плане встречи</> : 'Обсудить на встрече'}</button>
          </div>)}</div></section>
        </div>
      </main>
      <aside className="apv-person-summary"><p className="apv-eyebrow">О человеке</p>{facts}<div className="apv-resume-excerpt"><h3>Профессиональный опыт</h3><p>{data.candidate.resumeText}</p><button className="apv-text-button" onClick={() => setOverlay('resume')}>Читать резюме<ArrowUpRight size={13} aria-hidden="true"/></button></div><div className="apv-next-hint"><h3>Следующий разговор</h3><p>{chosenQuestions[0] || 'Добавьте вопрос из того, что стоит уточнить.'}</p><button className="apv-text-button" onClick={() => openAction('invite')}>Посмотреть план встречи</button></div></aside>
    </div>}

    {variant === 'evidence' && <div className="apv-dossier-layout">
      <aside className="apv-dossier-index"><p className="apv-eyebrow">Что показало интервью</p><nav aria-label="Качества кандидата">{available.map(criterion => <button key={criterion.id} className={selected === criterion.id ? 'apv-selected-criterion' : ''} aria-current={selected === criterion.id ? 'true' : undefined} onClick={() => { setSelected(criterion.id); setFeedbackFor(null); }}><strong>{criterion.label}</strong><span className={`apv-state apv-state-${criterion.state}`}>{criterion.stateLabel}</span></button>)}</nav><div className="apv-index-facts">{facts}</div></aside>
      {active && <AnimatePresence mode="wait" initial={false}><motion.section key={active.id} className="apv-dossier-detail" initial={{ opacity: 0, y: reducedMotion ? 0 : 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={reducedMotion ? { duration: 0 } : { duration: .16 }}>
        <span className={`apv-state apv-state-${active.state}`}>{active.stateLabel}</span><h2>{active.label}</h2><p className="apv-dossier-summary">{active.summary}</p>{evidenceBlock(active)}
        <div className="apv-follow-up"><p className="apv-eyebrow">Проверить на следующей встрече</p><h3>{active.nextQuestion}</h3><button className="apv-secondary" aria-pressed={agenda.includes(active.id)} onClick={() => toggleAgenda(active)}>{agenda.includes(active.id) ? <><Check size={15} aria-hidden="true"/>В плане встречи</> : 'Добавить в план встречи'}</button></div>
      </motion.section></AnimatePresence>}
    </div>}

    {variant === 'plan' && <div className="apv-plan-layout">
      <main className="apv-plan-main"><section className="apv-intro"><p className="apv-eyebrow">Подготовка следующей встречи</p><h2>Продолжить там,<br/><span>где осталось неясно.</span></h2><p>Выберите, что хотите проверить. Эти вопросы станут планом встречи с кандидатом.</p></section>
        <section className="apv-plan-questions"><h3>Предлагаем обсудить</h3>{[...gaps, ...supported].map(criterion => <div key={criterion.id} className={`apv-question-card${agenda.includes(criterion.id) ? ' apv-question-chosen' : ''}`}>
          <div className="apv-question-top"><p>{criterion.label}<span className={`apv-state apv-state-${criterion.state}`}>{criterion.stateLabel}</span></p><button className="apv-question-toggle" aria-label={`${agenda.includes(criterion.id) ? 'Убрать из плана' : 'Добавить в план'}: ${criterion.label}`} aria-pressed={agenda.includes(criterion.id)} onClick={() => toggleAgenda(criterion)}>{agenda.includes(criterion.id) ? <Check size={17} aria-hidden="true"/> : <span aria-hidden="true">+</span>}</button></div>
          {editingQuestion === criterion.id ? <div className="apv-question-edit"><textarea aria-label={`Вопрос: ${criterion.label}`} autoFocus value={questionEdits[criterion.id] ?? criterion.nextQuestion} onChange={event => setQuestionEdits(current => ({ ...current, [criterion.id]: event.target.value }))}/><button className="apv-text-button" onClick={() => setEditingQuestion(null)}>Готово</button></div> : <button className="apv-question-copy" onClick={() => setEditingQuestion(criterion.id)} title="Изменить вопрос">{questionEdits[criterion.id] || criterion.nextQuestion}</button>}
          <button className="apv-text-button apv-why" aria-expanded={expanded === criterion.id} onClick={() => setExpanded(expanded === criterion.id ? null : criterion.id)}>{expanded === criterion.id ? 'Свернуть основание' : 'Почему этот вопрос'}<ChevronDown size={13} aria-hidden="true" style={{ transform: expanded === criterion.id ? 'rotate(180deg)' : undefined }}/></button>
          <AnimatePresence initial={false}>{expanded === criterion.id && <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }} transition={transition} className="apv-expand"><p className="apv-question-reason">{criterion.summary || (realData ? '' : criterion.missingReason)}</p>{evidenceBlock(criterion, true)}</motion.div>}</AnimatePresence>
        </div>)}</section>
      </main>
      <aside className="apv-agenda"><p className="apv-eyebrow">Ваш план встречи</p><h2>Разговор по делу.</h2>{chosenQuestions.length ? <ul>{chosenQuestions.map((question, index) => <li key={`${question}-${index}`}>{question}</li>)}</ul> : <p className="apv-agenda-empty">Добавьте вопросы слева. Можно пригласить и без готового плана.</p>}<button className="apv-primary" onClick={() => openAction('invite')}>{realData ? 'Подготовить встречу' : 'Выбрать время'}<ArrowUpRight size={16} aria-hidden="true"/></button><button className="apv-text-button" onClick={() => setOverlay('resume')}>Свериться с резюме</button></aside>
    </div>}

    <footer className="apv-footer"><p>{realData ? 'Локальный предпросмотр. Решение и сообщения кандидатам не отправляются.' : variant === 'plan' ? 'План остаётся редактируемым до отправки приглашения.' : 'Следующий шаг выбираете вы. Неизвестное можно проверить на встрече.'}</p>{actions}</footer>

    <Dialog.Root open={!!overlay} onOpenChange={open => { if (!open) setOverlay(null); }}>
      <Dialog.Portal><Dialog.Overlay className="apv-dialog-scrim"/><Dialog.Content className={`apv-dialog${overlay === 'resume' ? ' apv-resume-dialog' : ''}`}>
        <div className="apv-dialog-heading"><Dialog.Title>{overlay === 'resume' ? 'Резюме' : outcome ? outcome === 'invite' ? 'Приглашение подготовлено' : 'Отказ подготовлен' : overlay === 'invite' ? 'Встреча с кандидатом' : 'Ваше решение'}</Dialog.Title><Dialog.Close className="apv-close" aria-label="Закрыть"><X size={19}/></Dialog.Close></div>
        {overlay === 'resume' ? <><Dialog.Description className="apv-dialog-description">{data.candidate.name} · {data.candidate.role}</Dialog.Description>{realData && resumeUrl ? <div className="apv-resume-body apv-resume-original"><EmployerResumePreview resume={{ id: resumeUrl, name: 'Резюме.pdf', load: async signal => { const response = await fetch(resumeUrl, { signal, cache: 'no-store' }); if (!response.ok) throw new Error('Не удалось открыть оригинал резюме.'); return response.blob(); } }}/></div> : <div className="apv-resume-body"><h2>{data.candidate.name}</h2><p>{data.candidate.role}</p>{facts}<div className="apv-resume-text">{data.candidate.resumeText}</div></div>}</> : outcome ? <><Dialog.Description className="apv-dialog-description">{outcome === 'invite' ? realData ? 'Локальный план следующей встречи' : `${slot} · 30 минут` : reason}</Dialog.Description><div className="apv-outcome"><div className="apv-outcome-check"><Check size={24}/></div><h3>{realData ? 'Предпросмотр готов' : outcome === 'invite' ? 'У встречи есть цель.' : 'Причина решения сохранена.'}</h3><p>{realData ? 'Это предпросмотр. Статус кандидата не изменён, время не назначено, сообщения не отправлены.' : outcome === 'invite' ? 'Кандидат переходит в работу по вакансии. Интервью и план следующей встречи остаются рядом.' : 'Ваш комментарий связан с этим решением. Исходные материалы интервью сохраняются.'}</p><span>Предпросмотр результата · ничего не отправлено</span></div><Dialog.Close className="apv-primary apv-dialog-done">Вернуться к профилю</Dialog.Close></> : overlay === 'invite' ? <>
          <Dialog.Description className="apv-dialog-description">{realData ? 'План встречи в локальном макете. Время согласовывается отдельно; приглашение не отправляется.' : `${data.candidate.name}. Выберите время из доступных слотов кандидата.`}</Dialog.Description>{!realData && <fieldset className="apv-slots"><legend>Время встречи · МСК</legend>{['27 октября, 11:00', '27 октября, 15:30', '28 октября, 12:00'].map(time => <button key={time} className={slot === time ? 'apv-slot-selected' : ''} aria-pressed={slot === time} onClick={() => setSlot(time)}>{time}{slot === time && <Check size={14} aria-hidden="true"/>}</button>)}</fieldset>}<div className="apv-invite-agenda"><h3>План встречи</h3>{chosenQuestions.length ? <ul>{chosenQuestions.map((question, index) => <li key={`${question}-${index}`}>{question}</li>)}</ul> : <p>Свободный разговор — без заранее выбранных вопросов.</p>}</div><button className="apv-primary apv-dialog-done" disabled={!realData && !slot} onClick={() => { if (!realData) onInvite?.(chosenQuestions); setOutcome('invite'); }}>{realData ? 'Посмотреть результат' : 'Подготовить приглашение'}<ArrowUpRight size={16}/></button>
        </> : <>
          <Dialog.Description className="apv-dialog-description">Укажите причину в контексте вакансии. Отсутствие ответа в интервью само по себе не говорит о качестве кандидата.</Dialog.Description><label className="apv-reject-label" htmlFor={`${id}-reason`}>Почему не продолжаете?</label><div className="apv-reason-options">{['Не подходит опыт', 'Не совпали условия', 'Выбрали другого кандидата'].map(value => <button className={reason === value ? 'apv-reason-selected' : ''} key={value} onClick={() => setReason(value)}>{value}</button>)}</div><textarea className="apv-reject-reason" id={`${id}-reason`} value={reason} onChange={event => setReason(event.target.value)} placeholder="Коротко и по существу" maxLength={1600}/><button className="apv-primary apv-dialog-done" disabled={!reason.trim()} onClick={() => { if (!realData) onReject?.(reason.trim()); setOutcome('reject'); }}>Подготовить отказ</button>
        </>}
      </Dialog.Content></Dialog.Portal>
    </Dialog.Root>
    <Dialog.Root open={!!source} onOpenChange={open => { if (!open) setSource(null); }}>
      <Dialog.Portal><Dialog.Overlay className="apv-dialog-scrim"/><Dialog.Content className="apv-dialog apv-source-dialog"><div className="apv-dialog-heading"><Dialog.Title>Фрагмент интервью{source?.time ? ` · ${source.time}` : ''}</Dialog.Title><Dialog.Close className="apv-close" aria-label="Закрыть фрагмент"><X size={19}/></Dialog.Close></div><Dialog.Description className="apv-dialog-description">{source?.sourceKind === 'resume' ? 'Исходный фрагмент резюме' : 'Исходная реплика кандидата'}</Dialog.Description><blockquote>{source?.text}</blockquote><p className="apv-source-context">Вывод можно уточнить. Реплика остаётся исходным материалом.</p><Dialog.Close className="apv-text-button"><ArrowLeft size={15} aria-hidden="true"/>Вернуться к выводу</Dialog.Close></Dialog.Content></Dialog.Portal>
    </Dialog.Root>
  </article>;
}
