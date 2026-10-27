export const references: {
  name: string;
  url: string;
  pattern: string;
  screen: 'search' | 'calendar' | 'profile' | 'all';
}[] = [
  {
    name: 'Things — Today',
    url: 'https://culturedcode.com/things/features/',
    pattern: 'Берём спокойную иерархию и ближайшее действие. Не берём чекбоксы этапов, звёзды и показатели прогресса поиска.',
    screen: 'all',
  },
  {
    name: 'Welcome to the Jungle — matched jobs',
    url: 'https://go.welcometothejungle.com/candidate-migration-live',
    pattern: 'Берём ясный список предложений и подробности компании рядом. Не придумываем процент совпадения, зарплату или объяснение рекомендации.',
    screen: 'search',
  },
  {
    name: 'Linear — My Issues',
    url: 'https://linear.app/docs/my-issues',
    pattern: 'Берём смысловые группы реальных действий и компактные строки. Не переносим backlog, инженерные статусы и управление внутренней работой рекрутера.',
    screen: 'search',
  },
  {
    name: 'Notion Calendar — Upcoming',
    url: 'https://www.notion.com/product/calendar#features',
    pattern: 'Берём ближайшее событие с точным временем, контекстом и входом. Не превращаем указанные кандидатом свободные часы в подтверждённую встречу.',
    screen: 'calendar',
  },
  {
    name: 'Workable — подтверждение времени',
    url: 'https://help.workable.com/hc/en-us/articles/360007483594-Self-scheduled-events',
    pattern: 'Берём отдельное подтверждение выбранного времени и видимый часовой пояс. Не переносим свободное бронирование: HR Vision предлагает конкретный слот, отказ от него не исключает из вакансии.',
    screen: 'calendar',
  },
  {
    name: 'Apple Account — личные сведения',
    url: 'https://support.apple.com/guide/iphone/sign-in-and-manage-your-apple-account-iph76e54c61e/ios',
    pattern: 'Берём группировку личных данных и понятные точки редактирования. Не добавляем чужие настройки аккаунта; автосогласование вакансий и уведомления остаются самостоятельными решениями.',
    screen: 'profile',
  },
];
