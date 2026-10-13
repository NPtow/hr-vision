"""Read-only adapter from canonical employer data to the existing screen contract.

No demo people, invented availability, matching or provider calls belong here.
Media URLs are fetched separately, only when an authorized screen requests them.
"""
import datetime as dt
import json

from domain import Problem, iso, now, timestamp
import employer_store as store
import employer_workflow as workflow

WORK_MODES = {'office': 'Офис', 'hybrid': 'Гибрид', 'remote': 'Удалённо', 'any': 'Любой'}
EMPLOYMENT_TYPES = {'full': 'Полная', 'part': 'Частичная', 'project': 'Проектная',
                    'full_time': 'Полная', 'part_time': 'Частичная'}


def _salary(profile):
    low, high = profile.get('salaryMin'), profile.get('salaryMax')
    if low and high:
        return f'{low:,}–{high:,} ₽'.replace(',', ' ')
    return f'от {low:,} ₽'.replace(',', ' ') if low else profile.get('desiredSalary', '')


def _profile(db, candidate_id, profile, portal):
    row = db.execute('SELECT body FROM cp_internal_profiles WHERE candidate_id=?', (candidate_id,)).fetchone()
    internal = json.loads(row[0]) if row else {}
    resume = db.execute('''SELECT text_content,filename,mime,length(content),uploaded_at
      FROM cp_resumes WHERE candidate_id=?''', (candidate_id,)).fetchone()
    account = db.execute('SELECT email,profile FROM cp_candidates WHERE id=?', (candidate_id,)).fetchone()
    saved_profile = json.loads(account[1]) if account else {}
    contact_values = {'email': account[0] if account and account[0] else saved_profile.get('email'),
                      'phone': saved_profile.get('phone'), 'telegram': saved_profile.get('telegram')}
    interview = portal.interview(db, candidate_id)
    fields = [
        ('Город', profile.get('city')), ('Место проживания', profile.get('residence')),
        ('Занятость', EMPLOYMENT_TYPES.get(profile.get('employmentType'), profile.get('employmentType'))),
        ('Формат работы', WORK_MODES.get(profile.get('workMode'), profile.get('workMode'))),
        ('Желаемая зарплата', _salary(profile)), ('Срок выхода', profile.get('latestStartDate')),
        ('О себе', profile.get('about')),
    ]
    name = profile.get('name', '')
    return {
        'id': candidate_id, 'name': name,
        'initials': ''.join(part[0] for part in name.split()[:2]),
        'role': ' · '.join(str(value) for value in (profile.get('city'), _salary(profile)) if value),
        'interviewRole': profile.get('desiredRole', ''),
        'interviewId': interview['id'] if interview else None,
        'interviewDate': interview['start'] if interview else '',
        'score': None, 'duration': None, 'photo': None, 'video': None,
        'shortChapters': [], 'fullChapters': [],
        'shortSummary': internal.get('summary', ''),
        'profile': [{'label': label, 'value': str(value)} for label, value in fields if value],
        'cv': resume[0] if resume else '',
        'resume': {'filename': resume[1], 'mime': resume[2], 'size': resume[3], 'uploadedAt': resume[4]} if resume else None,
        'contacts': [{'kind': kind, 'value': value.strip()} for kind, value in contact_values.items()
                     if isinstance(value, str) and value.strip()],
        'conclusion': [internal['evaluation']] if internal.get('evaluation') else [],
        'questions': [],
    }


def _busy(db, candidate_id, workspace_id):
    meetings = db.execute('''SELECT m.start,m.end FROM cp_meetings m
      JOIN cp_applications a ON a.id=m.application_id
      WHERE m.status IN ('pending','confirmed','live')
      AND (a.archived_at IS NULL OR m.status='live')
      AND (a.candidate_id=? OR m.manager_id=?)''',
                          (candidate_id, 'workspace:' + workspace_id)).fetchall()
    interviews = db.execute('''SELECT s.start,s.end FROM cp_interviews i
      JOIN cp_recruiter_slots s ON s.id=i.slot_id WHERE i.candidate_id=?
      AND i.status IN ('booked','live')''', (candidate_id,)).fetchall()
    return [(timestamp(start), timestamp(end)) for start, end in meetings + interviews]


def _free_slots(db, candidate_id, workspace_id, profile):
    occupied = _busy(db, candidate_id, workspace_id)
    tick, duration = now(), dt.timedelta(minutes=30)
    slots = set()
    for interval in profile.get('availability', []):
        try:
            start, end = timestamp(interval['start']), timestamp(interval['end'])
        except (Problem, KeyError, TypeError):
            continue
        # Availability can be old or broad. Never loop over an unbounded interval.
        if start <= tick:
            start += duration * (int((tick - start) / duration) + 1)
        end = min(end, tick + dt.timedelta(days=180))
        while start + duration <= end and len(slots) < 240:
            if not any(start < stop and start + duration > begin for begin, stop in occupied):
                slots.add(iso(start))
            start += duration
        if len(slots) >= 240:
            break
    return sorted(slots)


