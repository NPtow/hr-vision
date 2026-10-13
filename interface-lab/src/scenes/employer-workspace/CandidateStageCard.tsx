import { useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { DropdownMenu, Tabs } from 'radix-ui';
import { Button } from '../../components/ui/button';
import { Textarea } from '../../components/ui/textarea';
import { Avatar } from './PeoplePanel';
import { answerLabels } from '../connected-journey/FeedbackGate';
import type { CandidateStage, CandidateStageDocument, CandidateStageKind, CandidateStageNotes, CandidateStagePerson, CandidateTestStatus } from './candidate-stage-types';
import interviewLayer from './assets/candidate-stages/interview.svg';
import interviewMuted from './assets/candidate-stages/interview-muted.svg';
import meetingLayer from './assets/candidate-stages/meeting.svg';
import meetingMuted from './assets/candidate-stages/meeting-muted.svg';
import testLayer from './assets/candidate-stages/test.svg';
import testMuted from './assets/candidate-stages/test-muted.svg';
import offerLayer from './assets/candidate-stages/offer.svg';
import offerMuted from './assets/candidate-stages/offer-muted.svg';
import documentIcon from './assets/candidate-stages/file.svg';
import './candidate-stage-card.css';

const stageLayers: Record<CandidateStageKind, [string, string]> = {
  interview: [interviewLayer, interviewMuted], meeting: [meetingLayer, meetingMuted],
  test: [testLayer, testMuted], offer: [offerLayer, offerMuted],
};
const statusLabels: Record<string, string> = {
  draft: 'Черновик', sent: 'Отправлен', accepted: 'Принят', declined: 'Отклонён', superseded: 'Предыдущая версия', withdrawn: 'Отозван',
  pending: 'Ждём подтверждения', confirmed: 'Подтверждена', live: 'Идёт встреча', completed: 'Завершено',
  cancelled: 'Отменено', assigned: 'Задание подготовлено', in_progress: 'В работе', ready: 'Готово', processing: 'Обрабатывается',
};
const dateLabel = (value?: string | null) => {
  if (!value) return '';
  const date = new Date(value.length === 10 ? `${value}T12:00:00Z` : value);
  return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', timeZone: 'Europe/Moscow' }).format(date);
};
const documentUrl = (value: string) => {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined; } catch { return undefined; }
};

export type CandidateStageCardProps = {
  person: CandidateStagePerson; stages: CandidateStage[]; workflowRevision: number;
  selectedStageId?: string; onSelectStage: (id: string) => void;
  onAddStage?: (kind: Exclude<CandidateStageKind, 'interview'>) => void;
  renderInterview: (stage: CandidateStage) => ReactNode;
  renderMeeting?: (stage: CandidateStage) => ReactNode;
  renderOffer?: (stage: CandidateStage) => ReactNode;
  editor?: ReactNode; headerActions?: ReactNode; busy?: boolean; error?: string;
  onEditStage?: (stage: CandidateStage) => void;
  onSaveStageNotes?: (stageId: string, values: CandidateStageNotes) => Promise<boolean>;
  onOpenDocument?: (stage: CandidateStage, document: CandidateStageDocument) => void;
};

