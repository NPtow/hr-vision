import { useEffect, useRef, useState } from 'react';
import { CandidateStageWorkspace } from '../employer-workspace/CandidateStageWorkspace';
import { EmployerShell } from '../employer-workspace/EmployerShell';
import type { Journey, Person, SharedState, SourceProfile, Meeting, Offer } from '../connected-journey/api';
import type { CandidateStage } from '../employer-workspace/candidate-stage-types';
import type { CallProvider } from '../../components/call/call-provider';
import { makeTaskStages, taskPersonName, taskVacancyTitle } from './task-state-fixtures';
import '../agency-product/theme.css';
import '../agency-product/product.css';
import '../connected-journey/connected.css';
import '../employer-workspace/employer.css';
import './employer-task-states.css';

export type ProductionScenario = 'completed' | 'scheduled' | 'no-offer' | 'offer' | 'read-only';
const personId = 'qa-task-application';
const qaToken = 'local-only-task-state-fixture';
const clip = '/task-meeting-preview/interview.webm';
const transcript = 'WEBVTT\n\n00:00.000 --> 00:04.000\nРекрутер: Расскажите об опыте.\n\n00:04.000 --> 00:12.000\nКандидат: Это синтетическая запись для проверки интерфейса.\n\n00:12.000 --> 00:20.000\nРекрутер: Какие задачи хотите обсудить на следующей встрече?';
const source: SourceProfile = {
  id: personId, name: taskPersonName, initials: 'А', role: 'Менеджер по продажам', interviewRole: 'Менеджер по продажам',
  interviewDate: '2026-10-13', score: null, duration: 20, photo: null, video: clip, shortChapters: [], fullChapters: [],
  shortSummary: 'Синтетический профиль для проверки интерфейса.', profile: [], cv: 'Тестовое резюме. Все материалы этой сцены синтетические.', conclusion: [], questions: [], interviewId: 'qa-interview',
};
function initialState(scenario: ProductionScenario, provider: CallProvider): SharedState {
  const original = makeTaskStages();
  const chosen = original.filter(stage => stage.kind === 'interview' || (stage.kind === 'meeting' && (scenario === 'scheduled' ? stage.status !== 'completed' : stage.status === 'completed')) || ((scenario === 'offer' || scenario === 'read-only') && stage.kind === 'offer'));
  const stages: CandidateStage[] = chosen.map(stage => {
    if (stage.meeting) {
      const meeting: Meeting = { ...stage.meeting, testSkip: false, callProvider: provider, videoConfigured: true, callProviderOptions: [{ id: 'daily', available: true }, { id: 'zoom', available: true }] };
      return { ...stage, id: `meeting:${meeting.id}`, meetingId: meeting.id, meeting, notes: stage.status === 'completed' ? 'Сохранённая заметка: уточнить дату выхода.' : '',
        documents: stage.status === 'completed' ? [{ id: 'qa-reference', title: 'Материал встречи', url: clip }] : [] };
    }
    if (stage.offer) { const offer: Offer = { ...stage.offer, status: scenario === 'read-only' ? 'accepted' : stage.offer.status }; return { ...stage, id: `offer:${offer.id}`, status: offer.status, offer }; }
    return { ...stage, id: 'interview:qa-interview' };
  });
  const meetings = stages.flatMap(stage => stage.meeting ? [stage.meeting] : []);
  const offers = stages.flatMap(stage => stage.offer ? [stage.offer] : []);
  const person: Person = { id: personId, applicationId: personId, candidateId: 'qa-candidate', vacancyId: 'qa-vacancy', name: taskPersonName,
    interest: 'accepted', auto: false, freeSlots: ['2026-10-27T10:00:00.000Z', '2026-10-27T11:00:00.000Z', '2026-10-28T13:30:00.000Z'], meeting: meetings.at(-1) || null,
    meetings, offers, stages, meetingComments: meetings.filter(meeting => meeting.status === 'completed').map(meeting => ({ id: `qa-comment:${meeting.id}`, meetingId: meeting.id, recordingId: 'qa-recording', seconds: 4, text: 'Сохранённый комментарий к записи.', createdAt: '2026-10-17T10:35:00.000Z' })), workflowRevision: 1, afterInterest: 'yes', decision: 'review', offer: offers.at(-1) || null, messages: [], sourceProfile: source };
  return { mode: 'employer', workspaceId: 'qa-workspace', workspaceName: 'Локальная проверка', scope: 'sfera', generation: 'qa-task-generation', revision: 1, actor: 'manager', actorName: 'Никита', closedBy: scenario === 'read-only' ? personId : null,
    videoConfigured: true, vacancy: { id: 'qa-vacancy', company: 'Тестовая компания', role: taskVacancyTitle, salary: '120 000 ₽ + премия', format: 'Офис', expectations: 'Начать работу с клиентами.', problem: 'Проверка интерфейса' }, candidates: { [personId]: person } };
}
type LoggedRequest = { path: string; body: Record<string, unknown> };
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

