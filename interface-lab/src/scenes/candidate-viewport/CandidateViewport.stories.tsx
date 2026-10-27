import type { Meta, StoryObj } from '@storybook/react-vite';
import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { ArrowLeft, ArrowUpRight, CalendarDays, Check, ChevronDown, ClipboardList, Files, Plus, RotateCcw, Users } from 'lucide-react';
import { CandidateInterviewPlayer } from '../interview-review/CandidateInterviewPlayer';
import type { RecordingChapter } from '../interview-review/InterviewReview';
import type { InterviewAssistantRequest } from '../interview-review/interview-assistant-api';
import { EmployerResumeDialog } from '../employer-workspace/EmployerResumeDialog';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import '../employer-workspace/employer-candidate-profile.css';
import './candidate-viewport.css';

type Layout = 'cabinet' | 'focus';
const transcript = [
  { start: 0, end: 2, speaker: 'Р', text: 'Расскажите о вашем опыте в продажах.' },
  { start: 2, end: 5, speaker: 'К', text: 'Я работала с постоянными клиентами: вела переговоры, готовила предложения и сопровождала сделки.' },
  { start: 5, end: 8, speaker: 'Р', text: 'Как вы работаете с возражением по цене?' },
  { start: 8, end: 11, speaker: 'К', text: 'Сначала уточняю, с чем сравнивают. Дальше обсуждаю задачу клиента, комплектацию и условия.' },
  { start: 11, end: 14, speaker: 'Р', text: 'Что для вас важно при выборе следующего места?' },
  { start: 14, end: 18, speaker: 'К', text: 'Понятная система мотивации, сильный продукт и возможность развиваться в B2B-продажах.' },
];
const stamp = (value: number) => `00:${String(value).padStart(2, '0')}`;
const transcriptText = transcript.map(row => `[${stamp(row.start)}-${stamp(row.end)}] ${row.speaker}: ${row.text}`).join('\n');
const chapters: RecordingChapter[] = [
  { title: 'Опыт и работа с клиентами', detail: 'Переговоры, предложения и сопровождение сделок.', seconds: 0, source: '00:00', fragment: '' },
  { title: 'Возражения по цене', detail: 'Как уточняет запрос и сравнивает условия.', seconds: 5, source: '00:05', fragment: '' },
  { title: 'Мотивация и следующий шаг', detail: 'Что важно при выборе новой компании.', seconds: 11, source: '00:11', fragment: '' },
];
const resume = `Демонстрационное резюме для проверки компоновки\n\nАнастасия Глухова\nМенеджер по продажам\n\nОПЫТ\nРабота с постоянными клиентами, переговоры, коммерческие предложения и сопровождение сделок.\n\nНАВЫКИ\nРабота с возражениями, выявление потребностей, CRM, B2B-продажи.\n\nОЖИДАНИЯ\nПонятная система мотивации, сильный продукт и профессиональное развитие.\n\nЭтот текст и ответы помощника используются только в макете. Они не являются новым разбором интервью кандидата.`;
const initialComments = [{ id: 'preview-note', seconds: 8, text: 'На встрече уточнить пример сделки и личный результат.', createdAt: '2026-10-21T12:00:00Z' }];

const previewAssistant: InterviewAssistantRequest = async (action, query, _id, signal) => {
  await new Promise<void>((resolve, reject) => {
    if (signal.aborted) { reject(new DOMException('Aborted', 'AbortError')); return; }
    const timer = setTimeout(resolve, 450);
    signal.addEventListener('abort', () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); }, { once: true });
  });
  if (action === 'search') {
    const text = query.toLowerCase();
    const rows = transcript.filter(row => row.speaker === 'К' && (text.includes('цен') || text.includes('возраж') ? row.start === 8 : text.includes('мотивац') ? row.start === 14 : row.start === 2));
    return { mode: 'search', matches: rows.map(row => ({ id: `preview-${row.start}`, text: row.text, startMs: row.start * 1000, endMs: row.end * 1000, speakerName: 'Кандидат' })), totalMatches: rows.length, coverage: 'entire_transcript', model: 'preview', version: 'preview' };
  }
  const objections = /возраж|цен/i.test(query);
  const row = transcript[objections ? 3 : 1];
  return { mode: 'chat', text: objections
    ? 'В примере кандидат сначала уточняет, с каким предложением сравнивают цену, затем обсуждает задачу клиента, комплектацию и условия. На встрече можно попросить разобрать конкретную сделку.'
    : 'В примере кандидат ведёт постоянных клиентов, проводит переговоры, готовит коммерческие предложения и сопровождает сделки. Для следующей встречи стоит уточнить личные результаты, объём продаж и опыт привлечения новых клиентов.',
    supported: true, evidence: [{ id: 'preview-quote', quote: row.text, startMs: row.start * 1000, speakerName: 'Кандидат' }], coverage: { selected: 6, omitted: 0 }, model: 'preview', version: 'preview' };
};

