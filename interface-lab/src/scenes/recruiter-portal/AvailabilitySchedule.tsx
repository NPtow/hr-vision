import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Button } from '../../components/ui/button';
import { moscowInput, type AvailabilityBlock } from './api';

const days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const dayNames = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];
function scheduleDays(weekdays: number[]) {
  const selected = [...weekdays].sort();
  if (selected.join(',') === '0,1,2,3,4') return 'Понедельник — пятница';
  if (selected.length === 7) return 'Каждый день';
  return selected.map(day => selected.length === 1 ? dayNames[day] : days[day]).join(', ');
}
function scheduleDate(date: string) {
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'Europe/Moscow' }).format(new Date(`${date}T12:00:00+03:00`));
}

export function AvailabilitySchedule({ blocks, busy, run, recruiterName }: { blocks: AvailabilityBlock[]; busy: boolean; run: (path: string, body: unknown) => Promise<boolean>; recruiterName?: string }) {
  const [name, setName] = useState('');
  const [date, setDate] = useState(() => moscowInput(new Date()).slice(0, 10));
  const [weekdays, setWeekdays] = useState<number[]>([0, 1, 2, 3, 4]);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const pending = useRef(false);
  const dateInput = useRef<HTMLInputElement>(null);
  const editButtons = useRef(new Map<string, HTMLButtonElement>());
  const addButton = useRef<HTMLButtonElement>(null);
  const focusAfterClose = useRef<string | null>(null);
  const personal = recruiterName !== undefined;
  const ownerName = (recruiterName ?? name).trim();
  const locked = busy || submitting;
  const editing = adding || editingId !== null || blocks.length === 0;

  useEffect(() => { if (adding || editingId !== null) dateInput.current?.focus(); }, [adding, editingId]);
  useEffect(() => {
    if (!editing && focusAfterClose.current) {
      if (focusAfterClose.current === 'add') addButton.current?.focus();
      else editButtons.current.get(focusAfterClose.current)?.focus();
      focusAfterClose.current = null;
    }
  }, [editing]);

  function fillDraft(block: AvailabilityBlock) {
    setName(block.recruiterName); setDate(block.startDate); setWeekdays([...block.weekdays]);
    setStart(block.startTime); setEnd(block.endTime);
  }
  function edit(block: AvailabilityBlock) {
    fillDraft(block); setError(''); setSaved(false); setAdding(false); setEditingId(block.id);
  }
  function addInterval() {
    setName(recruiterName ?? ''); setDate(moscowInput(new Date()).slice(0, 10));
    setWeekdays([0, 1, 2, 3, 4]); setStart(''); setEnd('');
    setError(''); setSaved(false); setEditingId(null); setAdding(true);
  }
  function cancel() {
    const block = blocks.find(item => item.id === editingId);
    if (block) fillDraft(block);
    focusAfterClose.current = editingId ?? 'add';
    setEditingId(null); setAdding(false); setError(''); setSaved(false);
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (locked || pending.current) return;
    setError(''); setSaved(false);
    if (!ownerName) { setError('Укажите имя рекрутера.'); return; }
    if (!weekdays.length || !start || !end || end <= start) { setError('Выберите дни недели и интервал для интервью.'); return; }
    pending.current = true; setSubmitting(true);
    try {
      const body = { recruiterName: ownerName, startDate: date, weekdays: [...weekdays].sort(), startTime: start, endTime: end, timezone: 'Europe/Moscow', ...(editingId ? { blockId: editingId } : {}) };
      if (await run('availability', body)) {
        focusAfterClose.current = editingId ?? 'add';
        setEditingId(null); setAdding(false); setSaved(true);
      } else setError('Не удалось сохранить расписание. Проверьте данные и попробуйте ещё раз.');
    } catch { setError('Не удалось сохранить расписание. Попробуйте ещё раз.'); }
    finally { pending.current = false; setSubmitting(false); }
  }
  return <section className="rp-card rp-schedule" aria-label={personal ? 'Моё расписание' : 'Время для интервью'}>
    <h2>{personal ? 'Моё расписание' : 'Время для интервью'}</h2>
    {editing ? <form className="rp-slot-form rp-schedule-editor" onSubmit={submit} aria-busy={locked} onChange={() => { setSaved(false); setError(''); }}>
      <div className={personal ? 'rp-schedule-date' : 'rp-fields'}>{!personal && <label>Рекрутер<input required disabled={locked} value={name} onChange={event => setName(event.target.value)} maxLength={120} placeholder="Имя и фамилия"/></label>}<label>Начиная с<input ref={dateInput} required disabled={locked} type="date" value={date} onChange={event => setDate(event.target.value)}/></label></div>
      <fieldset className="rp-weekdays" disabled={locked}><legend>Дни недели</legend>{days.map((day, index) => <button type="button" key={day} aria-pressed={weekdays.includes(index)} aria-label={dayNames[index]} onClick={() => { setSaved(false); setError(''); setWeekdays(current => current.includes(index) ? current.filter(value => value !== index) : [...current, index]); }}>{day}</button>)}</fieldset>
      <div className="rp-fields"><label>С · МСК<input required disabled={locked} type="time" step={1800} value={start} onChange={event => setStart(event.target.value)}/></label><label>До · МСК<input required disabled={locked} type="time" step={1800} value={end} onChange={event => setEnd(event.target.value)}/></label></div>
      <p className="rp-muted rp-help">Интервью по 30 минут · московское время</p>
      <div className="rp-schedule-actions">{blocks.length > 0 && <Button type="button" variant="ghost" disabled={locked} onClick={cancel}>Отмена</Button>}<Button type="submit" disabled={locked || !ownerName || !date || !start || !end || !weekdays.length}>{locked ? 'Сохраняем…' : 'Сохранить расписание'}</Button></div>
      {error && <p className="rp-error" role="alert">{error}</p>}
    </form> : <div className="rp-schedule-blocks" aria-label="Постоянное расписание">{blocks.map(block => <div className="rp-schedule-block" key={block.id}>
      <div className="rp-schedule-summary"><div className="rp-schedule-rule">{!personal && <strong>{block.recruiterName}</strong>}<strong className="rp-schedule-days-label">{scheduleDays(block.weekdays)}</strong><span className="rp-schedule-time">{block.startTime}–{block.endTime}</span><span className="rp-schedule-meta">С {scheduleDate(block.startDate)} · по 30 минут · МСК</span></div><Button ref={element => { if (element) editButtons.current.set(block.id, element); else editButtons.current.delete(block.id); }} type="button" variant="secondary" disabled={locked} aria-label={`Изменить расписание: ${scheduleDays(block.weekdays)}, ${block.startTime}–${block.endTime}`} onClick={() => edit(block)}>Изменить</Button></div>
      <div className="rp-schedule-day-strip" aria-hidden="true">{days.map((day, index) => <span key={day} data-active={block.weekdays.includes(index)}>{day}</span>)}</div>
    </div>)}<Button ref={addButton} className="rp-schedule-add" type="button" variant="ghost" disabled={locked} onClick={addInterval}>Добавить интервал</Button></div>}
    {saved && <p className="rp-schedule-status" role="status">Расписание сохранено.</p>}
  </section>;
}
