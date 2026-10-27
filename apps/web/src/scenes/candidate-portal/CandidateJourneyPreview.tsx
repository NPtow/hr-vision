import { useState, type ReactNode } from 'react';
import { ArrowLeft, ArrowRight, ArrowUpRight, Check, Mail, Mic, PhoneOff, Video, RotateCcw, Send, UserRound } from 'lucide-react';
import { Input } from '../../components/ui/input';
import { PreferencesForm } from './PreferencesForm';
import { CandidateCompletion } from './CandidateCompletion';
import type { CandidateState } from './api';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import '@fontsource-variable/inter';
import '../agency-product/theme.css';
import './candidate-portal.css';
import './candidate-cabinet.css';
import './candidate-surfaces.css';
import './candidate-journey-preview.css';

type Entry = 'guest' | 'no-email' | 'registered' | 'employer';
type Step = 'call' | 'left' | 'email' | 'inbox' | 'mail' | 'review' | 'consent' | 'home' | 'example' | 'telegram' | 'after' | 'result';
const entries: {value: Entry; label: string}[] = [
  {value: 'guest', label: 'Почта есть'}, {value: 'no-email', label: 'Почты нет'}, {value: 'registered', label: 'Кабинет есть'},
];
const localDate = (days = 0) => {
  const day = new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  const d = new Date(`${day}T12:00:00+03:00`); d.setUTCDate(d.getUTCDate() + days); return d.toISOString().slice(0, 10);
};
const interview = () => ({ id: 'preview-interview', start: `${localDate()}T09:00:00Z`, end: `${localDate()}T09:30:00Z`, recruiterName: 'Мария', status: 'completed' as const });
const fixture = (registered: boolean): CandidateState => ({
  id: 'preview-candidate', email: registered ? 'anna@example.test' : null, accountActive: registered,
  name: 'Анна Миронова', city: 'Москва', residence: '', desiredRole: 'Менеджер по продажам',
  about: 'Пять лет работаю с корпоративными клиентами. Веду сделки от первого разговора до договора, развиваю повторные продажи.',
  salaryMin: 120000, salaryMax: 160000, latestStartDate: localDate(14), employmentType: 'full-time',
  geographies: [], availability: [], consentReuse: registered, autoApprove: false, resumeReviewed: registered, searching: true,
  resume: { filename: 'Резюме.pdf', mime: 'application/pdf', size: 1000, uploadedAt: localDate(), extractedFields: ['about', 'city', 'salaryMin', 'salaryMax'] },
  interview: interview(), applications: [], videoConfigured: false, onboarding: { step: 'preferences', missingFields: [] },
});
const initialStep = (entry: Entry): Step => entry === 'registered' ? 'home' : entry === 'no-email' ? 'email' : entry === 'employer' ? 'after' : 'inbox';
const labels: Record<Step, string> = { call: 'Интервью', left: 'Выход из встречи', email: 'Добавить почту', inbox: 'После встречи', mail: 'Письмо', review: 'Проверка профиля', consent: 'Согласие', home: 'Кабинет', example: 'Пример вакансии', telegram: 'Telegram', after: 'После работодателя', result: 'Решение' };

