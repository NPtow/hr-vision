import type { RecruiterCandidate } from './api';

/** Temporary visual fixture. Never persisted or submitted to the recruiter API. */
export function createAgendaPreview(now = new Date()): RecruiterCandidate[] {
  const names = ['Анна Миронова', 'Иван Петров', 'Мария Соколова', 'Алексей Орлов', 'Екатерина Волкова', 'Дмитрий Кузнецов', 'Полина Лебедева', 'Михаил Новиков', 'Ольга Фёдорова', 'Артём Морозов', 'Дарья Васильева', 'Николай Павлов', 'Софья Алексеева', 'Роман Семёнов', 'Елена Михайлова', 'Максим Егоров', 'Анастасия Белова', 'Андрей Ковалёв', 'Виктория Никитина', 'Сергей Громов', 'Алина Тихонова', 'Денис Захаров', 'Юлия Воронцова', 'Павел Романов', 'Ксения Филатова', 'Владислав Макаров', 'Валерия Смирнова', 'Тимур Андреев', 'Наталья Александрова', 'Константин Вишняков'];
  const roles = ['Менеджер по работе с клиентами', 'Продуктовый дизайнер', 'Frontend-разработчик', 'Аналитик данных', 'Руководитель отдела продаж'];
  const cities = ['Москва', 'Санкт-Петербург', 'Казань', 'Екатеринбург'];
  const companies = ['Север', 'Маяк', 'Орбита'];
  const counts = [2, 3, 1, 3, 2, 1, 4, 2, 3, 1, 2, 1, 3, 1, 1];
  const moscowDay = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit' }).format(now);
  const day = new Date(`${moscowDay}T00:00:00Z`);
  const candidates: RecruiterCandidate[] = [];
  counts.forEach(count => {
    do { day.setUTCDate(day.getUTCDate() + 1); } while ([0, 6].includes(day.getUTCDay()));
    for (let slot = 0; slot < count; slot++) {
      const i = candidates.length;
      const start = new Date(`${day.toISOString().slice(0, 10)}T16:00:00+03:00`);
      start.setMinutes(start.getMinutes() + slot * 30);
      const viaHH = i % 3 !== 2;
      candidates.push({
        id: `preview-candidate-${i + 1}`, name: names[i], email: `candidate${i + 1}@example.test`, accountActive: true,
        desiredRole: roles[i % roles.length], city: cities[i % cities.length],
        about: i % 3 === 0 ? 'Пять лет работаю с корпоративными клиентами. Развиваю долгосрочные отношения, веду переговоры и помогаю команде улучшать клиентский опыт.' : 'Хочу развиваться в команде с понятными задачами и возможностью влиять на результат.',
        telegram: '', geographies: [cities[i % cities.length]], workMode: i % 2 ? 'remote' : 'hybrid', desiredSalary: '150 000–180 000 ₽',
        availability: [], applications: [], consentReuse: true, autoApprove: false, videoConfigured: false,
        resume: { filename: `Резюме — ${names[i]}.pdf`, mime: 'application/pdf', size: 164000 + i * 1000, uploadedAt: now.toISOString(), extractedFields: ['name', 'city', 'desiredRole'] },
        source: { kind: viaHH ? 'hh' : 'registration', label: viaHH ? 'HH' : 'Самостоятельная регистрация' },
        sourceApplications: viaHH ? [{ id: `preview-vacancy-${i}`, title: roles[i % roles.length], company: companies[i % companies.length], url: '' }] : [],
        interview: { id: `preview-interview-${i + 1}`, start: start.toISOString(), end: new Date(start.getTime() + 30 * 60000).toISOString(), status: 'booked', recruiterName: 'Никита Пуссер' },
        internalProfile: i % 4 === 0 ? { summary: 'На встрече обсудить опыт работы и ожидания от новой роли.', evaluation: '' } : undefined,
      });
    }
  });
  return candidates;
}
