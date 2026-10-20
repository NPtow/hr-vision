import { useState, type FormEvent, type ReactNode } from 'react';
import { ChevronRight, FileText } from 'lucide-react';
import { Button } from '../../components/ui/button';
import type { CandidateProfile } from '../candidate-portal/api';
import type { RecruiterCandidate } from './api';
import { CandidateAccessControls } from './CandidateAccessControls';
import { RecruiterMediaActions } from './RecruiterMediaActions';
import { InterviewPreparation } from './InterviewPreparation';
import './candidate-details.css';

type CandidateDetailsData = RecruiterCandidate & Partial<Pick<CandidateProfile,
  'salaryMin' | 'salaryMax' | 'latestStartDate' | 'residence' | 'employmentType'>>;

export type RecruiterCandidateDetailsProps = {
  candidate: CandidateDetailsData;
  busy: boolean;
  run: (path: string, body: unknown) => Promise<boolean>;
  onNotice: (value: string) => void;
  token: string;
  onUpdated: () => void | Promise<void>;
  readOnly?: boolean;
};

const workModes = { office: 'В офисе', hybrid: 'Гибрид', remote: 'Удалённо', any: 'Любой формат' };
const employmentTypes = { 'full-time': 'Полная занятость', 'part-time': 'Частичная занятость', project: 'Проектная работа', internship: 'Стажировка' };
const number = new Intl.NumberFormat('ru-RU');

function sourceLabel(candidate: RecruiterCandidate) {
  if (candidate.source?.label) return candidate.source.label;
  const kind = candidate.source?.kind || candidate.intake?.source;
  return kind === 'hh' ? 'HH' : kind === 'manual' ? 'Добавлен рекрутером' : kind === 'registration' ? 'Регистрация в HR Vision' : '';
}

function salaryLabel(candidate: CandidateDetailsData) {
  const minimum = candidate.salaryMin;
  const maximum = candidate.salaryMax;
  const hasMinimum = typeof minimum === 'number' && Number.isFinite(minimum);
  const hasMaximum = typeof maximum === 'number' && Number.isFinite(maximum);
  if (hasMinimum && hasMaximum) return `${number.format(minimum)}–${number.format(maximum)} ₽`;
  if (hasMinimum) return `от ${number.format(minimum)} ₽`;
  if (hasMaximum) return `до ${number.format(maximum)} ₽`;
  return candidate.desiredSalary?.trim() || '';
}

function dateLabel(value?: string) {
  if (!value) return '';
  const date = new Date(`${value}T12:00:00+03:00`);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('ru-RU', {
    timeZone: 'Europe/Moscow', day: 'numeric', month: 'long', year: 'numeric',
  }).format(date);
}

function fileSize(size: number) {
  if (!Number.isFinite(size) || size <= 0) return '';
  return size < 1024 * 1024 ? `${number.format(Math.max(1, Math.round(size / 1024)))} КБ` : `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(size / (1024 * 1024))} МБ`;
}

function Fact({ label, children }: { label: string; children?: ReactNode }) {
  if (!children) return null;
  return <div className="rcd-fact"><dt>{label}</dt><dd>{children}</dd></div>;
}

