import fixture from './fixtures/selection-matching.json' with { type: 'json' };
const copy = value => structuredClone(value);
export function makeFixture() {
  const names = ['Анна Романова', 'Борис Соколов', 'Виктор Петров', 'Дарья Орлова', 'Елена Иванова', 'Фёдор Смирнов', 'Только запись', 'Только текст', 'Встреча с работодателем', 'Закрытая комната', 'Не все части', 'Статус неизвестен'];
  const people = names.map((name, i) => ({
    id: 'candidate-' + (i + 1), name, interviewStatus: 'completed', interviewId: 'interview-' + i,
    telegramConnected: i < 3, emailConnected: i !== 4, accountActive: true, email: `person${i}@example.test`,
    desiredRole: 'Менеджер по продажам', city: 'Санкт-Петербург', about: '', revision: 'person-' + i, sourceRevision: 'corpus-' + i,
    searching: true, consentReuse: false, resumeAvailable: true, inCohort: true, canAdd: true,
    assessment: null, application: null, nomination: null, deliveries: [],
    hrInterview: { state: 'ready', recordingCount: 1, transcriptCount: 1, allPartsReady: true },
    workflow: { interview: { state: 'closed' }, resume: { state: 'available' }, assessment: { state: i < 5 ? 'ready' : 'not_started' }, match: { state: 'not_compared' } },
  }));
  people[6].hrInterview = { state: 'processing', recordingCount: 1, transcriptCount: 0, allPartsReady: false };
  people[7].hrInterview = { state: 'incomplete', recordingCount: 0, transcriptCount: 1, allPartsReady: false };
  people[8].hrInterview = { state: 'missing', recordingCount: 0, transcriptCount: 0, allPartsReady: false };
  people[9].hrInterview = undefined;
  people[10].hrInterview = { state: 'incomplete', recordingCount: 2, transcriptCount: 1, allPartsReady: false };
  people[11].hrInterview = { state: 'unavailable', recordingCount: null, transcriptCount: null, allPartsReady: false };
  const result = copy(fixture.result), profile = copy(fixture.request.vacancy_profile);
  const vacancy = { id: profile.vacancy_id, title: 'Менеджер по продажам', company: 'Компания · демо', city: 'Санкт-Петербург', salary: '100 000–200 000 ₽', description: '', revision: 1, contentHash: profile.source_revision, workspaceId: 'demo', generation: 'demo' };
  const fields = { location: { required: true, allowed_ids: ['moscow'] }, employment: { required: true, allowed_ids: ['full_time'] }, industry_experience: { required: false, industry_id: null, minimum_months: null } };
  const options = { locations: [{ id: 'moscow', label: 'Москва' }, { id: 'spb', label: 'Санкт-Петербург' }], employmentTypes: [{ id: 'full_time', label: 'Полная занятость' }], industries: [{ id: 'manufacturing', label: 'Производство' }, { id: 'logistics', label: 'Логистика' }] };
  const required = { vacancyId: vacancy.id, revision: 'fields-1', fields, options };
  const profileEnvelope = { vacancyId: vacancy.id, revision: vacancy.contentHash, state: 'ready', stale: false, staleReasons: [], canRun: true, runId: profile.run_id, profile, requiredFields: copy(required) };
  const recommendations = { vacancyId: vacancy.id, revision: vacancy.contentHash, state: 'ready', stale: false, staleReasons: [], canRun: true, recommendationId: result.recommendation_id, result, candidateIds: people.slice(0, 6).map(p => p.id), pendingCandidates: [{ candidateId: 'candidate-6', state: 'not_started' }], invitationEligibility: people.slice(0, 6).map(p => ({ candidateId: p.id, canInvite: ['candidate-1', 'candidate-2'].includes(p.id), reason: p.id === 'candidate-2' ? 'unknown_requires_approval' : p.id === 'candidate-5' ? 'no_delivery_channel' : 'ready', channels: [...(p.emailConnected ? ['email'] : []), ...(p.telegramConnected ? ['telegram'] : [])] })) };
  const candidateFacts = Object.fromEntries(people.map(p => [p.id, { candidateId: p.id, revision: p.id + '-fields', fields: { work_locations: null, employment_types: null, industry_months: null }, options }]));
  const assessment = Object.fromEntries(people.map(p => {
    const raw = copy(fixture.request.candidate_profiles.find(c => c.candidate_id === p.id));
    return [p.id, raw ? { state: 'ready', result: null, canRun: true, coreRun: { run_id: raw.run_id, status: 'completed', error: null, corpus_revision: raw.corpus_sha256, rubric_version: raw.rubric_version, model: raw.model, assessment: raw } } : { state: 'not_started', result: null, canRun: false }];
  }));
  const state = { people, vacancy, profileEnvelope, recommendations, required, candidateFacts, assessment, writes: [], responses: new Map(), failPublishOnce: false, pendingVerificationMode: false, delay: 0 };
  state.request = (method, target, body) => {
    const url = new URL(target, 'http://127.0.0.1'), pathname = url.pathname;
    if (state.responses.has(pathname)) return state.responses.get(pathname);
    const prefix = '/api/hr/selection/', action = pathname.slice(prefix.length);
    if (pathname === '/api/hr/access') return { ready: true };
    if (pathname === '/api/hr/session') return { token: 'local-synthetic-only' };
    if (method === 'POST') {
      state.writes.push({ action, body: copy(body) });
      if (action === 'publish') {
        if (state.failPublishOnce) { state.failPublishOnce = false; return { httpStatus: 503, error: 'Ответ не подтверждён. Обновите состояние перед повтором.' }; }
        if (state.pendingVerificationMode) {
          for (const id of body.candidateIds) {
            people.find(p => p.id === id).nomination = { id: 'nom-' + id, state: 'pending_verification', error: null, attempts: 0, retryAt: 0 };
            recommendations.invitationEligibility.find(p => p.candidateId === id).canInvite = false;
          }
        }
        return { added: body.candidateIds, waitingProfile: state.pendingVerificationMode ? [] : body.candidateIds, proposed: [], pendingVerification: state.pendingVerificationMode ? body.candidateIds : [] };
      }
      if (action.startsWith('nominations/') && action.endsWith('/retry')) {
        const p = people.find(p => p.nomination?.id === action.split('/')[1]);
        p.nomination = { ...p.nomination, state: 'pending_verification', error: null, attempts: 0, retryAt: 0 };
        return { nominationId: p.nomination.id, state: 'pending_verification' };
      }
      if (action.endsWith('/profile/revisions')) {
        for (const change of body.changes) Object.assign(profileEnvelope.profile.parameters.find(p => p.parameter_id === change.parameter_id), change);
        profileEnvelope.profile.profile_id = 'manual-profile-2'; recommendations.stale = true; recommendations.state = 'stale';
        return profileEnvelope;
      }
      if (action.endsWith('/required-fields')) {
        const target = action.startsWith('vacancies/') ? required : candidateFacts[action.split('/')[1]];
        target.fields = body.fields; target.revision += '-next'; recommendations.stale = true; return target;
      }
      if (action.endsWith('/recommendations')) return recommendations;
      if (action.endsWith('/profile')) return profileEnvelope;
      if (action.endsWith('/assessment')) return { candidateId: action.split('/')[1], assessment: assessment[action.split('/')[1]] };
      if (action === 'candidates/update') { Object.assign(people.find(p => p.id === body.candidateId), body.fields); return {}; }
      return { httpStatus: 404, error: 'Unknown fixture command' };
    }
    if (action === 'vacancies') return { vacancies: [vacancy] };
    if (action === 'candidates') {
      const offset = Number(url.searchParams.get('offset') ?? 0), limit = Number(url.searchParams.get('limit') ?? 500);
      const candidates = people.slice(offset, offset + limit);
      return { candidates, count: people.length, offset, limit, nextOffset: offset + candidates.length < people.length ? offset + candidates.length : null };
    }
    if (action.endsWith('/profile')) return profileEnvelope;
    if (action.endsWith('/recommendations')) return recommendations;
    if (action.endsWith('/required-fields')) return action.startsWith('vacancies/') ? required : candidateFacts[action.split('/')[1]];
    if (action.endsWith('/interviews')) return { candidateId: action.split('/')[1], interviews: [] };
    if (action.endsWith('/readiness')) {
      const id = action.split('/')[1], p = people.find(p => p.id === id);
      return { candidateId: id, hrInterview: p.hrInterview, materials: { state: 'ready', recordings: p.hrInterview?.recordingCount ?? null, transcripts: p.hrInterview?.transcriptCount ?? null, items: [] }, assessment: assessment[id] };
    }
    return { httpStatus: 404, error: 'Unknown fixture read: ' + action };
  };
  return state;
}