/** Local-only walkthrough: emails, magic-link sign-in and Telegram are simulated. */
export function CandidateJourneyPreview({ entry = 'guest' }: {entry?: Entry}) {
  const [branch, setBranch] = useState(entry);
  const [run, setRun] = useState(0);
  return <CandidateJourneyPath key={`${branch}-${run}`} entry={branch} onBranch={setBranch} onRestart={() => setRun(n => n + 1)}/>;
}
function CandidateJourneyPath({entry, onBranch, onRestart}: {entry: Entry; onBranch: (value: Entry) => void; onRestart: () => void}) {
  const [step, setStep] = useState<Step>(() => initialStep(entry));
  const [person, setPerson] = useState(() => fixture(entry === 'registered' || entry === 'employer'));
  const [email, setEmail] = useState(entry === 'no-email' ? '' : 'anna@example.test');
  const [telegram, setTelegram] = useState(false);
  const [resent, setResent] = useState(false);
  const [exampleReply, setExampleReply] = useState<string | null>(null);
  const [continuing, setContinuing] = useState(false);
  function go(next: Step) { setStep(next); window.scrollTo({top: 0, behavior: 'instant'}); }
  function openFromMail() { setPerson(p => ({...p, accountActive: true, email})); go('review'); }
  const action = (text: string, onClick: () => void, secondary = false, icon?: ReactNode) => <button type="button" className={`cjp-action ${secondary ? 'cjp-secondary-action' : 'cjp-primary'}`} onClick={onClick}>{text}{icon}</button>;
  const back = (to: Step, label = 'Назад') => <button className="cjp-back" onClick={() => go(to)}><ArrowLeft size={16}/>{label}</button>;
  const page = (title: string, copy: string, children: ReactNode) => <section className="cjp-page cjp-enter"><h1>{title}</h1><p className="cjp-lead">{copy}</p>{children}</section>;
  const sample = (expanded = false) => <article className={`cjp-sample${expanded ? ' cjp-sample-expanded' : ''}`}>
    <div className="cjp-sample-meta"><span>Компания Пример</span><span className="cjp-sample-label">Пример вакансии</span></div>
    <h2>Менеджер по продажам</h2><p className="cjp-salary">120 000–160 000 ₽</p><p className="cjp-location">Москва · гибрид · полная занятость</p>
    {expanded && <div className="cjp-sample-description"><h3>Чем предстоит заниматься</h3><p>Развивать отношения с корпоративными клиентами, проводить переговоры и вести сделки до договора.</p><h3>Что важно команде</h3><p>Опыт B2B-продаж, самостоятельность в работе с клиентами и умение объяснить ценность продукта.</p></div>}
    {expanded ? <><div className="cjp-actions">{action('Мне интересно', () => setExampleReply('В настоящей вакансии после этого вы сможете согласовать встречу с компанией.'))}{action('Не подходит', () => setExampleReply('В настоящей вакансии участие завершится, а поиск других предложений продолжится.'), true)}</div>{exampleReply && <p className="cjp-example-reply" role="status">{exampleReply}</p>}<p className="cjp-sample-disclaimer">Учебная карточка. Ответы никуда не отправляются.</p></>
      : <button className="cjp-text-link" onClick={() => go('example')}>Посмотреть пример<ArrowUpRight size={17}/></button>}
  </article>;
  return <div className="cjp-preview">
    <header className="cjp-lab" aria-label="Управление макетом"><span className="cjp-lab-title">Макет · {labels[step]}</span><nav aria-label="Сценарий после встречи">{entries.map(item => <button key={item.value} aria-pressed={entry === item.value} onClick={() => onBranch(item.value)}>{item.label}</button>)}</nav><button className="cjp-reset" onClick={onRestart}><RotateCcw size={14}/>Сначала</button></header>
    <div className="agency-product-theme cp-root cp-surfaces">
      {step !== 'mail' && <header className="cp-topbar"><span className="cp-logo"><img src={logo} alt="HR Vision"/></span>{person.accountActive && <span className="cjp-account"><UserRound size={17}/>Анна</span>}</header>}
      <main className={`cp-main cjp-main${step === 'home' || step === 'example' ? ' cjp-cabinet' : ''}`}>
        {step === 'email' && page('Встреча завершилась', 'Оставьте почту — отправим ссылку на ваш кабинет.', <form className="cjp-email-form" onSubmit={e => {e.preventDefault(); setEmail(email.trim()); go('inbox');}}>
          <label htmlFor="cjp-email">Ваша почта</label><div><Input id="cjp-email" type="email" autoComplete="email" placeholder="name@example.com" required value={email} onChange={e => setEmail(e.target.value)}/><button type="submit" className="cjp-action cjp-primary">Получить ссылку<ArrowRight size={17}/></button></div>
        </form>)}
        {step === 'inbox' && page('Встреча завершилась', 'Ссылка на ваш кабинет уже в письме. Нажмите кнопку внутри — и начнём знакомство с кабинетом.', <><div className="cjp-mail-destination"><span><Mail size={22}/></span><div><strong>{email}</strong><p>Письмо от МашаДаша</p></div></div><div className="cjp-resend"><span>Письма нет? Проверьте спам.</span><button type="button" onClick={() => setResent(true)} disabled={resent}>{resent ? 'Отправили ещё раз' : 'Отправить ещё раз'}</button></div>{resent && <p className="cjp-inline-status" role="status">Новое письмо отправлено на {email}.</p>}</>)}
        {step === 'mail' && <section className="cjp-mail-view cjp-enter"><div className="cjp-mail-envelope"><span className="cjp-avatar">М</span><div><strong>МашаДаша</strong><p>Кому: {email}</p></div></div><article className="cjp-mail-letter"><img src={logo} alt="HR Vision"/><h1>Анна, спасибо за разговор</h1><p>Ваш профиль уже в кабинете. Осталось проверить данные и условия поиска.</p>{action('Открыть кабинет', openFromMail, false, <ArrowRight size={17}/>)}<p className="cjp-mail-signature">До встречи,<br/>команда МашаДаша</p></article></section>}
        {step === 'review' && <section className="cjp-page cjp-review cjp-enter"><span className="cjp-eyebrow">Ваш профиль</span><h1>Анна, всё верно?</h1><p className="cjp-lead">Данные уже заполнены из резюме. Проверьте их и условия поиска.</p><PreferencesForm initial={person} missingFields={person.onboarding.missingFields} busy={false} onSubmit={async patch => {setPerson(p => ({...p, ...patch, resumeReviewed: true})); go('consent');}}/></section>}
        {step === 'consent' && <section className="cjp-page cjp-review cjp-enter">{back('review', 'К профилю')}<CandidateCompletion candidate={person} busy={false} onOpen={() => go('review')} onConsent={async () => {setPerson(p => ({...p, consentReuse: true})); go('home');}}/></section>}
        {step === 'home' && <section className="cjp-home cjp-enter"><div className="cjp-home-heading"><span className="cjp-done"><Check size={22}/></span><div><h1>Встреча завершилась</h1><p>Новые вакансии будут появляться здесь.<br/>А пока посмотрите, как выглядит предложение.</p></div></div>{sample()}<div className={`cjp-telegram${telegram ? ' is-linked' : ''}`}><button type="button" className="cjp-telegram-button" aria-label={telegram ? 'Telegram подключён' : 'Подключить Telegram'} onClick={() => go('telegram')}><span className="cjp-telegram-icon">{telegram ? <Check size={22}/> : <Send size={22}/>}</span><span><strong>{telegram ? 'Telegram подключён' : 'Подключить Telegram'}</strong><small>{telegram ? 'Уведомления о вакансиях придут в бот' : 'Чтобы новые вакансии не прошли мимо'}</small></span>{!telegram && <ArrowUpRight size={17}/>}</button></div></section>}
        {step === 'example' && <section className="cjp-home cjp-enter">{back('home', 'К вакансиям')}{sample(true)}</section>}
        {step === 'telegram' && page(telegram ? 'Уведомления подключены' : 'МашаДаша в Telegram', telegram ? 'Новые вакансии и напоминания о встречах будут приходить в бот.' : 'Подключите бота, чтобы получать новые вакансии и напоминания о встречах.', <><div className="cjp-bot"><span className="cjp-telegram-icon"><Send size={26}/></span><strong>МашаДаша</strong><p>Вакансии, которые подходят вам.<br/>И ничего лишнего.</p>{telegram ? action('Вернуться в кабинет', () => go('home')) : action('Запустить', () => {setTelegram(true); go('home');})}</div>{!telegram && <button className="cjp-back" onClick={() => go('home')}>Позже</button>}</>)}
        {step === 'call' && <section className="cjp-call"><div className="cjp-call-heading"><span>Интервью с Марией</span><span>29:58</span></div><div className="cjp-call-person"><span>М</span><h2>Мария · рекрутер</h2></div><div className="cjp-call-actions"><span><Mic size={19}/><Video size={19}/></span><button className="cjp-action cjp-end-call" onClick={() => go('left')}><PhoneOff size={18}/>Выйти</button></div></section>}
        {step === 'left' && page('Вы вышли из встречи', 'Когда рекрутер завершит разговор, здесь появится следующий шаг.', action('Вернуться во встречу', () => go('call')))}
        {step === 'after' && page('Продолжим с этой компанией?', 'Вы встретились с работодателем по вакансии менеджера по продажам. Интересно двигаться дальше?', <div className="cjp-actions">{action('Хочу продолжить', () => {setContinuing(true); go('result');})}{action('Не продолжать', () => {setContinuing(false); go('result');}, true)}</div>)}
        {step === 'result' && page(continuing ? 'Ваш интерес сохранён' : 'Участие завершено', continuing ? 'Ждём следующего шага от компании.' : 'Поиск других предложений продолжается.', action('К вакансиям', () => go('home')))}
      </main>
    </div>
    <aside className="cjp-simulation" aria-label="События макета"><span>Только макет · без отправок</span>
      {step === 'inbox' && <button onClick={() => go('mail')}><Mail size={15}/>Посмотреть письмо<ArrowRight size={15}/></button>}
      {(step === 'call' || step === 'left') && <button onClick={() => go(initialStep(entry))}>Рекрутер завершил встречу<ArrowRight size={15}/></button>}
      {['email', 'home'].includes(step) && <button onClick={() => go('call')}>Показать завершение звонка<ArrowRight size={15}/></button>}
      {step === 'mail' && <button onClick={() => go('inbox')}>К экрану после встречи</button>}
    </aside>
  </div>;
}