/** Interview details share one reading order on desktop and narrow screens. */
export function RecruiterCandidateDetails({ candidate, busy, run, onNotice, token, onUpdated, readOnly = false }: RecruiterCandidateDetailsProps) {
  const [summary, setSummary] = useState(candidate.internalProfile?.summary || '');
  const [evaluation, setEvaluation] = useState(candidate.internalProfile?.evaluation || '');
  const source = sourceLabel(candidate);
  const applications = (candidate.sourceApplications || []).filter(application => application.title || application.company);
  const city = candidate.city?.trim();
  const residence = candidate.residence?.trim();
  const locations = [...new Set((candidate.geographies || []).map(value => value.trim()).filter(Boolean))]
    .filter(value => value.toLocaleLowerCase('ru-RU') !== city?.toLocaleLowerCase('ru-RU'));
  const email = candidate.email || candidate.intake?.contactEmail;
  const salary = salaryLabel(candidate);
  const latestStart = dateLabel(candidate.latestStartDate);
  const employment = candidate.employmentType ? employmentTypes[candidate.employmentType] : '';
  const hasPreferences = salary || latestStart || employment || candidate.workMode || locations.length > 0;
  const hasContacts = email || candidate.telegram;
  const hasOverview = candidate.desiredRole || city || residence || candidate.about || candidate.resume;
  const hasSide = hasContacts || hasPreferences;
  const hasNotes = Boolean(summary || evaluation);

  async function saveInternal(event: FormEvent) {
    event.preventDefault();
    if (readOnly || busy) return;
    if (await run('profile', { candidateId: candidate.id, summary, evaluation })) onNotice('Заметки сохранены.');
  }

  return <div className="rp-candidate-details">
    {!readOnly && candidate.interview && <InterviewPreparation key={candidate.interview.id} candidate={candidate} token={token}/>}
    {!readOnly && candidate.interview?.status === 'completed' && <section className="rcd-results" aria-label="Результаты интервью">
      <RecruiterMediaActions kind="interview" candidateId={candidate.id} status={candidate.interview.status} token={token} onUpdated={onUpdated}/>
    </section>}

    {!readOnly && candidate.interview && <CandidateAccessControls candidateId={candidate.id} token={token} allowClaimFallback={candidate.accountActive === false && candidate.interview.status === 'completed'} onChanged={() => { void onUpdated(); }}/> }

    {(source || applications.length > 0) && <dl className="rcd-origin">
      <Fact label="Источник">{source}</Fact>
      {applications.length > 0 && <Fact label={applications.length === 1 ? 'Отклик на вакансию' : 'Отклики на вакансии'}>
        <ul>{applications.map(application => <li key={application.id}>{[application.title, application.company].filter(Boolean).join(' · ')}</li>)}</ul>
      </Fact>}
    </dl>}

    {(hasOverview || hasSide) && <div className="rcd-columns" data-single-column={!hasOverview || !hasSide ? true : undefined}>
      {hasOverview && <div className="rcd-overview">
        {(candidate.desiredRole || city || residence || candidate.about) && <section className="rcd-section" aria-label="О кандидате">
          {candidate.desiredRole && <h3 className="rcd-role">{candidate.desiredRole}</h3>}
          {(city || residence) && <dl className="rcd-location">
            <Fact label="Город">{city}</Fact>
            {residence && residence.toLocaleLowerCase('ru-RU') !== city?.toLocaleLowerCase('ru-RU') && <Fact label="Место проживания">{residence}</Fact>}
          </dl>}
          {candidate.about && <p className="rcd-about">{candidate.about}</p>}
        </section>}

        {candidate.resume && <section className="rcd-document" aria-label="Резюме кандидата">
          <span className="rcd-document-icon" aria-hidden="true"><FileText size={22}/></span>
          <div><span className="rcd-label">Резюме</span><p>{candidate.resume.filename}</p>{fileSize(candidate.resume.size) && <span className="rcd-file-size">{fileSize(candidate.resume.size)}</span>}</div>
        </section>}
      </div>}

      {(hasContacts || hasPreferences) && <div className="rcd-side">
        {hasContacts && <section className="rcd-section" aria-label="Контакты">
          <dl className="rcd-facts">
            <Fact label="Почта">{email}</Fact>
            <Fact label="Telegram">{candidate.telegram}</Fact>
          </dl>
        </section>}
        {hasPreferences && <section className="rcd-section" aria-label="Предпочтения кандидата">
          <dl className="rcd-facts">
            <Fact label="Зарплата">{salary}</Fact>
            <Fact label="Формат работы">{candidate.workMode ? workModes[candidate.workMode] : ''}</Fact>
            <Fact label="Занятость">{employment}</Fact>
            <Fact label="Выход на работу до">{latestStart}</Fact>
            <Fact label="Также готов работать">{locations.join(', ')}</Fact>
          </dl>
        </section>}
      </div>}
    </div>}

    {(!readOnly || hasNotes) && <details className="rcd-notes">
      <summary><ChevronRight size={17} aria-hidden="true"/>Заметки рекрутера</summary>
      {readOnly ? <dl className="rcd-note-content"><Fact label="Краткое резюме">{summary}</Fact><Fact label="Оценка и заметки">{evaluation}</Fact></dl> : <form onSubmit={saveInternal}>
        <label>Краткое резюме<textarea value={summary} onChange={event => setSummary(event.target.value)} maxLength={12000} rows={3}/></label>
        <label>Оценка и заметки<textarea value={evaluation} onChange={event => setEvaluation(event.target.value)} maxLength={12000} rows={3}/></label>
        <Button type="submit" variant="secondary" disabled={busy}>Сохранить</Button>
      </form>}
    </details>}
  </div>;
}
