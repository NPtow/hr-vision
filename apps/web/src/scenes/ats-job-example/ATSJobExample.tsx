import { useRef, useState } from 'react';
import { ArrowDown, ArrowRight, ArrowUpRight, RotateCcw, ChevronDown } from 'lucide-react';
import './ats-job-example.css';
import { JourneyNav } from '../job-journeys/JourneyNav';

type Interest = 'pending' | 'yes' | 'no';
type DetailId = 'hr-job' | 'manager-job' | 'new-job' | 'compare' | 'handoff' | 'feedback' | 'receive' | 'decide' | 'show' | 'interest' | 'access';
type Detail = { label: string; title: string; before: string; action: string; after: string; job: string; ours: string; evidence?: string };

const details: Record<DetailId, Detail> = {
  'hr-job': {
    label: 'Джоба · HR · рабочая формулировка', title: 'Чтобы нанимающий доверял моему отбору',
    before: 'HR отвечает за подбор. То, как руководитель воспринимает её работу, для неё важно.',
    action: 'Отбирает кандидатов, объясняет свой выбор и передаёт материалы руководителю.',
    after: 'Руководителю проще понять основания отбора. Возникло ли доверие и оценил ли он работу HR, ещё нужно выяснить.',
    job: 'Эта потребность не заканчивается нажатием «Передать» и не считается закрытой автоматически после решения менеджера.',
    ours: 'Помогаем подготовить обоснованную подборку и передать её вместе с доказательствами.',
    evidence: 'Мотивация со слов Никиты: «Меньше ебали, больше хвалили». Доверие к отбору здесь служит рабочей конкретизацией, а не результатом исследования.',
  },
  'manager-job': {
    label: 'Основная джоба · нанимающий менеджер', title: 'Нанять человека, который закроет эту боль',
    before: 'В компании есть боль или провал. Вакансия отражает потребность в человеке, который поможет это исправить.',
    action: 'Менеджер получает подборку HR, сопоставляет людей с задачей и выбирает, с кем продолжить общение.',
    after: 'Выбран следующий шаг в поиске человека. Боль бизнеса ещё не закрыта, а основной желаемый результат остаётся прежним.',
    job: 'Джоба продолжается через отбор, интервью и договорённости. Выбор людей для встречи не завершает найм и не подтверждает решение бизнес-проблемы.',
    ours: 'В бесплатной ATS помогаем работать со своими кандидатами и передавать основания отбора. Диагностику бизнес-проблемы здесь не берём на себя.',
    evidence: 'Формулировка и связь вакансии с болью компании уточнены Никитой. Соответствие кандидата этой боли нужно проверять.',
  },
  'new-job': {
    label: 'Новая конкретная потребность · только при интересе', title: 'Хочу именно этого кандидата: он закроет мою боль',
    before: 'Менеджер ищет человека для решения боли бизнеса. До показа профиля он ещё не был сосредоточен на этом внешнем кандидате.',
    action: 'Увидел кандидата и поверил, что именно этот человек может решить его задачу.',
    after: 'Фокус сместился с «Кто закроет эту боль?» на «Как мне нанять именно этого кандидата?».',
    job: 'Основная джоба найма остаётся активной. Внутри неё появляется конкретное желание нанять этого человека. Если интереса нет, этого смещения не происходит.',
    ours: 'Должны показать такого кандидата и такие основания, чтобы менеджер увидел связь со своей болью. Одного сильного резюме или высокого балла для этого может быть недостаточно.',
    evidence: '«Он закроет мою боль» передаёт убеждение менеджера в этой ветке, а не доказанный результат или обещание HR Vision.',
  },
  compare: {
    label: 'Действие · HR · с нами', title: 'Отбирает своих кандидатов',
    before: 'У компании уже есть вакансия, свои кандидаты и материалы о них.',
    action: 'HR сравнивает кандидатов и выбирает, кого имеет смысл передать менеджеру.',
    after: 'Подготовлена подборка с объяснением, почему в неё вошли эти люди.',
    job: 'Это способ продвинуться к желаемому результату HR. Загрузка CV или записи интервью сама по себе джобой не становится.',
    ours: 'Свои кандидаты, записи интервью, оценка и таймкоды в одном месте.',
    evidence: 'Бесплатная часть ATS: GTM-дека 2.9, с. 43. Бизнес-диагностика находится за границей этого примера.',
  },
  handoff: {
    label: 'Передача · HR → менеджер', title: 'Передаёт подборку и основания выбора',
    before: 'HR определила, чьи материалы готова передать для решения.',
    action: 'Передаёт профили, аргументы и доступные доказательства нанимающему менеджеру.',
    after: 'У менеджера появились данные, по которым он может сформировать состав встреч.',
    job: 'Передача меняет ситуацию другого участника. Она не доказывает, что HR получила признание или что менеджер уже доволен результатом.',
    ours: 'Подборка и материалы для рассмотрения. Формат передачи является решением продукта.',
  },
  feedback: {
    label: 'Событие · HR получает решение', title: 'Узнаёт, кого менеджер хочет пригласить',
    before: 'Подборка передана. HR ждёт решения по следующему шагу.',
    action: 'Менеджер сообщает, с кем готов встретиться и по кому нужны уточнения.',
    after: 'HR может согласовывать встречи. Само решение ещё не равно похвале или доверию.',
    job: 'Мотивация HR продолжает действовать. Следующие работы по организации встреч находятся за границей фрагмента.',
    ours: 'Сохраняем решение и возвращаем его HR вместе с комментариями.',
  },
  receive: {
    label: 'Событие · нанимающий менеджер', title: 'Получает кандидатов и основания отбора',
    before: 'Менеджер ещё не сформировал состав личных встреч.',
    action: 'HR передала подборку; менеджер получает доступ к материалам.',
    after: 'Есть основание для собственного выбора, а не только фамилии в списке.',
    job: 'Текущая потребность уже была активна. Получение материалов помогает продвинуться, но ещё не закрывает её.',
    ours: 'Показываем профили, доказательства, оценку и комментарии HR.',
  },
  decide: {
    label: 'Действие → изменение ситуации · менеджер', title: 'Определяет, кого пригласить на интервью',
    before: 'Нужно понять, на кого потратить время личных встреч.',
    action: 'Рассматривает переданные материалы и выбирает людей для интервью.',
    after: 'Определён состав ближайших встреч. Человек ещё не нанят, а боль бизнеса остаётся причиной продолжать поиск.',
    job: 'Это промежуточное действие внутри джобы «Нанять человека, который закроет эту боль». Джоба не закрывается выбором людей для интервью.',
    ours: 'Материалы помогают принять решение, а фиксация решения помогает вернуть его HR.',
    evidence: 'Интервью, проверка ожиданий и взаимные договорённости остаются впереди. Подборка сама по себе не доказывает, что боль будет решена.',
  },
  show: {
    label: 'Событие · инициатор HR Vision', title: 'Показываем внешнего кандидата рядом со своими',
    before: 'Менеджер продолжает искать человека под боль бизнеса. Именно этого внешнего кандидата он ещё не хотел нанять.',
    action: 'Продукт показывает внешнего кандидата и материалы интервью, которые могут связать его опыт с задачей компании.',
    after: 'У менеджера появилась новая информация. Он может заинтересоваться, а может пройти мимо.',
    job: 'Основная джоба найма уже активна. Показ профиля не равен желанию нанять именно этого человека: это желание может появиться в ответ.',
    ours: 'Это наша инициатива и механизм GTM. Показ находится в слое событий под джобами человека.',
    evidence: 'GTM-дека 2.9, с. 9: свой поток кандидатов и более сильный внешний кандидат рядом.',
  },
  interest: {
    label: 'Развилка · реакция менеджера', title: 'Увидел в нём решение своей боли?',
    before: 'Внешний профиль показан, но реакция менеджера ещё неизвестна.',
    action: 'Менеджер соотносит опыт кандидата с болью компании: «Это тот человек, который мне нужен?»',
    after: 'Если да, фокус становится конкретным: «Хочу именно этого кандидата. Как его нанять?» Если нет, продолжается поиск того, кто закроет боль.',
    job: 'Основная джоба остаётся активной в обеих ветках. Конкретное желание нанять внешнего кандидата возникает только при интересе.',
    ours: 'Проверяем, видит ли менеджер решение своей задачи в этом человеке, и возникает ли желание добиваться его найма. Показ и высокий балл этого не доказывают.',
  },
  access: {
    label: 'Препятствие и путь решения · менеджер', title: 'Хочет связаться, но контакт закрыт',
    before: 'Менеджер хочет именно этого кандидата, потому что видит в нём решение боли компании.',
    action: 'Пытается перейти к общению и обнаруживает, что для внешнего кандидата нужен доступ к контакту.',
    after: 'Возникает препятствие на пути к конкретному человеку. Можно получить доступ, отложить решение или вернуться к другим кандидатам.',
    job: 'Закрытый контакт мешает уже возникшей потребности. Покупка контакта является способом продолжить, а не названием джобы.',
    ours: 'Предлагаем доступ к внешнему кандидату. Кто покупает контакт и на каких условиях, этот пример не определяет.',
    evidence: 'Согласие кандидата на общение и работу требуется отдельно. Встреча и найм не следуют автоматически из покупки.',
  },
};