function CandidateViewport({ initialLayout = 'cabinet' }: { initialLayout?: Layout }) {
  const [layout, setLayout] = useState<Layout>(initialLayout);
  const [decision, setDecision] = useState<'invite' | 'reject' | null>(null);
  const [reason, setReason] = useState('');
  const [slot, setSlot] = useState('');
  const [notice, setNotice] = useState('');
  const [reset, setReset] = useState(0);
  const [comment, setComment] = useState('');
  const [comments, setComments] = useState(initialComments);
  const sheet = useRef<HTMLElement>(null);
  const inviteTrigger = useRef<HTMLButtonElement>(null);
  const rejectTrigger = useRef<HTMLButtonElement>(null);
  const reduced = useReducedMotion();
  useEffect(() => { if (!notice) return; const timer = setTimeout(() => setNotice(''), 3300); return () => clearTimeout(timer); }, [notice]);
  useEffect(() => { if (decision) sheet.current?.querySelector<HTMLElement>('[data-cvf-first],textarea')?.focus({ preventScroll: true }); }, [decision]);
  const closeDecision = () => { const trigger = decision === 'invite' ? inviteTrigger : rejectTrigger; setDecision(null); requestAnimationFrame(() => trigger.current?.focus({ preventScroll: true })); };
  const resetAll = () => { setReset(value => value + 1); setDecision(null); setReason(''); setSlot(''); setNotice(''); setComment(''); setComments(initialComments); };
  return <div className="cvf-preview" data-layout={layout}>
    <div className="cvf-labbar" aria-label="Управление макетом"><span>Макет компоновки</span>
      <div className="cvf-variants" role="group" aria-label="Вариант компоновки">
        <button type="button" aria-pressed={layout === 'cabinet'} onClick={() => setLayout('cabinet')}>В кабинете</button>
        <button type="button" aria-pressed={layout === 'focus'} onClick={() => setLayout('focus')}>Фокус на интервью</button>
      </div><small>Ответы и действия — демонстрационные</small><button type="button" className="cvf-reset" aria-label="Сбросить макет" onClick={resetAll}><RotateCcw size={15}/></button>
    </div>
    <div className="cvf-product">
      <header className="cvf-topbar">
        {layout === 'cabinet' ? <img src={logo} alt="HR Vision"/> : <button type="button" className="cvf-back" onClick={() => setLayout('cabinet')}><ArrowLeft size={18}/><span>Кандидаты</span></button>}
        {layout === 'focus' && <span className="cvf-focus-title">Менеджер по продажам дверей</span>}
        <button type="button" className="cvf-account" onClick={() => setNotice('Демонстрационный кабинет Никиты')}>Никита<ChevronDown size={16}/></button>
      </header>
      <div className="cvf-body">
        <nav className="cvf-navigation" aria-label="Разделы кабинета">{[{ icon: ClipboardList, label: 'Задачи' }, { icon: Users, label: 'Кандидаты' }, { icon: CalendarDays, label: 'Встречи' }, { icon: Files, label: 'Документы' }].map(({ icon: Icon, label }) => <button type="button" key={label} aria-label={label} aria-current={label === 'Кандидаты' ? 'page' : undefined} title={label} onClick={() => setNotice(label === 'Кандидаты' ? 'Вы просматриваете кандидата по вакансии' : `Раздел «${label}» — вне этого макета`)}><Icon size={22}/></button>)}</nav>
        <div className="cvf-vacancy"><h1>Менеджер по продажам дверей</h1><button type="button" onClick={() => setNotice('Добавление кандидатов — вне этого макета')}><Plus size={18}/>Добавить кандидатов</button></div>
        <main className="cvf-card" aria-label="Просмотр кандидата">
          <div className="cvf-interview">
            <CandidateInterviewPlayer key={reset} sourceId="viewport-preview" name="Анастасия Глухова" kind="screening"
              media={{ src: '/viewport-preview/candidate-interview.mp4', localPreview: true }}
              chapters={chapters} transcriptText={transcriptText} assistant={previewAssistant}
              header={<div className="ecp-video-identity"><h2>Анастасия Глухова</h2><EmployerResumeDialog text={resume}/></div>}
              canComment comments={comments}
              renderTimeComment={(seconds, close) => <form className="cvf-comment-form" onSubmit={event => { event.preventDefault(); if (!comment.trim()) return; setComments(value => [...value, { id: crypto.randomUUID(), seconds, text: comment.trim(), createdAt: new Date().toISOString() }]); setComment(''); close(); }}><textarea aria-label="Комментарий к записи" placeholder="Что хочется отметить?" value={comment} onChange={event => setComment(event.target.value)}/><div><button type="button" onClick={close}>Свернуть</button><button type="submit" disabled={!comment.trim()}>Сохранить</button></div></form>}/>
          </div>
          <footer className="cvf-decision-bar"><span>Оценка рекрутера · 4 / 5</span><div><button ref={inviteTrigger} type="button" className="cvf-invite" aria-expanded={decision === 'invite'} onClick={() => setDecision(value => value === 'invite' ? null : 'invite')}>Позвать на встречу<ArrowUpRight size={17}/></button><button ref={rejectTrigger} type="button" className="cvf-reject" aria-expanded={decision === 'reject'} onClick={() => setDecision(value => value === 'reject' ? null : 'reject')}>Отказать</button></div></footer>
          <AnimatePresence>{decision && <motion.section ref={sheet} className="cvf-decision-sheet" aria-label={decision === 'invite' ? 'Выбор времени встречи' : 'Причина отказа'} onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeDecision(); } }} initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 16 }} transition={{ duration: reduced ? 0 : .2, ease: 'easeOut' }}>
            <header><h2>{decision === 'invite' ? 'Когда вам удобно встретиться?' : 'Что не подошло?'}</h2><button type="button" onClick={closeDecision}>Свернуть<ChevronDown size={16}/></button></header>
            <form onSubmit={event => { event.preventDefault(); setNotice(decision === 'invite' ? 'В макете кандидат приглашён. Настоящее приглашение не отправлялось.' : 'В макете причина сохранена. Настоящий отказ не отправлялся.'); closeDecision(); }}>
              {decision === 'invite' ? <><p>Свободное время кандидата · МСК</p><div className="cvf-slots">{['Пн, 26 окт. · 11:00', 'Пн, 26 окт. · 15:30', 'Вт, 27 окт. · 12:00'].map((value, index) => <button type="button" key={value} data-cvf-first={index === 0 ? '' : undefined} aria-pressed={slot === value} onClick={() => setSlot(value)}>{value}</button>)}</div></>
                : <label>Короткий комментарий<textarea autoFocus required aria-label="Причина отказа" placeholder="Какого опыта не хватило для этой вакансии?" value={reason} onChange={event => setReason(event.target.value)}/></label>}
              <div className="cvf-sheet-actions"><button className="cvf-invite" type="submit" disabled={decision === 'invite' ? !slot : !reason.trim()}>{decision === 'invite' ? 'Подтвердить время' : 'Сохранить отказ'}<Check size={16}/></button><button type="button" onClick={closeDecision}>Отмена</button></div>
            </form>
          </motion.section>}</AnimatePresence>
        </main>
      </div>
    </div>
    <AnimatePresence>{notice && <motion.div className="cvf-notice" role="status" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }}><Check size={16}/>{notice}</motion.div>}</AnimatePresence>
  </div>;
}

const meta = { id: 'hr-vision-candidate-viewport', title: 'HR Vision/Кандидат — размер окна', component: CandidateViewport, parameters: { layout: 'fullscreen', controls: { disable: true } } } satisfies Meta<typeof CandidateViewport>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Cabinet: Story = { name: 'В кабинете', args: { initialLayout: 'cabinet' } };
export const Focus: Story = { name: 'Фокус на интервью', args: { initialLayout: 'focus' } };
