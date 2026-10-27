import { useState, type FormEvent } from 'react';
import { ArrowRight, CalendarDays, Check, CheckCircle2, Clock3, FileText, MapPin, MessageCircle, UserRound } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs';
import { candidates, slots, type CandidateProfile, type JourneyProps } from './model';
import './candidate.css';

type Section = 'offer' | 'profile' | 'next';
type Modal = 'decline' | 'interview' | 'reschedule' | null;
const profileLabels: Record<keyof CandidateProfile, string> = {
  name: 'Имя и фамилия', experience: 'Опыт, который важно учесть', expectations: 'Ожидания по доходу', availability: 'Когда готовы выйти',
};

export function CandidateWorkspace({ state, update, notify }: JourneyProps) {
  const [section, setSection] = useState<Section>('offer');
  const [modal, setModal] = useState<Modal>(null);
  const [draft, setDraft] = useState<CandidateProfile>(state.profile);
  const [share, setShare] = useState(state.shareConfirmed);
  const [errors, setErrors] = useState<Partial<Record<keyof CandidateProfile, string>>>({});
  const [reason, setReason] = useState('');
  const [question, setQuestion] = useState(state.candidateQuestion);
  const [reschedule, setReschedule] = useState('');
  const [rescheduleError, setRescheduleError] = useState('');
  const [selectedSlot, setSelectedSlot] = useState(state.meetingSlot);
  const [editingBooking, setEditingBooking] = useState(false);
  const decision = state.decisions.anna;
  const eligibleToBook = decision.kind === 'invite' && state.candidateInterest === 'interested';
  const changed = Object.keys(draft).some(key => draft[key as keyof CandidateProfile] !== state.profile[key as keyof CandidateProfile]) || share !== state.shareConfirmed;
  const candidate = candidates[0];

  function showSection(value: Section) {
    setSection(value);
  }

  function saveProfile(event: FormEvent) {
    event.preventDefault();
    const nextErrors: Partial<Record<keyof CandidateProfile, string>> = {};
    if (draft.name.trim().length < 2) nextErrors.name = 'Укажите имя, чтобы к вам можно было обратиться.';
    if (draft.experience.trim().length < 20) nextErrors.experience = 'Добавьте немного деталей об опыте: хотя бы одно предложение.';
    if (draft.expectations.trim().length < 3) nextErrors.expectations = 'Укажите ожидания или напишите «Обсуждается».';
    if (draft.availability.trim().length < 3) nextErrors.availability = 'Укажите срок или напишите «Обсуждается».';
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) {
      document.getElementById(`agc-${Object.keys(nextErrors)[0]}`)?.focus();
      return;
    }
    const profile = Object.fromEntries(Object.entries(draft).map(([key, value]) => [key, value.trim()])) as CandidateProfile;
    update({ profile, profileSaved: true, shareConfirmed: share });
    setDraft(profile);
    notify('Изменения профиля сохранены в этом просмотре.');
  }

  function acceptInterest() {
    update({ candidateInterest: 'interested', candidateDeclineReason: '' });
    notify('Ваш интерес к роли отмечен. Следующий шаг доступен во вкладке «Следующий шаг».');
  }

  function declineRole() {
    update({ candidateInterest: 'declined', candidateDeclineReason: reason.trim(), meetingSlot: '', candidateReply: 'pending', candidateTimeRequest: '' });
    setSelectedSlot('');
    setModal(null);
    notify('Отказ отмечен только для этой роли. Поиск других предложений продолжается.');
  }

  function confirmMeeting() {
    if (!eligibleToBook || !slots.includes(selectedSlot)) return;
    update({ meetingSlot: selectedSlot, candidateReply: 'confirmed', candidateTimeRequest: '' });
    setEditingBooking(false);
    notify('Время встречи выбрано в этом просмотре.');
  }

  function requestOtherTime(event: FormEvent) {
    event.preventDefault();
    if (reschedule.trim().length < 5) {
      setRescheduleError('Напишите удобный день и примерное время.');
      return;
    }
    if (!eligibleToBook) return;
    update({ candidateReply: 'reschedule', candidateTimeRequest: reschedule.trim(), meetingSlot: '' });
    setSelectedSlot('');
    setModal(null);
    setEditingBooking(false);
    notify('Пожелание по времени сохранено в этом просмотре.');
  }

  const interestLabel = state.candidateInterest === 'interested' ? 'Вам интересно' : state.candidateInterest === 'declined' ? 'Вы отказались от роли' : 'Ждём вашего ответа';

  return (
    <div className="agc-workspace">
      <Tabs value={section} onValueChange={value => showSection(value as Section)} className="agc-layout">
        <aside className="agc-navigation" aria-label="Кабинет кандидата">
          <div className="agc-person"><span className="agc-avatar" aria-hidden="true">АМ</span><div><strong>{state.profile.name}</strong><span>Ваш поиск работы</span></div></div>
          <TabsList className="agc-tabs" aria-label="Разделы кабинета кандидата">
            <TabsTrigger value="offer"><FileText size={17} />Предложение</TabsTrigger>
            <TabsTrigger value="profile"><UserRound size={17} />Мой профиль</TabsTrigger>
            <TabsTrigger value="next"><CalendarDays size={17} />Следующий шаг{eligibleToBook && state.candidateReply === 'pending' && <span className="agc-unread" aria-label="Доступно приглашение" />}</TabsTrigger>
          </TabsList>
          <div className="agc-nav-note"><span className="agc-tiny-mark" aria-hidden="true">✦</span><p>Вы выбираете, с какой компанией продолжать общение.</p></div>
        </aside>

        <TabsContent value="offer" className="agc-content">
          <header className="agc-page-header"><p className="agc-eyebrow">Личное приглашение</p><h1>Возможность в Сфере</h1><p>Роль, в которой может пригодиться ваш опыт развития B2B-клиентов.</p></header>
          <div className="agc-offer-layout">
            <div className="agc-offer-main">
              <div className="agc-company-line"><span className="agc-company-mark" aria-hidden="true">с.</span><div><strong>Сфера</strong><span>Поставки для бизнеса</span></div><span className={`agc-status ${state.candidateInterest === 'interested' ? 'agc-status-active' : ''}`}>{state.candidateInterest === 'interested' && <Check size={12} />}{interestLabel}</span></div>
              <h2 className="agc-role-title">Менеджер по работе с клиентами</h2>
              <p className="agc-role-subtitle">Развивать текущих клиентов и возвращать повторные продажи.</p>
              <section className="agc-reading-section"><h3>Зачем компании нужен человек</h3><p>Часть клиентов перестала покупать. Компания хочет понять причины, восстановить отношения и сделать повторные продажи регулярными.</p><p>Вы будете работать с текущей базой: разбирать ситуацию клиента, предлагать следующий шаг и доводить договорённости до сделки.</p></section>
              <section className="agc-reading-section"><h3>Почему предложили вам</h3><p>На интервью вы рассказывали, как возвращали неактивных клиентов и вели переговоры с несколькими участниками сделки. Этот опыт близок к задаче Сферы.</p><div className="agc-fit-note"><span aria-hidden="true">↗</span><p>На встрече важно проверить, подходят ли вам сама задача, руководитель и способ расчёта бонуса.</p></div></section>
              <section className="agc-reading-section"><h3>Что ожидают на старте</h3><ul className="agc-plain-list"><li>Разобраться, почему клиенты перестали покупать.</li><li>Согласовать с руководителем план возвращения клиентов.</li><li>Начать работу с базой и отслеживать результаты в CRM.</li></ul><p className="agc-muted">Конкретные KPI и ресурсы нужно согласовать до принятия оффера.</p></section>
              <div className="agc-interest-section">
                {state.candidateInterest === 'unknown' && <><h3>Хотите узнать больше об этой роли?</h3><p>Интерес к роли ещё не обязывает принимать оффер.</p><div className="agc-actions"><Button onClick={acceptInterest}>Мне интересно<ArrowRight /></Button><Button variant="ghost" onClick={() => { setReason(''); setModal('decline'); }}>Не подходит</Button></div></>}
                {state.candidateInterest === 'interested' && <><div className="agc-inline-success"><CheckCircle2 size={19} /><strong>Вы готовы продолжить знакомство</strong></div><p>Посмотрите, что происходит дальше и нужно ли что-то от вас.</p><div className="agc-actions"><Button onClick={() => showSection('next')}>Следующий шаг<ArrowRight /></Button><Button variant="ghost" onClick={() => { setReason(''); setModal('decline'); }}>Роль больше не интересна</Button></div></>}
                {state.candidateInterest === 'declined' && <><h3>Эта роль вам не подошла</h3><p>Это не останавливает поиск других предложений. Если решение изменится, можно вернуться к разговору.</p><Button variant="outline" onClick={acceptInterest}>Рассмотреть ещё раз</Button></>}
              </div>
            </div>
            <aside className="agc-offer-aside" aria-label="Условия роли"><h3>Условия</h3><dl className="agc-conditions"><div><dt>Доход</dt><dd>150–180 тыс. ₽<span>На руки + бонус</span></dd></div><div><dt><MapPin size={14} />Место работы</dt><dd>Москва<span>Гибридный формат</span></dd></div><div><dt><Clock3 size={14} />Начало работы</dt><dd>По договорённости<span>Учтём ваш срок выхода</span></dd></div></dl><div className="agc-aside-note"><h4>Пока нужно уточнить</h4><p>Как устроен бонус, какой портфель клиентов передадут и какие ресурсы будут у команды.</p></div><button className="agc-text-link" onClick={() => showSection('profile')}>Проверить мой профиль<ArrowRight size={15} /></button></aside>
          </div>
        </TabsContent>

        <TabsContent value="profile" className="agc-content">
          <header className="agc-page-header"><p className="agc-eyebrow">Ваши данные и ожидания</p><h1>Мой профиль</h1><p>Мы уже знакомы с вашим опытом. Проверьте, всё ли актуально для нового предложения.</p></header>
          <div className="agc-profile-layout"><form onSubmit={saveProfile} className="agc-profile-form" noValidate>
            {(Object.keys(profileLabels) as (keyof CandidateProfile)[]).map(key => <div className="agc-field" key={key}><label htmlFor={`agc-${key}`}>{profileLabels[key]}</label>{key === 'experience' ? <Textarea id={`agc-${key}`} value={draft[key]} maxLength={1500} onChange={event => setDraft(current => ({ ...current, [key]: event.target.value }))} aria-invalid={Boolean(errors[key])} aria-describedby={errors[key] ? `agc-error-${key}` : undefined} /> : <Input id={`agc-${key}`} value={draft[key]} maxLength={key === 'name' ? 100 : 250} onChange={event => setDraft(current => ({ ...current, [key]: event.target.value }))} aria-invalid={Boolean(errors[key])} aria-describedby={errors[key] ? `agc-error-${key}` : undefined} autoComplete={key === 'name' ? 'name' : 'off'} />}{errors[key] && <p className="agc-error" id={`agc-error-${key}`}>{errors[key]}</p>}</div>)}
            <label className="agc-consent"><input type="checkbox" checked={share} onChange={event => setShare(event.target.checked)} /><span>Разрешаю показать обновлённый профиль компании «Сфера»<small>Для других компаний решение принимается отдельно.</small></span></label>
            <div className="agc-save-row"><Button type="submit" disabled={!changed && state.profileSaved}>Сохранить профиль</Button>{state.profileSaved && !changed && <span className="agc-saved"><Check size={15} />Изменения сохранены</span>}{changed && <span className="agc-muted">Есть несохранённые изменения</span>}</div>
          </form><aside className="agc-interview-aside"><span className="agc-icon-disc"><MessageCircle size={21} /></span><h3>Не нужно начинать с нуля</h3><p>В вашем профиле уже есть материалы первого интервью. Их можно использовать при обсуждении новой роли.</p><Button variant="outline" onClick={() => setModal('interview')}>Посмотреть материалы</Button><hr /><h4>Есть что добавить?</h4><p>Новые результаты или изменившиеся ожидания можно обсудить с рекрутером.</p><Button variant="ghost" className="agc-wrapped-button" disabled={state.candidateInterview === 'hr-requested'} onClick={() => { update({ candidateInterview: 'hr-requested' }); notify('Пожелание поговорить с рекрутером сохранено в этом просмотре.'); }}>{state.candidateInterview === 'hr-requested' ? <><Check size={15} />Беседа с рекрутером выбрана</> : 'Хочу поговорить с рекрутером'}</Button><p className="agc-small-note">AI-интервью пока недоступно. Когда ссылка будет готова, она появится здесь.</p></aside></div>
        </TabsContent>

        <TabsContent value="next" className="agc-content">
          <header className="agc-page-header"><p className="agc-eyebrow">Сфера · менеджер по работе с клиентами</p><h1>Следующий шаг</h1><p>Здесь видно, где вы сейчас в процессе и что произойдёт дальше.</p></header>
          <div className="agc-next-layout"><div className="agc-next-main">
            {state.candidateInterest === 'declined' ? <section className="agc-current-step"><span className="agc-step-kicker">Ваше решение</span><h2>Вы отказались от этой роли</h2><p>Поиск других предложений продолжается. К этому варианту можно вернуться, если ваши ожидания изменятся.</p><Button variant="outline" onClick={() => showSection('offer')}>Вернуться к предложению</Button></section>
              : decision.kind === 'decline' ? <section className="agc-current-step"><span className="agc-step-kicker">Решение компании</span><h2>По этой роли компания не продолжает общение</h2><p>{decision.reason || 'Причина пока не указана. Её стоит уточнить, чтобы учесть в следующем подборе.'}</p><p className="agc-muted">Это решение по одной роли. Ваш опыт может подойти для другой задачи.</p></section>
              : state.candidateInterest !== 'interested' ? <section className="agc-current-step"><span className="agc-step-kicker">Сейчас ваш ход</span><h2>Сначала решите, интересна ли вам роль</h2><p>Посмотрите задачу компании и условия. Вы можете согласиться на дальнейший разговор или отказаться только от этого предложения.</p><Button onClick={() => showSection('offer')}>Посмотреть предложение<ArrowRight /></Button></section>
              : decision.kind !== 'invite' ? <section className="agc-current-step"><span className="agc-step-kicker"><Clock3 size={15} />Ждём компанию</span><h2>Ваш интерес отмечен</h2><p>Нанимающий менеджер знакомится с подборкой. Когда он будет готов к встрече, здесь появятся доступные варианты времени.</p><div className="agc-soft-note">Вы можете продолжать рассматривать другие предложения. Здесь пока ничего делать не нужно.</div></section>
              : state.candidateReply === 'confirmed' && !editingBooking ? <section className="agc-current-step"><span className="agc-step-kicker"><CheckCircle2 size={16} />Вы выбрали время</span><h2>{state.meetingSlot}</h2><p>Знакомство с нанимающим менеджером Сферы. Обсудите задачу, ваш опыт и ожидания от сотрудничества.</p><div className="agc-soft-note">Ссылка на видеовстречу пока не добавлена. Рекрутер уточнит детали.</div><div className="agc-actions"><Button variant="outline" onClick={() => setEditingBooking(true)}>Изменить время</Button><Button variant="ghost" onClick={() => { setReschedule(''); setRescheduleError(''); setModal('reschedule'); }}>Нужно другое время</Button></div></section>
              : state.candidateReply === 'reschedule' && !editingBooking ? <section className="agc-current-step"><span className="agc-step-kicker">Подбираем другое время</span><h2>Ваши пожелания записаны</h2><blockquote>{state.candidateTimeRequest}</blockquote><p>Когда появятся новые варианты, вы сможете выбрать подходящий.</p><Button variant="outline" onClick={() => setEditingBooking(true)}>Вернуться к доступным вариантам</Button></section>
              : <section className="agc-current-step"><span className="agc-step-kicker">Компания приглашает на встречу</span><h2>Познакомьтесь с будущим руководителем</h2><p>Это взаимное знакомство: компания узнает больше о вас, а вы сможете проверить, подходит ли вам роль.</p><fieldset className="agc-slots"><legend>Какое время вам удобно?</legend><div role="radiogroup" aria-label="Доступное время встречи">{slots.map((slot, index) => <button key={slot} type="button" role="radio" tabIndex={selectedSlot === slot || (!selectedSlot && index === 0) ? 0 : -1} aria-checked={selectedSlot === slot} onKeyDown={event => { const direction = ['ArrowRight', 'ArrowDown'].includes(event.key) ? 1 : ['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 0; if (!direction && event.key !== 'Home' && event.key !== 'End') return; event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? slots.length - 1 : (index + direction + slots.length) % slots.length; setSelectedSlot(slots[next]); (event.currentTarget.parentElement?.querySelectorAll('button')[next] as HTMLButtonElement | undefined)?.focus(); }} className={selectedSlot === slot ? 'agc-slot agc-slot-selected' : 'agc-slot'} onClick={() => setSelectedSlot(slot)}><CalendarDays size={17} /><span>{slot}</span><span className="agc-radio-mark">{selectedSlot === slot && <span />}</span></button>)}</div></fieldset><div className="agc-actions"><Button disabled={!slots.includes(selectedSlot)} onClick={confirmMeeting}>Подтвердить время</Button><Button variant="ghost" onClick={() => { setReschedule(''); setRescheduleError(''); setModal('reschedule'); }}>Ничего не подходит</Button></div></section>}
            {state.candidateInterest !== 'declined' && <form className="agc-question-form" onSubmit={event => { event.preventDefault(); if (!question.trim()) return; update({ candidateQuestion: question.trim() }); notify('Вопрос сохранён в этом просмотре.'); }}><label htmlFor="agc-question">Что хотели бы уточнить до встречи?</label><Textarea id="agc-question" placeholder="Например, как устроен бонус и сколько клиентов будет в портфеле" value={question} maxLength={1000} onChange={event => setQuestion(event.target.value)} /><Button variant="outline" type="submit" disabled={!question.trim() || question.trim() === state.candidateQuestion}>Сохранить вопрос</Button><p className="agc-small-note">В этом просмотре вопрос сохраняется в процессе. Сообщения никому не отправляются.</p></form>}
          </div><aside className="agc-progress"><h3>Ваш путь к решению</h3><ol><li className={state.candidateInterest === 'interested' ? 'agc-step-done' : ''}><span>{state.candidateInterest === 'interested' ? <Check size={13} /> : '1'}</span><div><strong>Выбрать, интересна ли роль</strong><p>Задача, условия и ваши ожидания</p></div></li><li className={eligibleToBook && state.candidateReply === 'confirmed' ? 'agc-step-done' : ''}><span>{eligibleToBook && state.candidateReply === 'confirmed' ? <Check size={13} /> : '2'}</span><div><strong>Договориться о встрече</strong><p>Когда обе стороны готовы</p></div></li><li><span>3</span><div><strong>Познакомиться и решить</strong><p>Подходит ли вам эта работа</p></div></li></ol><p className="agc-progress-note">До оффера вы ничего не обещаете. Ваше решение так же важно, как решение компании.</p></aside></div>
        </TabsContent>
      </Tabs>

      <Dialog open={modal !== null} onOpenChange={open => { if (!open) setModal(null); }}><DialogContent className="agency-product-theme agc-dialog">
        {modal === 'decline' && <><DialogHeader><DialogTitle>Что не подошло в этой роли?</DialogTitle><DialogDescription>Ответ поможет точнее подбирать следующие предложения. Отказ касается только Сферы.</DialogDescription></DialogHeader><div className="agc-field"><label htmlFor="agc-decline-reason">Причина, если хотите поделиться</label><Textarea id="agc-decline-reason" value={reason} maxLength={1000} onChange={event => setReason(event.target.value)} placeholder="Задачи, условия или что-то ещё" /></div><DialogFooter><Button variant="outline" onClick={() => setModal(null)}>Вернуться</Button><Button onClick={declineRole}>Отказаться от этой роли</Button></DialogFooter></>}
        {modal === 'interview' && <><DialogHeader><DialogTitle>Материалы первого интервью</DialogTitle><DialogDescription>Выдержки из рассказа о вашем опыте. Видеозапись в этом просмотре не подключена.</DialogDescription></DialogHeader><div className="agc-interview-excerpts">{candidate.evidence.map(item => <section key={item.title}><h3>{item.title}</h3><blockquote>«{item.fragment}»</blockquote><p>{item.source}</p></section>)}</div><DialogFooter><Button variant="outline" onClick={() => setModal(null)}>Закрыть</Button></DialogFooter></>}
        {modal === 'reschedule' && <form onSubmit={requestOtherTime}><DialogHeader><DialogTitle>Когда вам удобно встретиться?</DialogTitle><DialogDescription>Укажите несколько вариантов и часовой пояс. Текущая встреча будет заменена пожеланием о новом времени.</DialogDescription></DialogHeader><div className="agc-field agc-dialog-field"><label htmlFor="agc-other-time">Ваши варианты</label><Textarea id="agc-other-time" value={reschedule} maxLength={500} onChange={event => setReschedule(event.target.value)} placeholder="Например, 9 октября после 14:00 по Москве" aria-invalid={Boolean(rescheduleError)} aria-describedby={rescheduleError ? 'agc-time-error' : undefined} />{rescheduleError && <p className="agc-error" id="agc-time-error">{rescheduleError}</p>}</div><DialogFooter><Button type="button" variant="outline" onClick={() => setModal(null)}>Отмена</Button><Button type="submit">Сохранить варианты</Button></DialogFooter></form>}
      </DialogContent></Dialog>
    </div>
  );
}