const phases = ['Свои кандидаты', 'Передача', 'Решение', 'Новая информация', 'Интерес', 'Доступ'];

export function ATSJobExample() {
  const mapScroll = useRef<HTMLDivElement>(null);
  const [interest, setInterest] = useState<Interest>('pending');
  const [selected, setSelected] = useState<DetailId>('show');
  const [showDetail, setShowDetail] = useState(false);
  const detail = details[selected];
  const select = (id: DetailId) => { setSelected(id); setShowDetail(true); };
  const choose = (next: Interest) => { setInterest(next); setSelected(next === 'yes' ? 'new-job' : 'interest'); setShowDetail(true); };
  const isSelected = (id: DetailId) => showDetail && selected === id;

  const event = (id: DetailId, kind: string, title: string, effect: string, extra = '') => (
    <button type="button" className={`ajx-event ${extra} ${isSelected(id) ? 'is-selected' : ''}`} onClick={() => select(id)} aria-expanded={isSelected(id)} aria-controls="ajx-detail">
      <span className="ajx-event-kind">{kind}</span>
      <strong>{title}</strong>
      <span className="ajx-event-effect">{effect}</span>
      <span className="ajx-event-open">Разобрать <ArrowUpRight size={13}/></span>
    </button>
  );

  return <main className="ajx-app">
    <header className="ajx-header">
      <a className="ajx-brand" href="?id=hr-vision-agency--overview&viewMode=story">HR Vision <span>/</span> CJM</a>
      <span className="ajx-header-label">Бесплатная ATS · один пример</span>
      <button className="ajx-reset" type="button" onClick={() => { setInterest('pending'); setShowDetail(false); setSelected('show'); mapScroll.current?.scrollTo({ left: 0 }); }}><RotateCcw size={14}/> Сначала</button>
    </header>
    <JourneyNav active="ats"/>

    <section className="ajx-intro">
      <div className="ajx-overline">Проверяем логику, затем расширяем карту</div>
      <h1>От боли бизнеса к желанию нанять конкретного человека</h1>
      <p>Менеджер хочет нанять человека, который закроет боль компании. Вакансия отражает эту боль.<br className="ajx-desktop-br"/> Мы показываем внешнего кандидата и проверяем, сместится ли фокус на его найм.</p>
      <div className="ajx-reading-guide"><span><i className="ajx-key-job"/>Сверху: чего человек хочет</span><span><i className="ajx-key-event"/>Снизу: что происходит и меняется</span><span className="ajx-guide-tip">Нажми на любой блок, чтобы разобрать связь</span></div>
    </section>

    <section className="ajx-map-section" aria-label="Джобы и события двух участников во времени">
      <div className="ajx-scroll-hint">Карту можно листать вправо <ArrowRight size={14}/></div>
      <div className="ajx-map-scroll" ref={mapScroll} tabIndex={0} aria-label="Горизонтальная карта сценария">
        <div className="ajx-map">
          <div className="ajx-grid ajx-phase-row"><span className="ajx-time-label">Время <ArrowRight size={15}/></span>{phases.map((phase, i) => <div className="ajx-phase" key={phase}><span>0{i + 1}</span>{phase}</div>)}</div>

          <section className="ajx-lane" aria-label="HR компании">
            <div className="ajx-grid ajx-jobs-row">
              <div className="ajx-role"><span className="ajx-role-number">01</span><h2>HR компании</h2><span className="ajx-layer-label">Джоба ↑</span></div>
              <button className={`ajx-job ajx-hr-job ${isSelected('hr-job') ? 'is-selected' : ''}`} onClick={() => select('hr-job')} type="button" aria-controls="ajx-detail">
                <span className="ajx-job-topline">Желаемый результат · рабочая формулировка</span>
                <strong>Чтобы нанимающий доверял моему отбору</strong>
                <span className="ajx-job-quote">«Меньше ебали, больше хвалили» <span>мотивация со слов Никиты</span></span>
                <span className="ajx-job-continues">Не заканчивается передачей <ArrowRight size={17}/></span>
              </button>
            </div>
            <div className="ajx-grid ajx-events-row">
              <div className="ajx-row-caption">Действия<br/>и события <ArrowDown size={13}/></div>
              {event('compare', 'HR · с нами', 'Отбирает своих кандидатов', 'Есть подборка и основания выбора.')}
              {event('handoff', 'HR → менеджер', 'Передаёт подборку', 'У менеджера появились данные для решения.', 'ajx-transfer')}
              {event('feedback', 'Получает ответ', 'Узнаёт, кого звать', 'Может согласовывать встречи.')}
              <div className="ajx-hr-continuation"><span className="ajx-quiet-line"/><p>{interest === 'yes' ? 'Если менеджер заинтересовался внешним кандидатом, HR может помочь установить контакт.' : 'Дальше ведёт согласованный процесс. Новую джобу HR здесь пока не добавляем.'}</p><span>За пределами этого фрагмента</span></div>
            </div>
          </section>

          <div className="ajx-grid ajx-handoff-row"><span className="ajx-handoff-caption"><ArrowDown size={14}/> Подборка + основания отбора</span></div>

          <section className="ajx-lane ajx-manager-lane" aria-label="Нанимающий менеджер">
            <div className="ajx-grid ajx-jobs-row">
              <div className="ajx-role"><span className="ajx-role-number">02</span><h2>Нанимающий<br/>менеджер</h2><span className="ajx-layer-label">Джобы ↑</span></div>
              <button className={`ajx-job ajx-manager-job ${isSelected('manager-job') ? 'is-selected' : ''}`} onClick={() => select('manager-job')} type="button" aria-controls="ajx-detail">
                <span className="ajx-job-topline">Основная джоба · активна на всём пути</span>
                <strong>Нанять человека, который закроет эту боль</strong>
                <span className="ajx-job-context">Вакансия отражает боль или провал в компании.</span>
                <span className="ajx-job-continues">Отбор не завершает найм <ArrowRight size={17}/></span>
              </button>
            </div>
            <div className="ajx-grid ajx-focus-row">
              <div className="ajx-row-caption">На чём<br/>сейчас фокус <ArrowRight size={13}/></div>
              <button className="ajx-initial-focus" type="button" onClick={() => select('manager-job')} aria-controls="ajx-detail">
                <span className="ajx-event-kind">До показа внешнего кандидата</span>
                <strong>Есть боль в бизнесе.<br/>Кто её закроет?</strong>
                <span>Рассматривает людей и продолжает найм.</span>
              </button>
              <div className="ajx-job-gap"><span className="ajx-gap-line"/><span>Именно этого кандидата<br/>ещё не хотел нанять</span><small>Показ может сместить фокус</small></div>
              <div className={`ajx-new-job-slot is-${interest}`} aria-live="polite">
                {interest === 'yes' ? <button type="button" className={`ajx-job ajx-new-job ${isSelected('new-job') ? 'is-selected' : ''}`} onClick={() => select('new-job')} aria-controls="ajx-detail"><span className="ajx-job-topline"><ArrowUpRight size={14}/> Новая конкретная потребность</span><strong>Хочу именно этого кандидата: он закроет мою боль</strong><span className="ajx-job-bottom">Теперь фокус: как мне его нанять?</span><span className="ajx-belief-note">Так считает менеджер. Результат ещё не доказан.</span></button> : <div className="ajx-future-job"><span className="ajx-gap-line"/><strong>{interest === 'no' ? 'Фокус остаётся на поиске человека под боль' : 'Захочет ли именно этого человека?'}</strong><span>{interest === 'no' ? 'Основная джоба продолжается. Конкретного желания нанять внешнего кандидата нет.' : 'Пока такого желания нет. Всё зависит от реакции ниже.'}</span></div>}
              </div>
            </div>

            <div className="ajx-grid ajx-events-row ajx-manager-events">
              <div className="ajx-row-caption">Действия<br/>и события <ArrowDown size={13}/></div>
              <div className="ajx-start-context"><span className="ajx-event-kind">Ситуация до</span><strong>Есть боль бизнеса<br/>и вакансия под неё</strong><p>Нужен человек, который поможет её закрыть.</p></div>
              {event('receive', 'Получает от HR', 'Получает материалы', 'Может обосновать собственный выбор.', 'ajx-transfer')}
              {event('decide', 'Менеджер · с нами', 'Выбирает, кого звать', 'Следующий шаг определён. Найм продолжается.')}
              {event('show', 'Событие · HR Vision', 'Показываем внешнего кандидата', 'Новая информация. Интерес ещё не известен.', 'ajx-product-event')}
              <div className="ajx-branch">
                <button type="button" className="ajx-branch-heading" onClick={() => select('interest')} aria-controls="ajx-detail"><span className="ajx-event-kind">Реакция менеджера</span><strong>В нём видит решение боли?</strong><ArrowUpRight size={13}/></button>
                <div className="ajx-choices" role="group" aria-label="Интерес к внешнему кандидату">
                  <button type="button" aria-pressed={interest === 'yes'} className={interest === 'yes' ? 'is-active' : ''} onClick={() => choose('yes')}>Да, хочу его нанять <ArrowUpRight size={13}/></button>
                  <button type="button" aria-pressed={interest === 'no'} className={interest === 'no' ? 'is-active' : ''} onClick={() => choose('no')}>Нет, не нужен <ArrowRight size={13}/></button>
                </div>
                <span className="ajx-branch-note">{interest === 'pending' ? 'Выбери ветку и посмотри наверх.' : interest === 'yes' ? 'Фокус: как нанять именно его ↑' : 'Ищет дальше того, кто закроет боль.'}</span>
              </div>
              {interest === 'yes' ? event('access', 'Препятствие', 'Хочет связаться, контакт закрыт', 'Доступ к контакту: один из путей продолжить.', 'ajx-access') : <div className="ajx-end-state"><ArrowRight size={20}/><strong>{interest === 'no' ? 'Продолжает со своими' : 'Продолжение зависит от реакции'}</strong><span>{interest === 'no' ? 'Ищет человека под боль бизнеса. К этой платной ветке не переходит.' : 'Доступ нужен, только если захочет перейти к общению.'}</span></div>}
            </div>
          </section>
        </div>
      </div>
    </section>

    <section id="ajx-detail" className={`ajx-detail ${showDetail ? 'is-open' : ''}`} aria-live="polite" aria-label="Разбор выбранного блока">
      {showDetail ? <>
        <div className="ajx-detail-header"><div><span className="ajx-overline">{detail.label}</span><h2>{detail.title}</h2></div><button type="button" className="ajx-close-detail" onClick={() => setShowDetail(false)}>Свернуть <ChevronDown size={15}/></button></div>
        <div className="ajx-detail-sequence"><div><span>До</span><p>{detail.before}</p></div><div><span>Что происходит</span><p>{detail.action}</p></div><div><span>Что изменилось</span><p>{detail.after}</p></div></div>
        <div className="ajx-detail-bottom"><div><span>Что с джобой</span><p>{detail.job}</p></div><div><span>Роль HR Vision</span><p>{detail.ours}</p></div></div>
        {detail.evidence && <p className="ajx-evidence">{detail.evidence}</p>}
      </> : <div className="ajx-detail-placeholder"><span>Почему именно так?</span><p>Нажми на джобу или событие. Здесь появится связь: что было до, что изменилось и откуда взялась потребность.</p><ArrowUpRight size={19}/></div>}
    </section>

    <section className="ajx-hypothesis" aria-label="Основная гипотеза и риск стратегии">
      <div className="ajx-hypothesis-heading"><span className="ajx-overline">Что проверяем этой стратегией</span><h2>Сможем ли мы давать ему такого кандидата?</h2><p>Такого, в котором менеджер увидит убедительное решение своей конкретной боли и захочет именно его.</p></div>
      <div className="ajx-hypothesis-grid">
        <div><span>Гипотеза</span><p>Кандидат и доказательства его опыта могут сместить фокус с «Кто закроет мою боль?» на «Как мне нанять этого человека?».</p></div>
        <div><span>Основной риск</span><p>Сильного CV или высокой оценки недостаточно. Мы можем не суметь регулярно находить людей, которых менеджер связывает с решением своей боли.</p></div>
        <div><span>Как проверять</span><p>На реальных вакансиях выяснять, почему менеджер хочет конкретного человека и делает ли следующий шаг к общению. Интерес, доступ к контакту, найм и решение боли проверяем отдельно.</p></div>
      </div>
      <div className="ajx-hiring-continuation"><span>Основная джоба продолжается</span><p>Общение → условия и KPI → оффер и договор → выход → проверка, закрыта ли бизнес-боль</p></div>
    </section>

    <footer className="ajx-footer"><span>Один сценарий для проверки логики. Формулировки джоб рабочие.</span><span>HR и менеджер · без HR-агентства · найм продолжается за пределами фрагмента</span></footer>
  </main>;
}
