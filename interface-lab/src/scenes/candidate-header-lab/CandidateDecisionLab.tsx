import { useEffect, useId, useRef, useState } from 'react';
import { AnimatePresence, LayoutGroup, MotionConfig, motion, useReducedMotion } from 'motion/react';
import { Tooltip } from 'radix-ui';
import { ArrowLeft, ArrowRight, CalendarDays, Check, CheckCheck, ChevronDown, RotateCcw, X } from 'lucide-react';
import { Player } from './CandidateHeaderLab';
import { InterviewAssistant } from './InterviewAssistant';
import { candidateName } from './demo-data';
import brand from '../employer-workspace/assets/hr-vision-logo.svg';
import './candidate-decision-lab.css';

type Variant = 'direct' | 'dock' | 'beside';
type Choice = 'invite' | 'reject';
type Feedback = { reason: string; missing: string; improve: string };
const variants = [
  { id: 'direct', number: '01', label: 'Открытый выбор', title: 'Решение без промежуточной кнопки', description: 'Приглашение видно сразу. Отказ раскрывает короткую форму под интервью — всё остаётся на своих местах.', motion: 'Форма плавно раскрывается из строки действий. Выбранная причина получает отчётливый отклик.' },
  { id: 'dock', number: '02', label: 'Плавающий пульт', title: 'Одно место для всего решения', description: 'Пульт закреплён внизу окна. Он расширяется вверх в форму и собирается в подтверждение после выбора.', motion: 'Контейнер меняет размер, содержимое сменяется мягким переходом. Обратный путь ведёт в тот же пульт.' },
  { id: 'beside', number: '03', label: 'Решение рядом', title: 'Смотреть и решать в одном экране', description: 'Форма занимает место расшифровки. Видео остаётся видимым; к словам кандидата можно вернуться, сохранив черновик.', motion: 'Правая панель меняет содержимое коротким горизонтальным переходом. Сама запись не сдвигается.' },
] as const;
const reasons = ['Опыт продаж', 'Уровень роли', 'Условия работы', 'Другое'];
const slots = ['22 октября · 11:00', '22 октября · 15:30', '23 октября · 12:00'];

function DecisionForm({ mode, feedback, setFeedback, slot, setSlot, onBack, onSubmit }: {
  mode: Choice; feedback: Feedback; setFeedback: (value: Feedback) => void; slot: string; setSlot: (value: string) => void;
  onBack: () => void; onSubmit: () => void;
}) {
  const id = useId();
  const [reason, setReason] = useState('');
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => { first.current?.focus({ preventScroll: true }); }, []);
  return <form className="cdl-form" aria-label={mode === 'reject' ? 'Причина отказа' : 'Время встречи'} onSubmit={event => { event.preventDefault(); onSubmit(); }} onKeyDown={event => {
    if (event.key === 'Escape') { event.stopPropagation(); onBack(); }
  }}>
    <header><button ref={first} type="button" className="cdl-back" onClick={onBack} aria-label="Вернуться к выбору"><ArrowLeft size={18}/></button><div><h3>{mode === 'reject' ? 'Что не подошло?' : 'Когда встретимся?'}</h3><p>{mode === 'reject' ? 'Обратная связь для рекрутера' : 'Свободные слоты кандидата · МСК'}</p></div></header>
    {mode === 'reject' ? <>
      <div className="cdl-reasons" aria-label="Тема обратной связи">{reasons.map(item => <button type="button" key={item} aria-pressed={reason === item} onClick={() => { setReason(reason === item ? '' : item); document.getElementById(`${id}-reason`)?.focus(); }}>{reason === item && <Check size={13}/>} {item}</button>)}</div>
      <label htmlFor={`${id}-reason`} className="cdl-sr">Причина отказа</label>
      <textarea id={`${id}-reason`} required maxLength={4000} value={feedback.reason} onChange={event => setFeedback({ ...feedback, reason: event.target.value })}
        placeholder={reason === 'Опыт продаж' ? 'Какой опыт нужен для этой вакансии?' : reason === 'Условия работы' ? 'Какие условия не совпали?' : 'Например: нужен опыт самостоятельного поиска B2B-клиентов…'}/>
      <details className="cdl-extra"><summary>Дополнить обратную связь<ChevronDown size={15}/></summary>
        <label>Какого опыта или навыков не хватило?<textarea value={feedback.missing} maxLength={4000} onChange={event => setFeedback({ ...feedback, missing: event.target.value })}/></label>
        <label>Что изменить в следующих кандидатах?<textarea value={feedback.improve} maxLength={4000} onChange={event => setFeedback({ ...feedback, improve: event.target.value })}/></label>
      </details>
      <div className="cdl-form-bottom"><span>Причина останется в истории подбора</span><button type="submit" className="cdl-confirm cdl-confirm-reject" disabled={!feedback.reason.trim()}>Сохранить отказ<ArrowRight size={17}/></button></div>
    </> : <>
      <div className="cdl-slots" role="group" aria-label="Свободное время">{slots.map(item => <button key={item} type="button" aria-pressed={slot === item} onClick={() => setSlot(item)}><CalendarDays size={17}/>{item}<span>{slot === item ? <Check size={16}/> : <i/>}</span></button>)}</div>
      <p className="cdl-invite-note">Кандидат перейдёт в работу по вакансии. Интервью и назначенная встреча будут рядом в его истории.</p>
      <div className="cdl-form-bottom"><span>30 минут · Видеовстреча</span><button type="submit" className="cdl-confirm" disabled={!slot}>Позвать на встречу<ArrowRight size={17}/></button></div>
    </>}
  </form>;
}

