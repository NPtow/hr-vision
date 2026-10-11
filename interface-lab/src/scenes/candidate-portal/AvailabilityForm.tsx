import { useId, useState, type FormEvent } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { localDate, localTime, timeZone, type Availability } from './api';

type WindowDraft = { id: string; date: string; start: string; end: string };
export function AvailabilityForm({ initial, busy, onSubmit }: { initial: Availability[]; busy: boolean; onSubmit: (windows: Availability[]) => Promise<void> }) {
  const id = useId();
  const [windows, setWindows] = useState<WindowDraft[]>(() => initial.map((window, index) => ({ id: String(index), date: localDate(new Date(window.start)), start: localTime(window.start), end: localTime(window.end) })));
  const [error, setError] = useState('');
  const edit = (windowId: string, key: 'date' | 'start' | 'end', value: string) => setWindows(previous => previous.map(window => window.id === windowId ? { ...window, [key]: value } : window));
  async function submit(event: FormEvent) {
    event.preventDefault(); setError('');
    const dates = windows.map(window => ({ start: new Date(`${window.date}T${window.start}`), end: new Date(`${window.date}T${window.end}`) }));
    if (dates.some(window => !Number.isFinite(window.start.getTime()) || !Number.isFinite(window.end.getTime()) || window.end.getTime() - window.start.getTime() < 30 * 60000 || window.end.getTime() - window.start.getTime() > 12 * 3600000)) { setError('Укажите интервалы от 30 минут до 12 часов.'); return; }
    await onSubmit(dates.map(window => ({ start: window.start.toISOString(), end: window.end.toISOString() })));
  }
  return <form onSubmit={submit}>
    <p className="cp-muted">Работодатель предложит время в этих интервалах. Вы подтвердите его отдельно. {timeZone()}.</p>
    <div className="cp-window-list">{windows.map((window, index) => <div className="cp-window" key={window.id}>
      <label htmlFor={`${id}-date-${window.id}`}>Дата<Input id={`${id}-date-${window.id}`} type="date" required value={window.date} onChange={event => edit(window.id, 'date', event.target.value)}/></label>
      <label htmlFor={`${id}-start-${window.id}`}>С<Input id={`${id}-start-${window.id}`} type="time" required step="900" value={window.start} onChange={event => edit(window.id, 'start', event.target.value)}/></label>
      <label htmlFor={`${id}-end-${window.id}`}>До<Input id={`${id}-end-${window.id}`} type="time" required step="900" value={window.end} onChange={event => edit(window.id, 'end', event.target.value)}/></label>
      <Button type="button" variant="ghost" size="icon" disabled={busy} aria-label={`Удалить интервал ${index + 1}`} onClick={() => setWindows(previous => previous.filter(item => item.id !== window.id))}><Trash2 size={17}/></Button>
    </div>)}</div>
    <Button type="button" variant="secondary" disabled={busy || windows.length >= 40} onClick={() => setWindows(previous => [...previous, { id: crypto.randomUUID(), date: '', start: '10:00', end: '18:00' }])}><Plus size={17}/>Добавить время</Button>
    {error && <p role="alert" className="cp-error">{error}</p>}
    <div className="cp-form-actions"><Button type="submit" disabled={busy}>{busy ? 'Сохраняем…' : 'Сохранить время'}</Button></div>
  </form>;
}
