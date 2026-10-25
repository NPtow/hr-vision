import { useEffect, useId, useRef, useState } from 'react';
import { Button } from '../../components/ui/button';
import { Textarea } from '../../components/ui/textarea';
import type { Journey, Person } from '../connected-journey/api';
import { employerRejectionQuestions } from './EmployerCandidateResponse';

export function CandidateNextStep({ onMeeting, onOffer, onReject, busy }: {
  onMeeting: () => void; onOffer: () => void; onReject: () => void; busy?: boolean;
}) {
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => { first.current?.focus(); }, []);
  return <div className="esc-next-step" role="group" aria-label="Следующий шаг">
    <Button ref={first} disabled={busy} onClick={onMeeting}>Назначить встречу</Button>
    <Button variant="secondary" disabled={busy} onClick={onOffer}>Сделать оффер</Button>
    <Button variant="ghost" disabled={busy} onClick={onReject}>Отказать</Button>
  </div>;
}

export function CandidateStageRejection({ journey, person, onCancel, onSaved }: {
  journey: Journey; person: Person; onCancel: () => void; onSaved: () => void;
}) {
  const id = useId();
  const [values, setValues] = useState({ reason: '', missing: '', improve: '' });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const command = useRef({ revision: person.workflowRevision || 0, requestId: crypto.randomUUID(), payload: '' });
  const pending = useRef(false);
  return <form className="esc-form" onSubmit={async event => {
    event.preventDefault();
    if (pending.current || journey.busy || !values.reason.trim()) return;
    const rejectionAnswers = Object.fromEntries(Object.entries(values).map(([key, value]) => [key, value.trim()]));
    const payload = JSON.stringify(rejectionAnswers);
    if (command.current.payload && command.current.payload !== payload) command.current.requestId = crypto.randomUUID();
    command.current.payload = payload;
    pending.current = true; setSaving(true); setError('');
    try {
      if (await journey.act('reject', person.id, { rejectionAnswers, requestId: command.current.requestId, expectedRevision: command.current.revision })) onSaved();
      else setError('Не удалось сохранить отказ. Ответы остались в форме.');
    } catch { setError('Не удалось сохранить отказ. Ответы остались в форме.'); }
    finally { pending.current = false; setSaving(false); }
  }}>
    <h3>Отказать кандидату</h3>
    {(['reason', 'missing', 'improve'] as const).map((key, index) => <label key={key} htmlFor={`${id}-${key}`}>
      {employerRejectionQuestions[index]}<Textarea id={`${id}-${key}`} autoFocus={index === 0} required={index === 0}
        maxLength={3000} disabled={saving || journey.busy} value={values[key]} onChange={event => setValues({ ...values, [key]: event.target.value })}/>
    </label>)}
    {(journey.error || error) && <p className="esc-error" role="alert">{journey.error || error}</p>}
    <div className="esc-form-actions"><Button disabled={saving || journey.busy || !values.reason.trim()} type="submit">{saving ? 'Сохраняем…' : 'Подтвердить отказ'}</Button>
      <Button variant="ghost" type="button" disabled={saving || journey.busy} onClick={onCancel}>Отмена</Button></div>
  </form>;
}
