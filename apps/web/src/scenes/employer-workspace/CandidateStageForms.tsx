import { useId, useRef, useState, type FormEvent } from 'react';
import { X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import type { CandidateOfferValues, CandidateStageDocumentInput, CandidateTestValues } from './candidate-stage-types';
import './candidate-stage-card.css';

type FormProps<T> = {
  initialValues?: Partial<T>; onSubmit: (values: T) => Promise<boolean>;
  onCancel: () => void; busy?: boolean; error?: string;
};
function useStageForm<T>(initial: T, onSubmit: (values: T) => Promise<boolean>, busy?: boolean) {
  // These values are a snapshot at editor opening. Polling cannot replace a user's draft.
  const [values, setValues] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (busy || pending.current) return;
    pending.current = true; setSaving(true); setError('');
    try { if (!await onSubmit(values)) setError('Не удалось сохранить. Ваши изменения остались в форме.'); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось сохранить изменения.'); }
    finally { pending.current = false; setSaving(false); }
  };
  return { values, setValues, submit, saving, error };
}
const moscowInput = (value?: string | null) => {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(date).map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
};

export function CandidateTestForm({ initialValues, onSubmit, onCancel, busy, error, editing = false }: FormProps<CandidateTestValues> & { editing?: boolean }) {
  const id = useId();
  const form = useStageForm<CandidateTestValues>({ title: '', instructions: '', dueAt: '', note: '', documents: [], ...initialValues }, onSubmit, busy);
  const { values, setValues } = form;
  const disabled = busy || form.saving;
  return <form className="esc-form" onSubmit={form.submit} aria-labelledby={`${id}-heading`}>
    <h3 id={`${id}-heading`}>{editing ? 'Изменить тестовое' : 'Новое тестовое'}</h3>
    <label htmlFor={`${id}-title`}>Задание<Input autoFocus id={`${id}-title`} required maxLength={240} value={values.title} disabled={disabled} onChange={event => setValues({ ...values, title: event.target.value })}/></label>
    <label htmlFor={`${id}-instructions`}>Что нужно сделать<Textarea id={`${id}-instructions`} maxLength={12000} value={values.instructions} disabled={disabled} onChange={event => setValues({ ...values, instructions: event.target.value })}/></label>
    <label htmlFor={`${id}-due`}>Срок · МСК<Input id={`${id}-due`} type="datetime-local" value={moscowInput(values.dueAt)} disabled={disabled} onChange={event => setValues({ ...values, dueAt: event.target.value ? `${event.target.value}:00+03:00` : '' })}/></label>
    <StageDocumentsEditor documents={values.documents} onChange={documents => setValues({ ...values, documents })} disabled={disabled}/>
    {(error || form.error) && <p className="esc-error" role="alert">{error || form.error}</p>}
    <div className="esc-form-actions"><Button type="submit" disabled={disabled}>{form.saving ? 'Сохраняем…' : editing ? 'Сохранить задание' : 'Добавить тестовое'}</Button><Button type="button" variant="ghost" disabled={form.saving} onClick={onCancel}>Отмена</Button></div>
  </form>;
}

export function CandidateOfferForm({ initialValues, onSubmit, onCancel, busy, error }: FormProps<CandidateOfferValues>) {
  const id = useId();
  const form = useStageForm<CandidateOfferValues>({ role: '', compensation: '', startDate: '', format: '', expectations: '', documents: [], ...initialValues }, onSubmit, busy);
  const { values, setValues } = form;
  const disabled = busy || form.saving;
  return <form className="esc-form" onSubmit={form.submit} aria-labelledby={`${id}-heading`}>
    <h3 id={`${id}-heading`}>Подготовить оффер</h3>
    <label htmlFor={`${id}-role`}>Должность<Input autoFocus id={`${id}-role`} required maxLength={240} value={values.role} disabled={disabled} onChange={event => setValues({ ...values, role: event.target.value })}/></label>
    <div className="esc-form-grid"><label htmlFor={`${id}-compensation`}>Зарплата в месяц<Input id={`${id}-compensation`} required maxLength={240} value={values.compensation} disabled={disabled} onChange={event => setValues({ ...values, compensation: event.target.value })}/></label>
      <label htmlFor={`${id}-start`}>Дата выхода<Input id={`${id}-start`} type="date" required value={values.startDate} disabled={disabled} onChange={event => setValues({ ...values, startDate: event.target.value })}/></label></div>
    <label htmlFor={`${id}-format`}>Формат работы<Input id={`${id}-format`} required maxLength={240} value={values.format} disabled={disabled} onChange={event => setValues({ ...values, format: event.target.value })}/></label>
    <label htmlFor={`${id}-expectations`}>Ожидания<Textarea id={`${id}-expectations`} required maxLength={12000} value={values.expectations} disabled={disabled} onChange={event => setValues({ ...values, expectations: event.target.value })}/></label>
    <StageDocumentsEditor documents={values.documents} onChange={documents => setValues({ ...values, documents })} disabled={disabled}/>
    {(error || form.error) && <p className="esc-error" role="alert">{error || form.error}</p>}
    <div className="esc-form-actions"><Button type="submit" disabled={disabled}>{form.saving ? 'Сохраняем…' : 'Сохранить оффер'}</Button><Button type="button" variant="ghost" disabled={form.saving} onClick={onCancel}>Отмена</Button></div>
  </form>;
}

function StageDocumentsEditor({ documents, onChange, disabled }: { documents: CandidateStageDocumentInput[]; onChange: (documents: CandidateStageDocumentInput[]) => void; disabled?: boolean }) {
  const id = useId();
  const update = (index: number, values: Partial<CandidateStageDocumentInput>) => onChange(documents.map((document, item) => item === index ? { ...document, ...values } : document));
  return <div className="esc-document-editor">
    {documents.map((document, index) => <div className="esc-document-fields" key={document.id || index}>
      <label htmlFor={`${id}-${index}-title`}>Название материала<Input id={`${id}-${index}-title`} required maxLength={240} value={document.title} disabled={disabled} onChange={event => update(index, { title: event.target.value })}/></label>
      <label htmlFor={`${id}-${index}-url`}>Ссылка на материал<Input id={`${id}-${index}-url`} required type="url" pattern="https?://.+" maxLength={2000} value={document.url} disabled={disabled} onChange={event => update(index, { url: event.target.value })}/></label>
      <Button type="button" variant="ghost" size="icon" aria-label={`Удалить ссылку ${index + 1}`} disabled={disabled} onClick={() => onChange(documents.filter((_, item) => item !== index))}><X size={16}/></Button>
    </div>)}
    {documents.length < 20 && <Button className="esc-secondary" type="button" variant="ghost" disabled={disabled} onClick={() => onChange([...documents, { title: '', url: '' }])}>Добавить ссылку на материал</Button>}
  </div>;
}