function Receipt({ choice, feedback, slot }: { choice: Choice; feedback: Feedback; slot: string }) {
  return <div className={`cdl-receipt cdl-receipt-${choice}`} role="status">
    <motion.div className="cdl-receipt-icon" initial={{ opacity: 0 }} animate={{ opacity: 1 }}><CheckCheck size={25}/></motion.div>
    <div><h3>{choice === 'reject' ? 'Отказ сохранён' : 'Встреча выбрана'}</h3><p>{choice === 'reject' ? feedback.reason : `${slot} МСК`}</p>{choice === 'reject' && feedback.improve && <small>Для следующих кандидатов: {feedback.improve}</small>}<small>Демонстрация · ничего не отправлено</small></div>
  </div>;
}

function DecisionSurface({ variant, mode, setMode, feedback, setFeedback, slot, setSlot, saved, onSubmit }: {
  variant: Variant; mode: Choice | null; setMode: (mode: Choice | null) => void;
  feedback: Feedback; setFeedback: (value: Feedback) => void; slot: string; setSlot: (value: string) => void;
  saved: Choice | null; onSubmit: () => void;
}) {
  const reduce = useReducedMotion();
  const surface = useRef<HTMLDivElement>(null);
  const restoreFocus = useRef(false);
  const state = saved ? 'saved' : mode || 'choose';
  return <motion.div ref={surface} layout={!reduce && variant !== 'beside'} transition={{ layout: { type: 'spring', stiffness: 360, damping: 36 } }}
    className={`cdl-surface cdl-surface-${variant}${mode ? ' cdl-expanded' : ''}${saved ? ' cdl-saved' : ''}`} style={{ borderRadius: variant === 'dock' && !mode && !saved ? 32 : 20 }}>
    <AnimatePresence initial={false} mode="wait">
      <motion.div key={state} className="cdl-surface-content" initial={{ opacity: 0, transform: reduce ? 'none' : `translate${variant === 'beside' ? 'X' : 'Y'}(8px)` }} animate={{ opacity: 1, transform: 'translate(0, 0)' }} exit={{ opacity: 0, transform: reduce ? 'none' : 'translateY(-4px)' }} transition={{ duration: reduce ? 0 : .16 }}>
        {saved ? <Receipt choice={saved} feedback={feedback} slot={slot}/> : mode ? <DecisionForm mode={mode} feedback={feedback} setFeedback={setFeedback} slot={slot} setSlot={setSlot} onBack={() => { restoreFocus.current = true; setMode(null); }} onSubmit={onSubmit}/> :
          <div className="cdl-choice-row">
            {variant !== 'dock' && <div className="cdl-choice-copy"><span>{variant === 'beside' ? 'Следующий шаг' : 'Продолжим знакомство?'}</span>{variant === 'beside' && <h3>Подходит для<br/>вашей команды?</h3>}</div>}
            <div className="cdl-choice-buttons"><button type="button" className="cdl-decline" onClick={() => setMode('reject')}><X size={17}/><span>Отказать</span></button><button type="button" className="cdl-invite" ref={button => { if (button && restoreFocus.current) { restoreFocus.current = false; button.focus({ preventScroll: true }); } }} onClick={() => setMode('invite')}><CalendarDays size={18}/><span>Позвать на встречу</span><ArrowRight size={18}/></button></div>
          </div>}
      </motion.div>
    </AnimatePresence>
  </motion.div>;
}

