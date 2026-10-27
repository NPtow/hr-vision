export type CandidateAccessState = {
  status: 'not_requested' | 'queued' | 'sending' | 'sent' | 'failed' | 'unknown';
  mode: 'credentials' | 'signin' | null;
  emailConfigured: boolean;
  recipientEmail: string | null;
  canSend: boolean;
  requestId?: string | null;
  deliveryId?: string | null;
  blockedReason?: 'email_not_configured' | 'booking_consent_required' | 'resume_required' | 'email_conflict' | 'unknown_delivery' | 'already_sent' | 'credentials_changed' | 'delivery_in_progress' | 'email_invalid' | 'interview_unavailable' | null;
  lastError?: string | null;
};

export function candidateAccessMessage(state: CandidateAccessState): string {
  if (['not_requested', 'failed'].includes(state.status) && state.blockedReason) {
    switch (state.blockedReason) {
      case 'credentials_changed': return 'Данные входа изменились. Проверьте аккаунт кандидата.';
      case 'email_not_configured': return 'Отправка писем пока недоступна.';
      case 'email_invalid': return 'Не удалось определить почту кандидата.';
      case 'interview_unavailable': return 'Доступ будет доступен после назначения интервью.';
      case 'email_conflict': return 'Эта почта связана с другим профилем. Уточните адрес кандидата.';
    }
  }
  switch (state.status) {
    case 'queued': return 'Письмо ожидает отправки.';
    case 'sending': return 'Отправляем письмо…';
    case 'sent': return `Письмо передано почтовому сервису${state.recipientEmail ? ` для ${state.recipientEmail}` : ''}.`;
    case 'unknown': return 'Результат отправки неизвестен. Уточните у кандидата, пришло ли письмо. Повторная отправка заблокирована.';
    case 'failed': return 'Письмо не отправлено. Можно повторить попытку.';
  }
  switch (state.blockedReason) {
    case 'email_not_configured': return 'Отправка писем пока недоступна.';
    case 'booking_consent_required': return 'Кандидат ещё не подтвердил подготовку профиля при записи.';
    case 'resume_required': return 'Сначала нужно подготовить профиль из резюме.';
    case 'email_conflict': return 'Эта почта связана с другим профилем. Уточните адрес кандидата.';
    default: return state.recipientEmail
      ? state.mode === 'signin' ? `Отправим ссылку для входа на ${state.recipientEmail}. Пароль останется прежним.`
        : `Отправим ссылку на готовый профиль, логин и пароль на ${state.recipientEmail}.`
      : 'Для отправки доступа нужна подтверждённая почта кандидата.';
  }
}

/** A timeout never permits a new send command. A known failed delivery does. */
export function candidateAccessRequestId(state: CandidateAccessState, previous: string, create: () => string, responseUncertain = false): string {
  if (responseUncertain && previous) return previous;
  if (state.status === 'failed') return create();
  return state.requestId || previous || create();
}