/** A stage selection is a view change only. Mutations stay in explicit parent callbacks. */
export function CandidateStageCard(props: CandidateStageCardProps) {
  const { person, stages, selectedStageId, onSelectStage, onAddStage, editor, headerActions, busy, error } = props;
  const selected = stages.find(stage => stage.id === selectedStageId) || stages.at(-1);
  const counts = { interview: 0, meeting: 0, test: 0, offer: 0 };
  const labels = stages.map(stage => {
    const number = ++counts[stage.kind];
    if (stage.kind === 'interview') return 'Интервью';
    if (stage.kind === 'meeting') return `Встреча ${number}`;
    if (stage.kind === 'test') return `Тестовое${number > 1 ? ` ${number}` : ''}`;
    return `Оффер${number > 1 ? ` ${number}` : ''}`;
  });
  const headingId = useId();
  const stageScroll = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const scroller = stageScroll.current;
    if (!scroller) return;
    const revealSelected = () => {
      const tab = scroller.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]');
      if (!tab) return;
      const viewport = scroller.getBoundingClientRect();
      const active = tab.getBoundingClientRect();
      const left = viewport.left + scroller.clientLeft + 4;
      const right = viewport.left + scroller.clientLeft + scroller.clientWidth - 4;
      const delta = active.left < left ? active.left - left : active.right > right ? active.right - right : 0;
      // Move only the strip; no page jump, focus change, or motion to wait for.
      if (delta) scroller.scrollTo({ left: scroller.scrollLeft + delta, behavior: 'instant' });
    };
    revealSelected();
    const observer = new ResizeObserver(revealSelected);
    observer.observe(scroller);
    return () => observer.disconnect();
  }, [selected?.id, stages.length]);
  return <Tabs.Root value={selected?.id || ''} onValueChange={onSelectStage} activationMode="manual" asChild>
    <article className="esc-card" aria-labelledby={headingId} data-candidate-id={person.id}>
      <header className="esc-person">
        <Avatar key={person.id} name={person.name} photo={person.photo}/>
        <div className="esc-person-copy"><h2 id={headingId}>{person.name}</h2>{(person.meta || person.role) && <p>{person.meta || person.role}</p>}{person.preferences && <p className="esc-person-preferences">{person.preferences}</p>}</div>
        {headerActions && <div className="esc-person-actions">{headerActions}</div>}
      </header>
      <div className="esc-stages">
        <div className="esc-stage-scroll" ref={stageScroll}><Tabs.List className="esc-stage-list" aria-label="Этапы кандидата">
          {stages.map((stage, index) => <Tabs.Trigger className="esc-stage-tab" key={stage.id} value={stage.id} disabled={busy} title={stage.title}>
            <span className="esc-stage-layer" aria-hidden="true"><img src={stageLayers[stage.kind][selected?.id === stage.id ? 0 : 1]} alt=""/></span>
            <span className="esc-stage-name">{labels[index]}</span>
            <span className="esc-stage-date">{stage.status === 'draft' ? 'Черновик' : dateLabel(stage.date) || statusLabels[stage.status] || ''}</span>
          </Tabs.Trigger>)}
        </Tabs.List></div>
        {onAddStage && <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild><button className="esc-add-stage" type="button" aria-label="Добавить следующий этап" disabled={busy}><span aria-hidden="true">+</span></button></DropdownMenu.Trigger>
          <DropdownMenu.Portal><DropdownMenu.Content className="esc-menu" sideOffset={12} align="end" collisionPadding={16}>
            <DropdownMenu.Label className="esc-menu-label">Что дальше?</DropdownMenu.Label>
            <DropdownMenu.Item className="esc-menu-item" onSelect={() => onAddStage('meeting')}>Встреча</DropdownMenu.Item>
            <DropdownMenu.Item className="esc-menu-item" onSelect={() => onAddStage('test')}>Тестовое</DropdownMenu.Item>
            <DropdownMenu.Item className="esc-menu-item" onSelect={() => onAddStage('offer')}>Оффер</DropdownMenu.Item>
          </DropdownMenu.Content></DropdownMenu.Portal>
        </DropdownMenu.Root>}
      </div>
      {error && <p className="esc-error" role="alert">{error}</p>}
      {editor ? <div className="esc-stage-editor">{editor}</div> : selected ? <Tabs.Content className="esc-stage-panel" key={selected.id} value={selected.id}>
        <StageMaterials {...props} stage={selected}/>
      </Tabs.Content> : <p className="esc-empty">Материалы кандидата появятся здесь.</p>}
    </article>
  </Tabs.Root>;
}

