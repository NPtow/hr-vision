import { useEffect, useState } from 'react';
import { MotionConfig, motion, useReducedMotion } from 'motion/react';
import { CalendarDays, Check, FileText, UserRound, Video } from 'lucide-react';
import { LOGO_URL } from './assets';
import './style.css';
import './candidate-site.css';

const candidateEntry = '/iframe.html?id=hr-vision-product--candidate&viewMode=story';
const steps = [
  {
    id: 'resume',
    title: 'Загрузите резюме',
    description: 'Заполним профиль из CV. Вы проверите данные и добавите пожелания к работе.',
  },
  {
    id: 'interview',
    title: 'Познакомимся за 30 минут',
    description: 'Выберите время разговора с рекрутером. С вашего согласия это интервью увидят подходящие компании.',
  },
  {
    id: 'vacancies',
    title: 'Выберите, что интересно',
    description: 'Предложим вакансии с учётом вашего опыта и пожеланий. Вы решите, где участвовать.',
  },
  {
    id: 'meeting',
    title: 'Встретьтесь с работодателем',
    description: 'Компания уже знакома с вашим опытом. Подтвердите удобное время и переходите к делу.',
  },
] as const;

function JourneyPicture({ step }: { step: number }) {
  if (step === 0) return <div className="cs-resume-picture">
    <div className="cs-document-icon"><FileText size={30} strokeWidth={1.5} /></div>
    <h3>Ваш профиль</h3>
    <div className="cs-resume-row"><span>Опыт</span><span><Check size={16} />Из резюме</span></div>
    <div className="cs-resume-row"><span>Навыки</span><span><Check size={16} />Из резюме</span></div>
    <div className="cs-resume-note">Осталось добавить пожелания</div>
  </div>;

  if (step === 1) return <div className="cs-interview-picture">
    <div className="cs-interview-people" aria-hidden="true"><span><UserRound size={28} strokeWidth={1.5} /></span><Video size={24} strokeWidth={1.5} /><span><UserRound size={28} strokeWidth={1.5} /></span></div>
    <strong>30<span>минут</span></strong>
    <p>Вы и рекрутер.<br />Один разговор о вашем опыте.</p>
  </div>;

  if (step === 2) return <div className="cs-vacancies-picture">
    <span className="cs-picture-kicker">Ваша следующая работа</span>
    <h3>Подходит вам.</h3>
    <ul><li><Check size={18} />Интересная роль</li><li><Check size={18} />Ваш формат работы</li><li><Check size={18} />Ваши условия</li></ul>
    <p>Участвовать или пропустить — решаете вы.</p>
  </div>;

  return <div className="cs-meeting-picture">
    <div className="cs-calendar-icon"><CalendarDays size={43} strokeWidth={1.25} /></div>
    <span className="cs-picture-kicker">Следующий шаг</span>
    <h3>Встреча<br />с компанией.</h3>
    <p>Время подтверждаете вы.</p>
  </div>;
}

