import type { Meeting, Offer } from '../connected-journey/api';
import type { CandidateStage } from '../employer-workspace/candidate-stage-types';

/** Local, synthetic review data. No media, API identity, or outbound action is implied. */
export const taskPersonName = 'Анна · тест звонка';
export const taskVacancyTitle = 'Менеджер по продажам дверей';
export const taskStageIds = {
  interview: 'task-preview-interview',
  completed: 'task-preview-meeting-completed',
  completedSecond: 'task-preview-meeting-completed-second',
  upcoming: 'task-preview-meeting-upcoming',
  offer: 'task-preview-offer',
} as const;

export function makeTaskStages(): CandidateStage[] {
  const completed: Meeting = {
    id: 'task-preview-completed-meeting',
    revision: 1,
    start: '2026-10-14T10:00:00.000Z',
    end: '2026-10-14T10:30:00.000Z',
    startedAt: '2026-10-14T10:00:00.000Z',
    endedAt: '2026-10-14T10:30:00.000Z',
    status: 'completed',
    testSkip: true,
    joined: [],
  };
  const upcoming: Meeting = {
    id: 'task-preview-upcoming-meeting',
    revision: 1,
    start: '2026-10-27T10:00:00.000Z',
    end: '2026-10-27T10:30:00.000Z',
    status: 'confirmed',
    testSkip: false,
    joined: [],
  };
  const completedSecond: Meeting = {
    ...completed,
    id: 'task-preview-completed-second-meeting',
    start: '2026-10-17T10:00:00.000Z',
    end: '2026-10-17T10:30:00.000Z',
    startedAt: '2026-10-17T10:00:00.000Z',
    endedAt: '2026-10-17T10:30:00.000Z',
  };
  const offer: Offer = {
    id: 'task-preview-sent-offer',
    createdAt: '2026-10-19T10:00:00.000Z',
    status: 'sent',
    role: taskVacancyTitle,
    compensation: '120 000 ₽ + премия',
    startDate: '2026-10-26',
    format: 'Офис',
    expectations: 'Демо-оффер. Освоить продукт и CRM, начать самостоятельную работу с клиентами.',
    version: 1,
  };

  return [
    {
      id: taskStageIds.interview,
      kind: 'interview',
      title: 'Интервью',
      status: 'completed',
      date: '2026-10-13T10:00:00.000Z',
      notes: '',
      documents: [],
    },
    {
      id: taskStageIds.completed,
      kind: 'meeting',
      title: 'Встреча',
      status: 'completed',
      date: completed.start,
      notes: 'Демонстрационный этап для просмотра интерфейса. Новая встреча не проводилась; запись к этому макету не подключена.',
      documents: [],
      meetingId: completed.id,
      meeting: completed,
      feedback: {
        example: 'Обсудили опыт продаж, мотивацию и подход к первому контакту.',
        check: 'Дать короткий кейс по работе с возражениями.',
        at: '2026-10-14T10:35:00.000Z',
      },
    },
    {
      id: taskStageIds.completedSecond,
      kind: 'meeting',
      title: 'Встреча',
      status: 'completed',
      date: completedSecond.start,
      notes: 'Демонстрационный этап для просмотра интерфейса. Новая встреча не проводилась; запись к этому макету не подключена.',
      documents: [],
      meetingId: completedSecond.id,
      meeting: completedSecond,
      feedback: {
        example: 'Разобрали тестовое. Согласовали ожидания на первые три месяца.',
        check: 'Уточнить дату выхода и согласовать условия оффера.',
        at: '2026-10-17T10:35:00.000Z',
      },
    },
    {
      id: taskStageIds.upcoming,
      kind: 'meeting',
      title: 'Встреча',
      status: 'confirmed',
      date: upcoming.start,
      notes: '',
      documents: [],
      meetingId: upcoming.id,
      meeting: upcoming,
    },
    {
      id: taskStageIds.offer,
      kind: 'offer',
      title: 'Оффер',
      status: 'sent',
      date: offer.createdAt,
      notes: '',
      documents: [],
      offer,
    },
  ];
}