def _safe_meeting(db, principal, portal, aid, meeting_id, saved):
    meeting = portal.meeting(db, aid, meeting_id)
    if meeting:
        meeting.pop('room', None)
        # A declined proposal is an ended appointment in the existing component.
        if meeting['status'] == 'declined':
            meeting['status'] = 'cancelled'
        meeting.update(testSkip=False, joined=[], endedAt=meeting.get('completedAt'))
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='em_meeting_transcripts'").fetchone():
            joined = db.execute('''SELECT joined_at FROM em_meeting_transcripts
              WHERE meeting_id=? AND application_id=? AND workspace_id=?''',
                                (meeting['id'], aid, principal['workspaceId'])).fetchone()
            if joined and joined[0]:
                meeting.update(joined=['manager'], startedAt=joined[0])
        for key in ('questionnaire', 'feedbackDraft'):
            if key in saved:
                meeting[key] = saved[key]
    return meeting


def person(db, principal, portal, record, vacancy):
    aid, cid = record['applicationId'], record['candidateId']
    profile = record['profile']
    state = workflow.load(db, aid, actor='manager', vacancy_id=vacancy['id'])
    stages = state['stages']
    for stage in stages:
        if stage['kind'] == 'meeting':
            stage['meeting'] = _safe_meeting(db, principal, portal, aid, stage['meetingId'], stage['meeting'])
    meetings = [stage['meeting'] for stage in stages if stage['kind'] == 'meeting']
    latest = state.get('meeting')
    meeting = next((item for item in meetings if latest and item['id'] == latest['id']), None)
    messages = [{**message, 'actor': 'manager' if message['actor'] == 'manager' else aid}
                for message in state['messages']]
    return {
        'id': aid, 'applicationId': aid, 'candidateId': cid, 'vacancyId': vacancy['id'],
        'vacancyRole': vacancy.get('role', vacancy.get('title', '')),
        'name': profile.get('name', ''), 'role': profile.get('desiredRole', ''),
        'summary': profile.get('about', ''), 'photo': None,
        'sourceProfile': _profile(db, cid, profile, portal),
        'reviewState': record['reviewState'], 'queueOrder': record['queueOrder'],
        'matchingReason': record['reason'], 'matchingSource': record['source'],
        'interest': record['status'], 'auto': bool(profile.get('autoApprove')),
        'slots': [], 'busy': [],
        'freeSlots': _free_slots(db, cid, principal['workspaceId'], profile),
        'meeting': meeting, 'feedback': state['feedback'],
        'screeningComments': state['screeningComments'], 'reviewRejection': state['reviewRejection'],
        'meetings': meetings, 'stages': stages, 'offers': state['offers'],
        'afterInterest': state['afterInterest'], 'decision': state['decision'],
        'offer': state['offer'], 'messages': messages, 'workflowRevision': state['revision'],
    }


def build_state(db, principal, portal, media, vacancy_id=None):
    wid = principal['workspaceId']
    if principal.get('team'):
        store.adopt_existing(db, wid)
    vacancies = store.list_vacancies(db, wid)
    if vacancy_id:
        selected = store.vacancy(db, wid, vacancy_id)
    else:
        selected = vacancies[0] if vacancies else None
    by_id = {vacancy['id']: vacancy for vacancy in vacancies}
    records = store.applications(db, wid)
    counts = {vid: {'proposedCount': 0, 'selectedCount': 0} for vid in by_id}
    people = []
    for record in records:
        candidate = person(db, principal, portal, record, by_id[record['vacancyId']])
        if candidate['decision'] == 'declined':
            continue
        counts[record['vacancyId']]['selectedCount' if record['reviewState'] == 'selected' else 'proposedCount'] += 1
        people.append(candidate)
    for vacancy in vacancies:
        vacancy.update(counts[vacancy['id']])
    current = {p['id']: p for p in people if selected and p['vacancyId'] == selected['id']}
    selected = {**selected, **counts[selected['id']]} if selected else {
        'id': '', 'company': '', 'role': '', 'problem': '', 'salary': '', 'format': '',
        'expectations': '', 'proposedCount': 0, 'selectedCount': 0,
    }
    closed_by = selected.get('closedBy')
    if closed_by and closed_by not in current:
        closed_by = next((p['id'] for p in current.values() if p['candidateId'] == closed_by), closed_by)
    tick = now()
    meetings = [p for p in people if p['meeting'] and (
        p['meeting']['status'] == 'live' or p['meeting']['status'] in ('pending', 'confirmed')
        and timestamp(p['meeting']['end']) > tick)]
    meetings.sort(key=lambda p: p['meeting']['start'])
    pending_feedback = [p for p in people if p['meeting'] and p['meeting']['status'] == 'completed'
                        and not p['feedback']]
    return {
        'mode': 'employer', 'scope': 'sfera', 'generation': selected.get('generation', wid),
        'revision': sum(v['revision'] for v in vacancies) + sum(p['workflowRevision'] +
                      (p['meeting'].get('revision', 0) if p['meeting'] else 0) for p in people),
        'actor': 'manager', 'actorName': principal['name'], 'workspaceId': wid,
        'workspaceName': db.execute('SELECT name FROM em_workspaces WHERE id=?', (wid,)).fetchone()[0],
        'closedBy': closed_by, 'videoConfigured': media.configured,
        'vacancy': selected, 'vacancies': vacancies, 'candidates': current,
        'meetings': meetings, 'offerCandidates': [p for p in people if p['offer'] or p['decision'] == 'pool'],
        'pendingFeedback': pending_feedback,
    }