export function CandidateSite() {
  const reduced = useReducedMotion();
  const [step, setStep] = useState(0);

  useEffect(() => {
    const previousTitle = document.title;
    const description = document.querySelector<HTMLMetaElement>('meta[name="description"]');
    const previousDescription = description?.content;
    document.title = 'HR Vision — работа начинается с разговора';
    if (description) description.content = 'Одно 30-минутное интервью с рекрутером HR Vision, профиль из вашего резюме и вакансии с учётом ваших пожеланий.';
    return () => {
      document.title = previousTitle;
      if (description && previousDescription !== undefined) description.content = previousDescription;
    };
  }, []);

  return <MotionConfig reducedMotion="user"><div className="candidate-site">
    <a className="skip-link" href="#candidate-main">К содержанию</a>
    <header className="cs-header">
      <a className="cs-brand" href="/" aria-label="HR Vision — главная"><img src={LOGO_URL} width="150" height="24" alt="HR Vision" /></a>
      <nav aria-label="Главная навигация"><a className="cs-employer-link" href="/">Ищу сотрудника</a><a className="cs-login" href={`${candidateEntry}&cp=login`}>Войти</a></nav>
    </header>

    <main id="candidate-main">
      <section className="cs-hero" aria-labelledby="candidate-heading">
        <div className="cs-hero-light" aria-hidden="true" />
        <motion.div className="cs-hero-content" initial={reduced ? false : { opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ type: 'spring', bounce: 0, duration: .55 }}>
          <h1 id="candidate-heading">Ваша новая работа.<br /><em>Начнём с разговора.</em></h1>
          <p>Одно интервью с рекрутером —<br className="cs-desktop-break" /> для знакомства с разными компаниями.</p>
          <a className="story-button cs-primary" href={`${candidateEntry}&cp=register`}>Начать поиск</a>
          <a className="cs-how-link" href="#candidate-path">Как это работает</a>
        </motion.div>
        <div className="cs-hero-caption"><span>Ваш опыт.</span><span>Ваши условия.</span><span>Ваш выбор.</span></div>
      </section>

      <section id="candidate-path" className="cs-journey" aria-labelledby="candidate-path-heading">
        <div className="cs-section-heading"><h2 id="candidate-path-heading">Один раз о себе.<br /><em>Дальше — по делу.</em></h2></div>
        <div className="cs-journey-layout">
          <div className="cs-steps" role="tablist" aria-label="Как проходит поиск работы" aria-orientation="vertical">
            {steps.map((item, index) => <button
              className={`cs-step ${step === index ? 'is-current' : ''}`}
              type="button"
              role="tab"
              id={`candidate-step-${item.id}`}
              aria-controls="candidate-step-picture"
              aria-selected={step === index}
              tabIndex={step === index ? 0 : -1}
              key={item.id}
              onClick={() => setStep(index)}
              onKeyDown={event => {
                let next = index;
                if (event.key === 'ArrowDown' || event.key === 'ArrowRight') next = (index + 1) % steps.length;
                else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') next = (index + steps.length - 1) % steps.length;
                else if (event.key === 'Home') next = 0;
                else if (event.key === 'End') next = steps.length - 1;
                else return;
                event.preventDefault();
                setStep(next);
                document.getElementById(`candidate-step-${steps[next].id}`)?.focus();
              }}
            >
              <span className="cs-step-number" aria-hidden="true">0{index + 1}</span>
              <span><strong>{item.title}</strong><span className="cs-step-description">{item.description}</span></span>
            </button>)}
          </div>
          <div className={`cs-journey-picture cs-journey-picture-${steps[step].id}`} id="candidate-step-picture" role="tabpanel" aria-labelledby={`candidate-step-${steps[step].id}`} tabIndex={0}>
            <motion.div className="cs-picture-content" key={step} initial={reduced ? false : { opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: reduced ? 0 : .16 }}><JourneyPicture step={step} /></motion.div>
          </div>
        </div>
      </section>

      <section className="cs-choice" aria-labelledby="candidate-choice-heading">
        <div className="cs-section-heading"><h2 id="candidate-choice-heading">Поиск идёт.<br /><em>Решение — за вами.</em></h2></div>
        <div className="cs-choice-notes"><p>Соглашайтесь на вакансии сами. <br />Автосогласование — только по желанию.</p><p>Время каждой встречи <br />вы подтверждаете отдельно.</p></div>
        <a className="story-button cs-primary" href={`${candidateEntry}&cp=register`}>Создать профиль</a>
      </section>
    </main>

    <footer className="cs-footer"><img src={LOGO_URL} width="150" height="24" alt="HR Vision" /><a href="/">Ищу сотрудника</a><a href={`${candidateEntry}&cp=login`}>Войти в кабинет</a></footer>
  </div></MotionConfig>;
}
