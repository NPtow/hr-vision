import { useState } from 'react';
import {
  ArrowRight,
  ArrowLeft,
  CalendarDays,
  Check,
  FileText,
  Home,
  LogOut,
  Mail,
  Monitor,
  RotateCcw,
  Smartphone,
  Sparkles,
  UserRound,
  Video,
  X,
} from 'lucide-react';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import { Button } from '../../components/ui/button';
import { CandidateSchedule } from './CandidateSchedule';
import type { CandidateState } from './api';
import './booking-portal.css';
import './candidate-portal.css';
import './candidate-cabinet.css';
import './candidate-schedule.css';

interface MockProps {
  initialStep?: 'booking' | 'calendar' | 'debrief';
}

type TelegramState = 'default' | 'linked' | 'declined';
type FlowStep = 'booking' | 'calendar' | 'debrief';

export function CandidateFlowMocks({ initialStep = 'booking' }: MockProps) {
  const [step, setStep] = useState<FlowStep>(initialStep);
  const [viewport, setViewport] = useState<'desktop' | 'mobile'>('desktop');
  const [tgState, setTgState] = useState<TelegramState>('default');
  const [isOpeningTg, setIsOpeningTg] = useState(false);
  const [onboardingStep, setOnboardingStep] = useState<1 | 2 | 3 | null>(1);
  const [cabinetTab, setCabinetTab] = useState<'calendar' | 'vacancies'>('calendar');

  const candidateEmail = 'alexey.smirnov@example.com';
  const botUsername = 'mashadasha_bot';
  const bookingToken = 'bk_e8b91a0c4f3912ad';
  const tgDeepLink = `https://t.me/${botUsername}?start=${bookingToken}`;

  const meetingDate = new Date(Date.now() + 24 * 3600 * 1000);
  meetingDate.setHours(14, 0, 0, 0);
  const meetingEndDate = new Date(meetingDate.getTime() + 30 * 60 * 1000);

  const formattedDate = meetingDate.toLocaleDateString('ru-RU', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  });

  const formattedTime = `${meetingDate.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })} – ${meetingEndDate.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}`;

  const mockCandidate: CandidateState = {
    id: 'cand_alexey_smirnov',
    name: 'Алексей Смирнов',
    city: 'Москва',
    desiredRole: 'Lead Product Manager',
    desiredSalary: 'от 320 000 ₽',
    workMode: 'remote',
    about: '8+ лет в управлении B2B продуктами. CustDev, метрики, unit-экономика.',
    telegram: tgState === 'linked' ? '@alexey_smirnov' : undefined,
    geographies: ['Москва', 'Удалённо'],
    availability: [],
    consentReuse: true,
    autoApprove: false,
    salaryMin: 320000,
    salaryMax: 400000,
    latestStartDate: '2026-11-01',
    residence: 'Москва',
    employmentType: 'full-time',
    resumeReviewed: true,
    email: candidateEmail,
    timezone: 'Europe/Moscow',
    searching: true,
    videoConfigured: true,
    onboarding: { step: 'complete', missingFields: [] },
    interview: {
      id: 'recruiter_call_01',
      start: meetingDate.toISOString(),
      end: meetingEndDate.toISOString(),
      recruiterName: 'HR Vision',
      status: step === 'debrief' ? 'completed' : 'booked',
    },
    applications: [
      {
        id: 'app_fintech_lead',
        status: 'pending',
        approvedAutomatically: false,
        meeting: null,
        vacancy: {
          id: 'vac_fintech_01',
          title: 'Lead Product Manager',
          company: 'Fintech Solutions',
          city: 'Москва',
          remote: true,
          salary: '350 000 – 420 000 ₽',
          description: 'Развитие ключевого продукта платформы, CustDev и команда из 8 инженеров.',
        },
      },
    ],
    resume: {
      filename: 'Резюме_Алексей_Смирнов_Product.pdf',
      mime: 'application/pdf',
      size: 245000,
      uploadedAt: Date.now() - 3600000,
      extractedFields: ['desiredRole', 'city', 'salaryMin'],
    },
    channels: {
      telegram: {
        configured: true,
        linked: tgState === 'linked',
        canLink: true,
        status: 'active',
      },
      email: {
        configured: true,
        verified: true,
        status: 'active',
        address: candidateEmail,
      },
    },
  };

  function handleConnectTg() {
    setIsOpeningTg(true);
    window.open(tgDeepLink, '_blank', 'noopener,noreferrer');
    setTimeout(() => {
      setTgState('linked');
      setIsOpeningTg(false);
    }, 600);
  }

  function handleDeclineTg() {
    setTgState('declined');
  }

  function handleReset() {
    setTgState('default');
    setIsOpeningTg(false);
    setOnboardingStep(1);
    setCabinetTab('calendar');
  }

  return (
    <div className="agency-product-theme bp-root" data-viewport={viewport} style={{ minHeight: '100vh', background: '#f8f8fb' }}>
      {/* Test Bench Controls */}
      <div
        style={{
          background: '#fff',
          borderBottom: '1px solid #e5e2ee',
          padding: '10px 20px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '12px',
          flexWrap: 'wrap',
          position: 'sticky',
          top: 0,
          zIndex: 50,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <button
            type="button"
            className="bp-btn-pill"
            style={{
              background: step === 'booking' ? '#105aaf' : '#f2f1f6',
              color: step === 'booking' ? '#fff' : '#373340',
            }}
            onClick={() => setStep('booking')}
          >
            1. Подтверждение записи
          </button>
          <button
            type="button"
            className="bp-btn-pill"
            style={{
              background: step === 'calendar' ? '#105aaf' : '#f2f1f6',
              color: step === 'calendar' ? '#fff' : '#373340',
            }}
            onClick={() => {
              setStep('calendar');
              if (onboardingStep === null) setOnboardingStep(1);
            }}
          >
            2. Календарь + Онбординг
          </button>
          <button
            type="button"
            className="bp-btn-pill"
            style={{
              background: step === 'debrief' ? '#105aaf' : '#f2f1f6',
              color: step === 'debrief' ? '#fff' : '#373340',
            }}
            onClick={() => setStep('debrief')}
          >
            3. Итоги интервью
          </button>

          {step === 'calendar' && (
            <button
              type="button"
              className="bp-btn-pill"
              style={{ background: '#f5f3ff', color: '#6d28d9', borderColor: '#ddd6fe' }}
              onClick={() => setOnboardingStep(1)}
              title="Перезапустить онбординг"
            >
              <Sparkles size={12} />
              Онбординг (тур)
            </button>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <select
            value={tgState}
            onChange={(e) => setTgState(e.target.value as TelegramState)}
            style={{
              fontSize: '12px',
              padding: '5px 8px',
              borderRadius: '8px',
              border: '1px solid #d4d0df',
              background: '#fff',
              color: '#232128',
              cursor: 'pointer',
            }}
          >
            <option value="default">TG: по умолчанию</option>
            <option value="linked">TG: подключён</option>
            <option value="declined">TG: отказ (почта)</option>
          </select>

          <div style={{ display: 'flex', borderRadius: '8px', background: '#f1f0f5', padding: '2px' }}>
            <button
              type="button"
              style={{
                border: 0,
                background: viewport === 'desktop' ? '#fff' : 'transparent',
                borderRadius: '6px',
                padding: '4px 8px',
                fontSize: '12px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                color: viewport === 'desktop' ? '#1e1b24' : '#64748b',
                boxShadow: viewport === 'desktop' ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
              }}
              onClick={() => setViewport('desktop')}
            >
              <Monitor size={12} /> Десктоп
            </button>
            <button
              type="button"
              style={{
                border: 0,
                background: viewport === 'mobile' ? '#fff' : 'transparent',
                borderRadius: '6px',
                padding: '4px 8px',
                fontSize: '12px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                color: viewport === 'mobile' ? '#1e1b24' : '#64748b',
                boxShadow: viewport === 'mobile' ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
              }}
              onClick={() => setViewport('mobile')}
            >
              <Smartphone size={12} /> 390px
            </button>
          </div>

          <button
            type="button"
            className="bp-btn-pill"
            onClick={handleReset}
            title="Сбросить состояние"
          >
            <RotateCcw size={13} />
          </button>
        </div>
      </div>

      {/* ================= STAGE 1: BOOKING CONFIRMATION ================= */}
      {step === 'booking' && (
        <div style={{ padding: '40px 20px 80px' }}>
          <header className="bp-header" style={{ marginBottom: '32px' }}>
            <img src={logo} alt="HR Vision" />
          </header>

          <main
            className="bp-main"
            style={{
              maxWidth: viewport === 'mobile' ? '390px' : '560px',
              margin: '0 auto',
              transition: 'max-width 0.25s cubic-bezier(0.16, 1, 0.3, 1)',
            }}
          >
            <section className="bp-surface bp-confirmation" aria-label="Подтверждение записи">
              <span className="bp-symbol bp-success">
                <Check size={26} />
              </span>

              <h1>Вы записаны</h1>

              {/* Time Slot Display (Noise removed: NO "Московское время · 30 минут", NO bulky calendar buttons) */}
              <div className="bp-booked-time">
                <strong>{formattedDate}</strong>
                <span>{formattedTime}</span>
              </div>

              {/* TELEGRAM / EMAIL CHANNEL (Apple Minimalist) */}
              <div className="bp-channel-card" data-mode={tgState}>
                {tgState === 'default' && (
                  <>
                    <h3>Напоминание за 1 час</h3>
                    <p>Пришлём ссылку прямо в Telegram, чтобы не искать в почте.</p>
                    <div className="bp-channel-actions">
                      <Button
                        variant="default"
                        disabled={isOpeningTg}
                        onClick={handleConnectTg}
                      >
                        {isOpeningTg ? 'Открываем…' : 'Подключить Telegram'}
                      </Button>
                      <button
                        type="button"
                        className="bp-optout-btn"
                        onClick={handleDeclineTg}
                      >
                        На почту
                      </button>
                    </div>
                  </>
                )}

                {tgState === 'declined' && (
                  <>
                    <h3>Напоминание придёт на почту</h3>
                    <p>
                      Ссылку отправим на <strong>{candidateEmail}</strong> за 1 час до встречи.
                    </p>
                    <div className="bp-channel-reconnect">
                      <span>Хотите в мессенджер?</span>
                      <button type="button" onClick={() => setTgState('default')}>
                        Подключить Telegram
                      </button>
                    </div>
                  </>
                )}

                {tgState === 'linked' && (
                  <>
                    <h3>✓ Telegram подключён</h3>
                    <p>Бот пришлёт ссылку на вход за 1 час до встречи.</p>
                    <div className="bp-channel-actions">
                      <a
                        href={tgDeepLink}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="bp-btn-pill"
                      >
                        Открыть бота
                      </a>
                      <button
                        type="button"
                        className="bp-optout-btn"
                        onClick={() => setTgState('default')}
                      >
                        Отвязать
                      </button>
                    </div>
                  </>
                )}
              </div>

              {/* Progression CTA directly into Calendar & Meetings view */}
              <Button
                variant="default"
                size="lg"
                className="bp-next-cabinet-btn"
                onClick={() => {
                  setStep('calendar');
                  setOnboardingStep(1);
                }}
              >
                Перейти в календарь к встрече
                <ArrowRight size={17} />
              </Button>
            </section>
          </main>
        </div>
      )}

      {/* ================= STAGE 2: CANDIDATE PORTAL (CALENDAR + ONBOARDING) ================= */}
      {step === 'calendar' && (
        <div
          style={{
            maxWidth: viewport === 'mobile' ? '390px' : '100%',
            margin: '0 auto',
            borderLeft: viewport === 'mobile' ? '1px solid #e5e2ee' : 'none',
            borderRight: viewport === 'mobile' ? '1px solid #e5e2ee' : 'none',
            background: '#fff',
            minHeight: 'calc(100vh - 53px)',
            transition: 'max-width 0.25s cubic-bezier(0.16, 1, 0.3, 1)',
          }}
        >
          <div className="cp-root cp-surfaces cp-cabinet">
            {/* Real Topbar */}
            <header className="cp-topbar">
              <div className="cp-logo">
                <img src={logo} alt="HR Vision" />
              </div>

              <div className="cp-account">
                <button
                  type="button"
                  className={`cp-avatar ${onboardingStep === 2 ? 'bp-spotlight-active' : ''}`}
                  aria-label="Профиль кандидата"
                  onClick={() => alert('Профиль: Алексей Смирнов (Lead Product Manager)')}
                >
                  АС
                </button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Выйти"
                  onClick={() => setStep('booking')}
                  title="Выйти"
                >
                  <LogOut size={18} />
                </Button>
              </div>
            </header>

            <div className="cp-body cp-authenticated">
              {/* Real Sidebar */}
              <aside className="cp-sidebar" aria-label="Разделы кандидата">
                <nav aria-label="Навигация кабинета">
                  <button
                    type="button"
                    className={onboardingStep === 3 ? 'bp-spotlight-active' : ''}
                    aria-current={cabinetTab === 'vacancies' ? 'page' : undefined}
                    aria-label="Поиск вакансий"
                    onClick={() => setCabinetTab('vacancies')}
                    title="Поиск вакансий"
                  >
                    <Home size={21} />
                  </button>
                  <button
                    type="button"
                    aria-current={cabinetTab === 'calendar' ? 'page' : undefined}
                    aria-label="Календарь встреч"
                    onClick={() => setCabinetTab('calendar')}
                    title="Календарь встреч"
                  >
                    <CalendarDays size={21} />
                  </button>
                  <a
                    className="cp-support"
                    href="mailto:support@hr-vision.tech"
                    aria-label="Поддержка"
                    title="Поддержка"
                  >
                    <Mail size={21} />
                  </a>
                </nav>
              </aside>

              {/* Main Content Area */}
              <main className="cp-main" style={{ padding: viewport === 'mobile' ? '20px 16px' : '32px 40px' }}>
                {/* ================= ONBOARDING TOUR DOCK ================= */}
                {onboardingStep !== null && (
                  <div className="bp-onboarding-dock" role="region" aria-label="Онбординг">
                    <div className="bp-onboarding-badge">
                      <Sparkles size={13} />
                      {onboardingStep === 1 && 'Шаг 1 из 3 · Назначенная встреча'}
                      {onboardingStep === 2 && 'Шаг 2 из 3 · Данные кандидата'}
                      {onboardingStep === 3 && 'Шаг 3 из 3 · Персональный подбор'}
                    </div>

                    <h2 className="bp-onboarding-title">
                      {onboardingStep === 1 && 'Вот ваша встреча'}
                      {onboardingStep === 2 && 'Вот ваш профиль'}
                      {onboardingStep === 3 && 'Сюда придут предложения'}
                    </h2>

                    <p className="bp-onboarding-desc">
                      {onboardingStep === 1 &&
                        `Интервью с рекрутером назначено на ${formattedDate}, ${formattedTime}. Прямо в этой карточке появится кнопка «Войти во встречу» за 10 минут до звонка. За 1 час пришлём напоминание со ссылкой.`}
                      {onboardingStep === 2 &&
                        'Мы создали кабинет и автоматически заполнили профиль из вашего резюме: позиция Lead Product Manager, вилка от 320 000 ₽. На звонке рекрутер зафиксирует ваши пожелания к командам.'}
                      {onboardingStep === 3 &&
                        'После интервью рекрутер начнёт предлагать вашу кандидатуру работодателям. Все подходящие предложения появятся во вкладке «Поиск» с условиями и прямым откликом.'}
                    </p>

                    <div className="bp-onboarding-footer">
                      <div className="bp-onboarding-dots">
                        <span className="bp-onboarding-dot" data-active={onboardingStep === 1} />
                        <span className="bp-onboarding-dot" data-active={onboardingStep === 2} />
                        <span className="bp-onboarding-dot" data-active={onboardingStep === 3} />
                      </div>

                      <div className="bp-onboarding-nav">
                        {onboardingStep > 1 && (
                          <button
                            type="button"
                            className="bp-onboarding-nav-btn"
                            onClick={() => setOnboardingStep((s) => ((s ?? 1) - 1) as 1 | 2 | 3)}
                          >
                            ← Назад
                          </button>
                        )}
                        <button
                          type="button"
                          className="bp-onboarding-nav-btn bp-primary-nav"
                          onClick={() => {
                            if (onboardingStep < 3) {
                              setOnboardingStep((s) => ((s ?? 1) + 1) as 1 | 2 | 3);
                            } else {
                              setOnboardingStep(null);
                            }
                          }}
                        >
                          {onboardingStep < 3 ? 'Далее →' : 'Всё понятно, к встрече! ✓'}
                        </button>
                        <button
                          type="button"
                          className="bp-onboarding-nav-btn"
                          style={{ background: 'transparent', opacity: 0.7 }}
                          onClick={() => setOnboardingStep(null)}
                        >
                          Пропустить
                        </button>
                      </div>
                    </div>
                  </div>
                )}

                {/* Reminder Status Banner */}
                <div
                  className="bp-cabinet-notification-banner"
                  data-mode={tgState === 'linked' ? 'linked' : 'email'}
                >
                  <div>
                    {tgState === 'linked' ? (
                      <>
                        <strong>✓ Напоминание в Telegram подключено</strong> · Бот пришлёт ссылку на вход за 1 час до встречи.
                      </>
                    ) : tgState === 'declined' ? (
                      <>
                        <strong>✉ Напоминание придёт на почту</strong> · Ссылку отправим на {candidateEmail} за 1 час.
                      </>
                    ) : (
                      <>
                        <strong>✉ Напоминание придёт на почту</strong> · {candidateEmail}
                      </>
                    )}
                  </div>
                  {tgState !== 'linked' ? (
                    <button type="button" onClick={handleConnectTg}>
                      Подключить Telegram
                    </button>
                  ) : (
                    <button type="button" onClick={() => setTgState('default')}>
                      Изменить
                    </button>
                  )}
                </div>

                {/* Calendar View vs Vacancies View */}
                {cabinetTab === 'calendar' ? (
                  <div className={onboardingStep === 1 ? 'bp-spotlight-active' : ''} style={{ borderRadius: '24px' }}>
                    <CandidateSchedule
                      candidate={mockCandidate}
                      busy={false}
                      onAvailability={async () => true}
                      onTimezone={async () => true}
                      onMeetingReply={async () => true}
                      onOpenVacancy={() => setCabinetTab('vacancies')}
                      onJoin={() => alert('Открывается защищённая видеокомната интервью Daily.co')}
                      onInterview={() => alert('Открывается защищённая видеокомната интервью Daily.co')}
                      onBookInterview={() => setStep('booking')}
                    />
                  </div>
                ) : (
                  <section style={{ padding: '24px', background: '#f8f8fb', borderRadius: '24px' }}>
                    <h2 style={{ fontSize: '22px', fontWeight: 650, margin: '0 0 12px' }}>Предложения о работе</h2>
                    <p style={{ color: '#686473', margin: '0 0 20px' }}>
                      После звонка с рекрутером здесь появятся персонально отобранные вакансии компаний.
                    </p>
                    <article
                      style={{
                        background: '#fff',
                        padding: '20px 24px',
                        borderRadius: '18px',
                        boxShadow: '0 2px 8px rgba(0,0,0,0.04)',
                      }}
                    >
                      <h3 style={{ margin: '0 0 4px', fontSize: '18px', fontWeight: 600 }}>Lead Product Manager</h3>
                      <p style={{ margin: '0 0 8px', color: '#686473', fontSize: '14px' }}>Fintech Solutions · 350 000 – 420 000 ₽ · Удаленно</p>
                      <span
                        style={{
                          display: 'inline-block',
                          padding: '4px 10px',
                          borderRadius: '8px',
                          background: '#fef3c7',
                          color: '#92400e',
                          fontSize: '12.5px',
                          fontWeight: 550,
                        }}
                      >
                        Ожидает завершения интервью
                      </span>
                    </article>
                  </section>
                )}
              </main>
            </div>
          </div>
        </div>
      )}

      {/* ================= STAGE 3: POST-INTERVIEW DEBRIEF ================= */}
      {step === 'debrief' && (
        <div style={{ padding: '40px 20px 80px' }}>
          <header className="bp-header" style={{ marginBottom: '32px' }}>
            <img src={logo} alt="HR Vision" />
          </header>

          <main
            className="bp-main"
            style={{
              maxWidth: viewport === 'mobile' ? '390px' : '560px',
              margin: '0 auto',
              transition: 'max-width 0.25s cubic-bezier(0.16, 1, 0.3, 1)',
            }}
          >
            <section className="bp-surface bp-confirmation" aria-label="Итоги интервью">
              <span className="bp-symbol bp-success">
                <Check size={26} />
              </span>

              <h1>Интервью завершено</h1>

              {/* Profile Review */}
              <div className="bp-debrief-profile">
                <div className="bp-debrief-header">
                  <h3>Профиль</h3>
                  <button
                    type="button"
                    className="bp-resume-badge"
                    onClick={() => alert('Предпросмотр резюме: Резюме_Алексей_Смирнов_Product.pdf')}
                  >
                    <FileText size={13} color="#6756ce" />
                    Резюме.pdf
                  </button>
                </div>

                <div className="bp-debrief-summary">
                  <div>
                    <strong>Lead Product Manager</strong> · от 320 000 ₽ · Удалённо
                  </div>
                  <small>Опыт 8+ лет, B2B SaaS, подтверждён рекрутером</small>
                </div>
              </div>

              {/* Compact Step Indicator */}
              <div className="bp-timeline-compact">
                <span className="bp-timeline-pill bp-done">✓ Интервью</span>
                <span className="bp-timeline-arrow">→</span>
                <span className="bp-timeline-pill">Матчинг (до 24ч)</span>
                <span className="bp-timeline-arrow">→</span>
                <span className="bp-timeline-pill">Предложения</span>
              </div>

              {/* TELEGRAM OFFER CHANNEL (Apple Minimalist) */}
              <div className="bp-channel-card" data-mode={tgState}>
                {tgState === 'default' && (
                  <>
                    <h3>Предложения о работе</h3>
                    <p>Пришлём вакансии с вилкой и условиями прямо в Telegram.</p>
                    <div className="bp-channel-actions">
                      <Button
                        variant="default"
                        disabled={isOpeningTg}
                        onClick={handleConnectTg}
                      >
                        {isOpeningTg ? 'Открываем…' : 'Получать в Telegram'}
                      </Button>
                      <button
                        type="button"
                        className="bp-optout-btn"
                        onClick={handleDeclineTg}
                      >
                        На почту
                      </button>
                    </div>
                  </>
                )}

                {tgState === 'declined' && (
                  <>
                    <h3>Предложения придут на почту</h3>
                    <p>
                      Подходящие вакансии отправим на <strong>{candidateEmail}</strong>.
                    </p>
                    <div className="bp-channel-reconnect">
                      <span>Хотите в мессенджер?</span>
                      <button type="button" onClick={() => setTgState('default')}>
                        В Telegram
                      </button>
                    </div>
                  </>
                )}

                {tgState === 'linked' && (
                  <>
                    <h3>✓ Telegram подключён</h3>
                    <p>Новые вакансии поступят в чат с ботом.</p>
                    <div className="bp-channel-actions">
                      <a
                        href={tgDeepLink}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="bp-btn-pill"
                      >
                        Открыть бота
                      </a>
                      <button
                        type="button"
                        className="bp-optout-btn"
                        onClick={() => setTgState('default')}
                      >
                        Отвязать
                      </button>
                    </div>
                  </>
                )}
              </div>

              <Button
                variant="outline"
                size="lg"
                style={{ width: '100%', marginTop: '16px' }}
                onClick={() => {
                  setStep('calendar');
                  setOnboardingStep(null);
                }}
              >
                Вернуться в календарь кабинета
              </Button>
            </section>
          </main>
        </div>
      )}
    </div>
  );
}