/** Runs the actual production components. Every API request is intercepted before mounting them. */
export function ProductionTaskStates({ scenario = 'completed', provider = 'daily' }: { scenario?: ProductionScenario; provider?: CallProvider }) {
  return <ProductionTaskFixture key={`${scenario}:${provider}`} scenario={scenario} provider={provider}/>;
}
function ProductionTaskFixture({ scenario, provider }: { scenario: ProductionScenario; provider: CallProvider }) {
  const [state, setState] = useState(() => initialState(scenario, provider));
  const stateRef = useRef(state); stateRef.current = state;
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const [callActive, setCallActive] = useState(false);
  const [requests, setRequests] = useState<LoggedRequest[]>([]);
  const record = (path: string, body: Record<string, unknown>) => setRequests(items => [...items, { path, body }]);
  const action = async (name: string, candidate?: string, values: Record<string, unknown> = {}) => {
    record(`action:${name}`, values);
    if (candidate && candidate !== personId) { setError('QA: неизвестный кандидат.'); return false; }
    const current = stateRef.current;
    const person = current.candidates[personId]!;
    if (current.closedBy || person.decision === 'declined') { setError('QA: процесс уже закрыт.'); return false; }
    let updated = { ...person, workflowRevision: (person.workflowRevision || 0) + 1 };
    if (name === 'stage.update') updated.stages = person.stages!.map(stage => stage.id === values.stageId ? { ...stage, notes: String(values.notes || '') } : stage);
    else if (name === 'meeting.comment.add') {
      const meetingId = String(values.meetingId || '');
      if (!person.meetings?.some(meeting => meeting.id === meetingId && meeting.status === 'completed')) { setError('QA: завершённая встреча не найдена.'); return false; }
      updated.meetingComments = [...(person.meetingComments || []), { id: `qa-comment-${Date.now()}`, meetingId, recordingId: typeof values.recordingId === 'string' ? values.recordingId : null, seconds: Number(values.seconds) || 0, chapterTitle: String(values.chapterTitle || ''), text: String(values.text || ''), createdAt: new Date().toISOString() }];
    } else if (name === 'offer.save') {
      const offer: Offer = { id: person.offer?.id || `qa-offer-${Date.now()}`, version: (person.offer?.version || 0) + 1, status: 'draft', role: String(values.role || ''), compensation: String(values.compensation || ''), format: String(values.format || ''), expectations: String(values.expectations || ''), startDate: String(values.startDate || ''), createdAt: new Date().toISOString() };
      updated.offer = offer;
      const stage: CandidateStage = { id: `offer:${offer.id}`, kind: 'offer', title: 'Оффер', status: offer.status, date: offer.createdAt, notes: '', documents: [], offer };
      updated.stages = [...person.stages!.filter(item => item.id !== stage.id), stage];
    } else if (name === 'offer.send' && person.offer) {
      updated.offer = { ...person.offer, status: 'sent' }; updated.stages = person.stages!.map(stage => stage.offer?.id === person.offer!.id ? { ...stage, status: 'sent', offer: updated.offer! } : stage);
    } else if (name === 'meeting.propose') {
      if (person.meeting && ['pending', 'confirmed', 'live'].includes(person.meeting.status)) { setError('QA: активная встреча уже есть.'); return false; }
      const meeting: Meeting = { id: `qa-meeting-${Date.now()}`, start: String(values.start), end: new Date(Date.parse(String(values.start)) + 1800000).toISOString(), status: 'pending', testSkip: false, joined: [], revision: 1, videoConfigured: true, callProvider: provider };
      updated.meeting = meeting; updated.meetings = [...(person.meetings || []), meeting];
      updated.stages = [...person.stages!, { id: `meeting:${meeting.id}`, kind: 'meeting', title: 'Встреча', status: 'pending', date: meeting.start, notes: '', documents: [], meetingId: meeting.id, meeting }];
    } else if (name === 'reject') updated.decision = 'declined';
    else if (name !== 'stage.update') { setError(`QA: действие ${name} не подключено.`); return false; }
    const next = { ...current, revision: current.revision + 1, candidates: { ...current.candidates, [personId]: updated } };
    stateRef.current = next; setState(next); setError(''); return true;
  };
  const actionRef = useRef(action); actionRef.current = action;
  useEffect(() => {
    const url = new URL(location.href);
    for (const key of ['stageId', 'meetingId', 'view', 'applicationId', 'pool']) url.searchParams.delete(key);
    const person = stateRef.current.candidates[personId]!;
    const initial = scenario === 'offer' ? person.stages!.at(-1)! : [...person.stages!].reverse().find(stage => stage.kind === 'meeting')!;
    url.searchParams.set('stageId', initial.id); history.replaceState(history.state, '', url);
    const original = window.fetch;
    const mockFetch: typeof fetch = async (input, init) => {
      const target = new URL(input instanceof Request ? input.url : String(input), location.href);
      if (!target.pathname.startsWith('/api/hr/')) return original(input, init);
      const bodyText = init?.body ?? (input instanceof Request ? await input.clone().text() : '');
      let body: Record<string, unknown> = {};
      try { body = JSON.parse(typeof bodyText === 'string' ? bodyText : '{}'); } catch { /* GET */ }
      record(target.pathname, body);
      const path = target.pathname.replace('/api/hr/', '').replace(/^employer\//, '');
      if (path === 'materials') return json({ recordings: [{ id: 'qa-recording', url: clip }], transcripts: [{ id: 'qa-transcript', text: transcript }], video: { status: 'ready' }, transcription: { status: 'ready' }, comments: [] });
      if (path === 'state') return json(stateRef.current);
      if (path === 'room') return json({ error: 'Локальная QA-проверка: согласие принято. Реальный звонок не запускается.' }, 503);
      if (path === 'action') { const ok = await actionRef.current(String(body.action), personId, body); return json(ok ? stateRef.current : { error: 'QA: действие отклонено.' }, ok ? 200 : 409); }
      if (path === 'interview-assistant') return json({ mode: body.action, text: 'Синтетический ответ для проверки интерфейса.', supported: true, parts: [{ text: 'Синтетический ответ для проверки интерфейса.', sourceIds: ['qa-turn'] }], evidence: [{ id: 'qa-turn', quote: 'Это синтетическая запись для проверки интерфейса.', startMs: 4000, endMs: 12000, speakerName: 'Кандидат' }], coverage: { selected: 1, omitted: 0 }, model: 'local-fixture', version: 'qa', matches: [], totalMatches: 0 });
      return json({ error: `Локальная QA-проверка: маршрут ${path} заблокирован.` }, 501);
    };
    window.fetch = mockFetch; setReady(true);
    return () => { if (window.fetch === mockFetch) window.fetch = original; };
  }, [scenario]);
  const journey: Journey = {
    state, token: qaToken, error, setError, busy: false, initializing: false, employer: true, entryWarning: '', clearEntryWarning: () => {},
    act: action, refresh: async () => {}, login: async () => false, authenticate: async () => false, logout: async () => false, createVacancy: async () => false, openVacancy: async () => false,
    setCallProvider: async (_person, nextProvider) => {
      record('call-provider', { provider: nextProvider });
      const current = stateRef.current; const person = current.candidates[personId]!;
      if (!person.meeting) return false;
      const meeting = { ...person.meeting, callProvider: nextProvider };
      const updated = { ...person, meeting, stages: person.stages!.map(stage => stage.meetingId === meeting.id ? { ...stage, meeting } : stage) };
      const next = { ...current, candidates: { ...current.candidates, [personId]: updated } }; stateRef.current = next; setState(next); return true;
    },
  };
  return <div className="ets-preview">
    <div className="ets-labbar"><strong>QA · реальные компоненты</strong><span>Все данные синтетические. API перехвачен локально, звонки и отправки отключены.</span></div>
    {ready && <div className="agency-product agency-product-theme cj-app ew-root ets-product"><EmployerShell page="shortlist" hasOffers={!!state.candidates[personId]!.offer} candidateContext="vacancy" scope="sfera" vacancy={taskVacancyTitle} name="Никита" onNavigate={() => {}} onAccount={() => {}}>
      <CandidateStageWorkspace journey={journey} person={state.candidates[personId]!} source={source} entry={scenario === 'offer' ? 'offer' : 'review'} onChat={() => setError('Чат в QA не отправляет сообщения.')} onCallChange={setCallActive} callActive={callActive}/>
    </EmployerShell></div>}
    <details className="ets-lab-status"><summary>Локальные запросы: {requests.length}</summary><pre data-testid="qa-requests">{JSON.stringify(requests, null, 2)}</pre></details>
  </div>;
}
