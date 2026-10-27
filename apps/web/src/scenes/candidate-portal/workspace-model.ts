import type { CandidateApplication, CandidateMeetingHistory, CandidateOffer } from './api';

export type ApplicationGroup = 'new' | 'process' | 'history';
export type ApplicationHistoryItem = {
  id: string;
  label: string;
  detail?: string;
  at?: string | null;
  tone: 'neutral' | 'blue' | 'green';
  meetingId?: string;
  materials?: boolean;
};

/** The workflow is authoritative even when its latest offer is explicitly null. */
export const applicationOffer = (application: CandidateApplication): CandidateOffer | null =>
  application.workflow ? application.workflow.offer : application.offer || null;

export const applicationRevision = (application: CandidateApplication): number | undefined =>
  application.workflow?.revision ?? application.workflowRevision;

export const applicationAfterInterest = (application: CandidateApplication) =>
  application.workflow?.afterInterest ?? application.afterInterest ?? 'pending';

export const applicationDecision = (application: CandidateApplication) =>
  application.workflow?.decision ?? application.decision ?? 'review';

export function applicationGroup(application: CandidateApplication): ApplicationGroup {
  const offer = applicationOffer(application);
  if (application.archivedAt || application.status === 'declined'
    || applicationDecision(application) === 'declined'
    || applicationAfterInterest(application) === 'no'
    || application.workflow?.closedBy
    || offer?.status === 'accepted' || offer?.status === 'declined') return 'history';
  return application.status === 'pending' ? 'new' : 'process';
}

/** Resolve the exact current meeting; a cancelled latest meeting must not revive an old slot. */
export function currentApplicationMeeting(application: CandidateApplication): CandidateMeetingHistory | null {
  if (application.workflow) {
    const current = application.workflow.meeting;
    if (!current) return null;
    const full = application.meetings?.find(meeting => meeting.id === current.id)
      || (application.meeting?.id === current.id ? application.meeting : undefined);
    return { ...full, ...current };
  }
  return application.meeting || application.meetings?.[0] || null;
}

export function canReplyToMeeting(application: CandidateApplication, now = Date.now()): boolean {
  const meeting = currentApplicationMeeting(application);
  const start = validDate(meeting?.start);
  return !!meeting && ['pending', 'confirmed'].includes(meeting.status) && !!start && start.getTime() > now;
}

export function applicationStatus(application: CandidateApplication, now = Date.now()): { label: string; detail: string; attention: boolean; positive?: boolean } {
  const offer = applicationOffer(application);
  if (application.archivedAt) return { label: 'В истории', detail: 'Процесс перенесён в историю при остановке поиска. Договорённости и материалы сохранены.', attention: false };
  if (offer?.status === 'accepted') return { label: 'Оффер принят', detail: 'Ваш ответ сохранён. Договоритесь с командой о дальнейших шагах.', attention: false, positive: true };
  if (offer?.status === 'declined') return { label: 'Оффер отклонён', detail: 'Вы отказались от этого предложения. История процесса сохранена.', attention: false };
  if (application.status === 'declined') return { label: 'Вы отказались от участия', detail: 'Вакансия осталась в истории.', attention: false };
  if (applicationDecision(application) === 'declined') return { label: 'Компания завершила процесс', detail: 'По этой вакансии компания решила не продолжать. События и материалы доступны в истории.', attention: false };
  if (application.workflow?.closedBy) return { label: 'Подбор завершён', detail: 'Подбор по этой вакансии закрыт. История процесса сохранена.', attention: false };
  if (applicationAfterInterest(application) === 'no') return { label: 'Вы решили не продолжать', detail: 'Ваш ответ после встречи передан команде.', attention: false };
  if (offer?.status === 'sent') return { label: 'Вам предлагают работу', detail: 'Проверьте все условия и ответьте на предложение.', attention: true };
  if (application.status === 'pending') return { label: 'Новое предложение', detail: 'Посмотрите описание и решите, хотите ли участвовать.', attention: true };
  const meeting = currentApplicationMeeting(application);
  if (meeting?.status === 'pending') return canReplyToMeeting(application, now)
    ? { label: 'Подтвердите время', detail: 'Работодатель предлагает встречу. Подтвердите время, если оно вам удобно.', attention: true }
    : { label: 'Время требует уточнения', detail: 'Это время уже недоступно для подтверждения. Ожидаем нового предложения от команды.', attention: false };
  if (meeting?.status === 'live') return { label: 'Встреча идёт', detail: 'Можно присоединиться к разговору с командой.', attention: true };
  if (meeting?.status === 'confirmed') return { label: 'Встреча подтверждена', detail: 'Вы согласовали это время с командой.', attention: false };
  if (meeting?.status === 'completed') return applicationAfterInterest(application) === 'yes'
    ? { label: 'Ждём решения команды', detail: 'Вы сообщили, что хотите продолжить. Ответ появится здесь.', attention: false }
    : { label: 'Продолжим знакомство?', detail: 'Встреча завершилась. Сообщите команде, хотите ли вы продолжить.', attention: true };
  if (meeting?.status === 'declined' || meeting?.status === 'cancelled') return { label: 'Ждём нового времени', detail: 'Предыдущая встреча отменена. Новое предложение появится здесь.', attention: false };
  if (offer?.status === 'withdrawn' || offer?.status === 'superseded') return { label: 'Ожидаем новые условия', detail: 'Предыдущая версия оффера больше не доступна для ответа.', attention: false };
  return { label: application.approvedAutomatically ? 'Согласовано автоматически' : 'Вы участвуете', detail: 'Ждём предложения о встрече от работодателя. Время вы подтвердите отдельно.', attention: false };
}

