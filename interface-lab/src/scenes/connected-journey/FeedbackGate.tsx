import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, Loader2 } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Textarea } from '../../components/ui/textarea';
import { request, type FeedbackAnswer, type Journey, type Person, type Questionnaire } from './api';

export const answerLabels = { clear: 'Убедительный ответ', unclear: 'Нужно уточнить', unanswered: 'Ответа не было', not_asked: 'Не обсуждали' };
type Result = { status: 'ready' | 'processing' | 'missing' | 'unavailable'; questionnaire: Questionnaire | null; draft?: Record<string, FeedbackAnswer> };
const time = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

export function FeedbackGate({ journey, person, remaining, onSaved }: { journey: Journey; person: Person; remaining: number; onSaved: () => void }) {
  const m = person.meeting!;
  const { token, state, busy, act } = journey;
  const identity = { candidate: person.id, meetingId: m.id, generation: state!.generation };
  const [q, setQ] = useState<Questionnaire | null>(m.questionnaire || null);
  const [answers, setAnswers] = useState<Record<string, FeedbackAnswer>>(m.feedbackDraft || {});
  const [index, setIndex] = useState(0);
  const [loading, setLoading] = useState(!m.questionnaire);
  const [status, setStatus] = useState<Result['status']>('processing');
  const [manual, setManual] = useState('');
  const [saveStatus, setSaveStatus] = useState('');
  const [loadError, setLoadError] = useState('');
  const [dirty, setDirty] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const requestId = useRef(0);
  const loadPending = useRef(false);
  const saveQueue = useRef<Promise<unknown>>(Promise.resolve());
  useEffect(() => { heading.current?.focus(); }, [index]);
  useEffect(() => {
    if (m.questionnaire && !q) { setQ(m.questionnaire); setLoading(false); }
  }, [m.questionnaire, q]);
  async function load() {
    if (loadPending.current) return;
    loadPending.current = true;
    const id = ++requestId.current;
    setLoading(true); setLoadError('');
    try {
      const result = await request<Result>('feedback/questions', token, identity);
      if (requestId.current !== id) return;
      setStatus(result.status);
      if (result.questionnaire) { setQ(result.questionnaire); setAnswers(result.draft || {}); }
    } catch (e) { if (requestId.current === id) setLoadError((e as Error).message); }
    finally { loadPending.current = false; if (requestId.current === id) setLoading(false); }
  }
  useEffect(() => {
    if (q) return;
    void load();
    const timer = setInterval(() => { void load(); }, 20000);
    return () => { clearInterval(timer); requestId.current++; };
  }, [q?.id, m.id]);
  function saveDraft(value: Record<string, FeedbackAnswer>) {
    setSaveStatus('Сохраняем…');
    const queued = saveQueue.current.catch(() => {}).then(() => request('action', token, { ...identity, action: 'feedback.draft', questionnaireId: q!.id, answers: value }));
    saveQueue.current = queued;
    void queued.then(() => setSaveStatus('Черновик сохранён')).catch(() => setSaveStatus('Черновик не сохранён. Проверьте соединение.'));
    return queued;
  }
  useEffect(() => {
    if (!q || !dirty) return;
    const timer = setTimeout(() => { void saveDraft(answers).catch(() => {}); }, 700);
    return () => clearTimeout(timer);
  }, [answers, q?.id, dirty]);
  const current = q?.questions[index];
  const answer = current ? answers[current.id] : undefined;
  const update = (value: FeedbackAnswer) => { setAnswers(a => ({ ...a, [current!.id]: value })); setDirty(true); };
  async function next() {
    if (!q || !answer || submitting) return;
    setSubmitting(true);
    try {
      await saveDraft(answers);
      setDirty(false);
      if (index + 1 < q.questions.length) setIndex(index + 1);
      else if (await act('feedback', person.id, { meetingId: m.id, questionnaireId: q.id, answers })) onSaved();
    } catch { /* The draft status shows the recoverable failure. */ }
    finally { setSubmitting(false); }
  }
  return <main className="cj-feedback-stage" aria-label="Обязательная обратная связь после встречи">
    <div className="cj-feedback-heading"><span>{person.name} · встреча завершена</span>{remaining > 1 && <small>Ещё встреч: {remaining - 1}</small>}</div>
    <h1 ref={heading} tabIndex={-1}>Зафиксируем итоги встречи</h1>
    {!q ? <section className="cj-feedback-loading">
      <p role="status">{loading ? <><Loader2 size={17} className="cj-spin"/>Готовим вопросы по разговору…</> : status === 'processing' ? 'Расшифровка обрабатывается. Вопросы появятся здесь автоматически.' : status === 'unavailable' ? 'Сейчас не удаётся получить расшифровку.' : 'В расшифровке пока не удалось выделить ваши вопросы.'}</p>
      {loadError && <p role="alert">{loadError}</p>}
      {!loading && <Button variant="outline" onClick={load}>Проверить ещё раз</Button>}
      <details className="cj-question-fallback"><summary>Записать заданные вопросы вручную</summary><p>Укажите только то, что вы спрашивали на встрече. Каждый вопрос с новой строки.</p><form onSubmit={async e => { e.preventDefault(); await act('feedback.questions', person.id, { meetingId: m.id, questions: manual }); }}><Textarea aria-label="Вопросы, заданные на встрече" required value={manual} onChange={e => setManual(e.target.value)}/><Button type="submit" disabled={busy || !manual.trim()}>Продолжить по этим вопросам</Button></form></details>
    </section> : current && <form onSubmit={e => { e.preventDefault(); void next(); }} className="cj-feedback-card">
      <div className="cj-feedback-progress"><span>Вопрос {index + 1} из {q.questions.length}</span><span>{current.source === 'manual' ? 'Указан вами' : `Из разговора${current.seconds === null ? '' : ` · ${time(current.seconds)}`}`}</span></div>
      <blockquote>{current.question}</blockquote>
      <fieldset><legend>Как кандидат ответил?</legend><div className="cj-answer-options">{Object.entries(answerLabels).map(([rating, label]) => <label key={rating} className={answer?.rating === rating ? 'is-selected' : ''}><input type="radio" name={current.id} value={rating} checked={answer?.rating === rating} onChange={() => update({ rating: rating as FeedbackAnswer['rating'], comment: answer?.comment || '' })}/><span>{label}</span>{answer?.rating === rating && <Check size={16}/>}</label>)}</div></fieldset>
      <label className="cj-field cj-feedback-comment">Пояснение <span>необязательно</span><Textarea rows={2} maxLength={3000} value={answer?.comment || ''} disabled={!answer} onChange={e => update({ ...answer!, comment: e.target.value })} placeholder="Что убедило или осталось неясным" onBlur={() => { if (dirty) void saveDraft(answers).catch(() => {}); }}/></label>
      <div className="cj-feedback-controls"><Button type="button" variant="ghost" disabled={index === 0 || busy || submitting} onClick={() => setIndex(index - 1)}><ArrowLeft size={16}/>Назад</Button><Button type="submit" disabled={!answer || busy || submitting}>{busy || submitting ? 'Сохраняем…' : index + 1 === q.questions.length ? 'Сохранить итоги' : 'Далее'}<ArrowRight size={16}/></Button></div>
      <p className="cj-draft-status" role="status">{saveStatus || 'Ответьте на вопросы, чтобы перейти к разбору встречи.'}</p>
    </form>}
  </main>;
}
