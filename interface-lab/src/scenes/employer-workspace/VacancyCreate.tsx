import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { Button } from '../../components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog';
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import './vacancy-create.css';

export type VacancyCreateValues = {
  title: string;
  company: string;
  city: string;
  remote: boolean;
  format: string;
  salary: string;
  description: string;
};

type VacancyCreateProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  busy: boolean;
  error: string;
  onCreate: (values: VacancyCreateValues) => Promise<boolean>;
  company?: string;
  initialDescription?: string;
};

const emptyValues = (company: string): VacancyCreateValues => ({
  title: '', company, city: '', remote: false, format: '', salary: '', description: '',
});

export function VacancyCreate({ open, onOpenChange, busy, error, onCreate, company = '', initialDescription = '' }: VacancyCreateProps) {
  const id = useId();
  const [values, setValues] = useState(() => ({ ...emptyValues(company), description: initialDescription }));
  const [companyEdited, setCompanyEdited] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [localError, setLocalError] = useState('');
  const locked = useRef(false);
  const titleInput = useRef<HTMLInputElement>(null);
  const companyInput = useRef<HTMLInputElement>(null);
  const saving = busy || submitting;

  useEffect(() => {
    if (!companyEdited) setValues(previous => ({ ...previous, company }));
  }, [company, companyEdited]);

  function edit(key: keyof Omit<VacancyCreateValues, 'remote'>, value: string) {
    setValues(previous => ({ ...previous, [key]: value, ...(key === 'format' ? { remote: value === 'Удалённо' } : {}) }));
    if (key === 'company') setCompanyEdited(true);
    setLocalError('');
  }

  function changeOpen(next: boolean) {
    if (saving || locked.current) return;
    setLocalError('');
    onOpenChange(next);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || locked.current) return;
    if (!values.title.trim() || !values.company.trim()) {
      const missingTitle = !values.title.trim();
      setLocalError(missingTitle ? 'Укажите название вакансии.' : 'Укажите компанию.');
      (missingTitle ? titleInput : companyInput).current?.focus();
      return;
    }
    locked.current = true;
    setSubmitting(true);
    setLocalError('');
    try {
      const created = await onCreate({
        title: values.title.trim(), company: values.company.trim(), city: values.city.trim(),
        remote: values.remote, format: values.format, salary: values.salary.trim(), description: values.description.trim(),
      });
      if (created) {
        setValues(emptyValues(company));
        setCompanyEdited(false);
        onOpenChange(false);
      }
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : 'Не удалось создать вакансию. Попробуйте ещё раз.');
    } finally {
      locked.current = false;
      setSubmitting(false);
    }
  }

  const visibleError = localError || error;
  return <Dialog open={open} onOpenChange={changeOpen}>
    <DialogContent className="ew-root ew-vacancy-create" showCloseButton={!saving} onOpenAutoFocus={event => { event.preventDefault(); titleInput.current?.focus(); }}>
      <DialogHeader>
        <DialogTitle>Создать вакансию</DialogTitle>
        <DialogDescription className="sr-only">Укажите название вакансии и компанию. Остальные поля можно заполнить по желанию.</DialogDescription>
      </DialogHeader>
      <form onSubmit={submit} aria-busy={saving}>
        <fieldset disabled={saving} className="ew-vacancy-create-fields">
          <label htmlFor={`${id}-title`} className="ew-vacancy-create-wide">Название вакансии<Input ref={titleInput} id={`${id}-title`} required maxLength={200} value={values.title} onChange={event => edit('title', event.target.value)} placeholder="Менеджер по работе с клиентами" autoComplete="off"/></label>
          <label htmlFor={`${id}-company`} className="ew-vacancy-create-wide">Компания<Input ref={companyInput} id={`${id}-company`} required maxLength={200} value={values.company} onChange={event => edit('company', event.target.value)} autoComplete="organization"/></label>
          <label htmlFor={`${id}-city`}>Город<Input id={`${id}-city`} maxLength={120} value={values.city} onChange={event => edit('city', event.target.value)} autoComplete="address-level2" placeholder="Необязательно"/></label>
          <label htmlFor={`${id}-format`}>Формат работы<select id={`${id}-format`} value={values.format} onChange={event => edit('format', event.target.value)}><option value="">Не указан</option><option>В офисе</option><option>Гибрид</option><option>Удалённо</option></select></label>
          <label htmlFor={`${id}-salary`} className="ew-vacancy-create-wide">Зарплата, ₽<Input id={`${id}-salary`} maxLength={300} value={values.salary} onChange={event => edit('salary', event.target.value)} placeholder="Например, 150 000–180 000"/></label>
          <label htmlFor={`${id}-description`} className="ew-vacancy-create-wide">Описание<Textarea id={`${id}-description`} rows={4} maxLength={10000} value={values.description} onChange={event => edit('description', event.target.value)} placeholder="Задачи, требования и условия работы"/></label>
        </fieldset>
        {visibleError && <p className="ew-vacancy-create-error" role="alert">{visibleError}</p>}
        <DialogFooter className="ew-vacancy-create-footer">
          <Button type="button" variant="ghost" disabled={saving} onClick={() => changeOpen(false)}>Отмена</Button>
          <Button type="submit" disabled={saving}>{saving ? 'Создаём…' : 'Создать вакансию'}</Button>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>;
}