export function validDate(value?: string | null): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date : null;
}

export function workspaceDateTime(value?: string | null): string {
  const date = validDate(value);
  return date ? new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' }).format(date) : 'Дата не указана';
}

export function meetingInterval(meeting: { start: string; end: string }): string {
  const start = validDate(meeting.start), end = validDate(meeting.end);
  if (!start) return 'Время уточняется';
  const sameDay = end && start.toDateString() === end.toDateString();
  return `${workspaceDateTime(meeting.start)}${end ? ` — ${sameDay ? new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' }).format(end) : workspaceDateTime(meeting.end)}` : ''}`;
}

/** Only persisted messages and entity snapshots: this is not an inferred audit trail. */
export function applicationHistory(application: CandidateApplication): ApplicationHistoryItem[] {
  const items: ApplicationHistoryItem[] = [];
  if (application.status === 'accepted') items.push({ id: `${application.id}:interest`, label: application.approvedAutomatically ? 'Участие согласовано автоматически' : 'Вы согласились участвовать', tone: 'blue' });
  else if (application.status === 'declined') items.push({ id: `${application.id}:interest`, label: 'Вы отказались от участия', tone: 'neutral' });

  const meetings = new Map<string, CandidateMeetingHistory>();
  for (const meeting of application.meetings || []) meetings.set(meeting.id, meeting);
  if (application.meeting && !meetings.has(application.meeting.id)) meetings.set(application.meeting.id, application.meeting);
  const current = currentApplicationMeeting(application);
  if (current) meetings.set(current.id, { ...meetings.get(current.id), ...current });
  const meetingLabels: Record<CandidateMeetingHistory['status'], string> = {
    pending: 'Предложено время встречи', confirmed: 'Время встречи подтверждено', live: 'Встреча идёт',
    completed: 'Встреча завершена', cancelled: 'Встреча отменена', declined: 'Вы отклонили время встречи',
  };
  const orderedMeetings = [...meetings.values()].sort((left, right) => (left.revision ?? 0) - (right.revision ?? 0));
  for (const meeting of orderedMeetings) items.push({
    id: `meeting:${meeting.id}`, meetingId: meeting.id, materials: meeting.status === 'completed',
    label: meeting.status === 'cancelled' && meeting.cancellationReason === 'rescheduled' ? 'Встреча перенесена' : meetingLabels[meeting.status],
    detail: meetingInterval(meeting), at: meeting.completedAt || meeting.cancelledAt,
    tone: meeting.status === 'completed' ? 'green' : ['pending', 'confirmed', 'live'].includes(meeting.status) ? 'blue' : 'neutral',
  });

  const messages = application.workflow ? application.workflow.messages : application.messages || [];
  for (const message of messages.filter(message => message.system)) items.push({
    id: `message:${message.id}`, label: message.text, at: message.at, tone: 'neutral',
  });
  const offer = applicationOffer(application);
  if (offer) {
    const labels: Record<CandidateOffer['status'], string> = {
      sent: 'Получен оффер', accepted: 'Оффер принят', declined: 'Оффер отклонён', superseded: 'Условия оффера обновлены', withdrawn: 'Оффер отозван',
    };
    // createdAt/sentAt must never masquerade as the missing withdrawal/update time.
    const at = ['accepted', 'declined'].includes(offer.status) ? offer.answeredAt : offer.status === 'sent' ? offer.sentAt : null;
    items.push({ id: `offer:${offer.id}:${offer.version}`, label: labels[offer.status], detail: `Версия ${offer.version}`, at, tone: offer.status === 'accepted' ? 'green' : offer.status === 'sent' ? 'blue' : 'neutral' });
  }
  if (application.archivedAt) items.push({ id: `${application.id}:archived`, label: 'Процесс перенесён в историю', at: application.archivedAt, tone: 'neutral' });
  return items.sort((left, right) => {
    const leftTime = validDate(left.at)?.getTime(), rightTime = validDate(right.at)?.getTime();
    if (leftTime === undefined) return rightTime === undefined ? 0 : 1;
    if (rightTime === undefined) return -1;
    return leftTime - rightTime;
  });
}