function StageMaterials(props: CandidateStageCardProps & { stage: CandidateStage }) {
  const { stage, person, renderInterview, renderMeeting, renderOffer, onEditStage, onOpenDocument, busy } = props;
  if (stage.kind === 'interview') return <div className="esc-interview" data-stage-id={stage.id}>
    {stage.date && <p className="esc-caption">Первичное интервью · {dateLabel(stage.date)}</p>}
    {renderInterview(stage)}
  </div>;
  if (stage.kind === 'offer') {
    const offer = stage.offer;
    return <section className="esc-materials" data-stage-id={stage.id}>
      <div className="esc-materials-heading"><h3>Предложение для кандидата</h3><p>{statusLabels[stage.status] || stage.status}</p></div>
      {offer ? <><p>{offer.role}</p><dl className="esc-facts">
        {offer.compensation && <div><dt>Зарплата в месяц</dt><dd>{offer.compensation}</dd></div>}
        {offer.startDate && <div><dt>Дата выхода</dt><dd>{dateLabel(offer.startDate) || offer.startDate}</dd></div>}
        {offer.format && <div><dt>Формат работы</dt><dd>{offer.format}</dd></div>}
      </dl>{offer.expectations && <div className="esc-text-block"><h4>Ожидания</h4><p>{offer.expectations}</p></div>}</> : <p className="esc-empty">Условия предложения ещё не сохранены.</p>}
      <StageDocuments stage={stage} onOpen={onOpenDocument}/>
      {renderOffer?.(stage)}
      {onEditStage && ['draft', 'sent'].includes(stage.status) && <div className="esc-stage-actions"><Button className="esc-secondary" variant="secondary" disabled={busy} onClick={() => onEditStage(stage)}>Изменить оффер</Button></div>}
    </section>;
  }
  const test = stage.test;
  return <section className="esc-materials" data-stage-id={stage.id}>
    <div className="esc-materials-heading"><h3>{stage.kind === 'test' ? test?.title || stage.title : stage.title}</h3><p>{[statusLabels[stage.status] || stage.status, dateLabel(stage.date)].filter(Boolean).join(' · ')}</p></div>
    {stage.kind === 'meeting' && <dl className="esc-facts"><div><dt>Кандидат</dt><dd>{person.name}</dd></div>{stage.meeting?.start && <div><dt>Время встречи · МСК</dt><dd>{new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' }).format(new Date(stage.meeting.start))}</dd></div>}</dl>}
    {test?.instructions && <p>{test.instructions}</p>}
    {test?.dueAt && <p className="esc-caption">Срок: {new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' }).format(new Date(test.dueAt))} МСК</p>}
    <StageDocuments stage={stage} onOpen={onOpenDocument}/>
    <StageNotes key={stage.id} stage={stage} workflowRevision={props.workflowRevision} busy={busy} onSave={props.onSaveStageNotes}/>
    {stage.kind === 'meeting' && <MeetingFeedback stage={stage}/>}
    {stage.kind === 'meeting' && renderMeeting?.(stage)}
    {stage.kind === 'test' && onEditStage && <div className="esc-stage-actions"><Button className="esc-secondary" variant="ghost" disabled={busy} onClick={() => onEditStage(stage)}>Изменить задание</Button></div>}
  </section>;
}

function MeetingFeedback({ stage }: { stage: CandidateStage }) {
  const feedback = stage.feedback;
  if (!feedback) return null;
  const notes = [
    { label: 'Что удалось подтвердить', text: feedback.example },
    { label: 'Сомнения', text: feedback.doubts },
    { label: 'Что проверить дальше', text: feedback.check },
  ].filter(item => item.text?.trim());
  const answers = (feedback.questions || []).flatMap(question => {
    const answer = feedback.answers?.[question.id];
    return answer ? [{ question, answer }] : [];
  });
  if (!notes.length && !answers.length) return null;
  return <div className="esc-text-block"><h4>Итог встречи</h4>
    {notes.map(item => <div className="esc-feedback-answer" key={item.label}><strong>{item.label}</strong><p>{item.text}</p></div>)}
    {answers.map(({ question, answer }) => <div className="esc-feedback-answer" key={question.id}><strong>{question.question}</strong><span>{answerLabels[answer.rating]}</span>{answer.comment && <p>{answer.comment}</p>}</div>)}
  </div>;
}

function StageDocuments({ stage, onOpen }: { stage: CandidateStage; onOpen?: CandidateStageCardProps['onOpenDocument'] }) {
  if (!stage.documents.length) return null;
  return <div className="esc-documents">{stage.documents.map(document => {
    const contents = <><img src={documentIcon} alt=""/><span><strong>{document.title}</strong></span></>;
    const url = documentUrl(document.url);
    return onOpen ? <button className="esc-document" type="button" key={document.id} onClick={() => onOpen(stage, document)}>{contents}</button>
      : url ? <a className="esc-document" key={document.id} href={url} target="_blank" rel="noreferrer">{contents}</a>
        : <div className="esc-document" key={document.id}>{contents}</div>;
  })}</div>;
}

function StageNotes({ stage, workflowRevision, busy, onSave }: { stage: CandidateStage; workflowRevision: number; busy?: boolean; onSave?: CandidateStageCardProps['onSaveStageNotes'] }) {
  const [editing, setEditing] = useState(false);
  const [values, setValues] = useState<CandidateStageNotes>({ notes: stage.notes, expectedRevision: workflowRevision });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const trigger = useRef<HTMLButtonElement>(null);
  const pending = useRef(false);
  const fieldId = useId();
  const finish = () => { setEditing(false); requestAnimationFrame(() => trigger.current?.focus()); };
  if (editing) return <form className="esc-form" onSubmit={async event => {
    event.preventDefault(); if (!onSave || pending.current || busy) return;
    pending.current = true; setSaving(true); setError('');
    try { if (await onSave(stage.id, values)) finish(); else setError('Не удалось сохранить. Ваши изменения остались в форме.'); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось сохранить изменения.'); }
    finally { pending.current = false; setSaving(false); }
  }}>
    <h3>{stage.kind === 'test' ? 'Результат тестового' : 'Заметка по встрече'}</h3>
    {stage.kind === 'test' && <><label htmlFor={`${fieldId}-status`}>Статус<select id={`${fieldId}-status`} value={values.status} disabled={busy || saving} onChange={event => setValues({ ...values, status: event.target.value as CandidateTestStatus })}>
      <option value="assigned">Задание подготовлено</option><option value="in_progress">В работе</option><option value="completed">Выполнено</option><option value="cancelled">Отменено</option>
    </select></label><label htmlFor={`${fieldId}-result`}>Результат<Textarea id={`${fieldId}-result`} maxLength={12000} value={values.result || ''} disabled={busy || saving} onChange={event => setValues({ ...values, result: event.target.value })}/></label></>}
    <label htmlFor={`${fieldId}-notes`}>Моя заметка<Textarea id={`${fieldId}-notes`} maxLength={12000} value={values.notes} disabled={busy || saving} onChange={event => setValues({ ...values, notes: event.target.value })}/></label>
    {error && <p className="esc-error" role="alert">{error}</p>}
    <div className="esc-form-actions"><Button type="submit" disabled={busy || saving}>{saving ? 'Сохраняем…' : 'Сохранить'}</Button><Button type="button" variant="ghost" disabled={saving} onClick={finish}>Отмена</Button></div>
  </form>;
  return <>
    {stage.test?.result && <div className="esc-text-block"><h4>Результат</h4><p>{stage.test.result}</p></div>}
    {stage.notes && <div className="esc-text-block"><h4>Моя заметка</h4><p>{stage.notes}</p></div>}
    {onSave && <div className="esc-notes-controls"><Button ref={trigger} className="esc-secondary" variant="ghost" disabled={busy} onClick={() => {
      setValues({ notes: stage.notes, expectedRevision: workflowRevision, ...(stage.test ? { result: stage.test.result, status: stage.test.status } : {}) });
      setError(''); setEditing(true);
    }}>{stage.kind === 'test' ? stage.test?.result ? 'Изменить результат' : 'Добавить результат' : stage.notes ? 'Изменить заметку' : 'Добавить заметку'}</Button></div>}
  </>;
}
