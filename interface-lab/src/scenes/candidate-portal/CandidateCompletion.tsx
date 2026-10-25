import { useState } from 'react';
import { Button } from '../../components/ui/button';
import type { CandidateState } from './api';
import { candidateCompletionStep } from './candidate-navigation';

export function CandidateCompletion({ candidate, busy, onOpen, onConsent }: {
  candidate: CandidateState; busy: boolean;
  onOpen: (page: 'resume' | 'preferences' | 'profile') => void;
  onConsent: () => Promise<unknown>;
}) {
  const [consent, setConsent] = useState(false);
  const step = candidateCompletionStep(candidate);
  if (!step) return null;
  return <section className="cp-form-page cp-completion" aria-label="После интервью">
    <div className="cp-page-heading"><h1>{step === 'consent' ? 'Продолжим поиск?' : 'Спасибо за интервью'}</h1>
      <p>{step === 'resume' ? 'Добавьте резюме к вашему интервью — запись уже связана с этим профилем.'
        : step === 'preferences' ? 'Проверьте данные из резюме и условия поиска. Ещё одно интервью проходить не нужно.'
          : ''}</p></div>
    {step === 'consent' ? <form className="cp-preferences" onSubmit={event => { event.preventDefault(); if (consent && !busy) void onConsent(); }}>
      <label className="cp-check"><input type="checkbox" checked={consent} disabled={busy} onChange={event => setConsent(event.target.checked)}/><span>Согласен на использование резюме и интервью для подбора вакансий.</span></label>
      <div className="cp-form-actions"><Button disabled={busy || !consent}>{busy ? 'Сохраняем…' : 'Продолжить поиск'}</Button></div>
    </form> : <Button disabled={busy} onClick={() => onOpen(step)}>{step === 'resume' ? 'Добавить резюме' : 'Проверить профиль'}</Button>}
  </section>;
}