function CandidateScene({ variant, searchMode }: { variant: Variant; searchMode: 'example' | 'jev' }) {
  const [mode, setMode] = useState<Choice | null>(null);
  const [feedback, setFeedback] = useState<Feedback>({ reason: '', missing: '', improve: '' });
  const [slot, setSlot] = useState('');
  const [saved, setSaved] = useState<Choice | null>(null);
  const [current, setCurrent] = useState(35);
  const [playing, setPlaying] = useState(false);
  function choose(value: Choice | null) { setMode(value); if (value) setPlaying(false); }
  const decision = <DecisionSurface variant={variant} mode={mode} setMode={choose} feedback={feedback} setFeedback={setFeedback} slot={slot} setSlot={setSlot} saved={saved} onSubmit={() => { if (mode === 'reject' ? feedback.reason.trim() : slot) { setSaved(mode); setMode(null); } }}/>
  return <LayoutGroup><section className={`chl-product-frame cdl-product cdl-variant-${variant}`}>
    <div className="chl-product-top"><img src={brand} alt="HR Vision"/><span>Менеджер по продажам B2B <span>/</span> Кандидаты</span></div>
    <article className="chl-candidate chl-variant-overlay cdl-candidate" aria-label={candidateName}>
      <div className="chl-content"><div className="chl-video-column"><Player overlay current={current} setCurrent={setCurrent} playing={playing} setPlaying={setPlaying}/><div className="cdl-video-caption">Интервью с рекрутером</div></div>
        <InterviewAssistant key={searchMode} current={current} onSeek={setCurrent} searchMode={searchMode} decision={variant === 'beside' ? decision : undefined}/>
      </div>
      {variant === 'direct' && <div className="cdl-inline">{decision}</div>}
    </article>
    {variant === 'dock' && <div className="cdl-dock-anchor">{decision}</div>}
  </section></LayoutGroup>;
}

export function CandidateDecisionLab({ initialVariant = 'direct' }: { initialVariant?: Variant }) {
  const [variant, setVariant] = useState<Variant>(initialVariant);
  const [generation, setGeneration] = useState(0);
  const [searchMode, setSearchMode] = useState<'example' | 'jev'>('example');
  const option = variants.find(item => item.id === variant)!;
  return <MotionConfig reducedMotion="user"><Tooltip.Provider delayDuration={400}><main className="chl-lab cdl-lab">
    <header className="cdl-lab-controls"><div className="cdl-selector" role="group" aria-label="Варианты решения">{variants.map(item => <button key={item.id} type="button" aria-pressed={variant === item.id} onClick={() => setVariant(item.id)}><span>{item.number}</span>{item.label}</button>)}</div><span className="cdl-demo-label">Макет · условные данные</span><button type="button" className="cdl-reset" aria-label="Сбросить демонстрацию" title="Сбросить демонстрацию" onClick={() => setGeneration(v => v + 1)}><RotateCcw size={17}/></button></header>
    <CandidateScene key={`${variant}-${generation}`} variant={variant} searchMode={searchMode}/>
    <footer className="cdl-lab-note"><p>{option.description}</p><div><label>Поиск: <select aria-label="Режим поиска" value={searchMode} onChange={event => setSearchMode(event.target.value as 'example' | 'jev')}><option value="example">Готовые примеры</option><option value="jev">Jev · проверить доступ</option></select></label><span>{searchMode === 'example' ? 'Ответы — примеры; Jev ожидает платный доступ.' : 'Реальный запрос к Jev 1.14 через AI Gateway.'} Видео и отправка решения — демонстрация.</span></div></footer>
  </main></Tooltip.Provider></MotionConfig>;
}
