export type Screen = "panel" | "add" | "schedule";
export type Candidate = {
  id: string;
  name: string;
  initials: string;
  role: string;
  city: string;
  experience: string;
  salary: string;
  email?: string;
  source?: string;
  note?: string;
  color: string;
  summary: string;
  strength: string;
  question: string;
  new?: boolean;
};
export type Meeting = {
  candidateId: string;
  day: string;
  time: string;
  duration: number;
  status: "pending" | "confirmed";
};
export const position = "Менеджер по работе с клиентами";
export const initialCandidates: Candidate[] = [
  {
    id: "anna",
    name: "Анна Миронова",
    initials: "АМ",
    role: "Key Account Manager",
    city: "Москва",
    experience: "5 лет",
    salary: "150–180 тыс. ₽",
    color: "#e6e9fd",
    summary:
      "Возвращала клиентов к регулярным заказам. Вела портфель из 42 компаний и развивала повторные продажи.",
    strength: "Есть опыт развития действующей базы",
    question: "Уточнить личный вклад в рост повторных продаж.",
    new: true,
  },
  {
    id: "mikhail",
    name: "Михаил Белов",
    initials: "МБ",
    role: "Менеджер по продажам B2B",
    city: "Санкт-Петербург",
    experience: "4 года",
    salary: "140–170 тыс. ₽",
    color: "#eee6de",
    summary:
      "Развивал продажи оборудования, вёл длинные сделки и общался с закупками. Работал с повторными заказами.",
    strength: "Понимает длинный цикл сделки",
    question: "Обсудить соотношение новых и повторных продаж.",
  },
  {
    id: "elena",
    name: "Елена Орлова",
    initials: "ЕО",
    role: "Менеджер по работе с клиентами",
    city: "Москва",
    experience: "6 лет",
    salary: "170–200 тыс. ₽",
    color: "#dfebe5",
    summary:
      "Развивала ключевых клиентов и выстраивала клиентский сервис. Работала с крупными B2B-контрактами.",
    strength: "Развивала крупные клиентские аккаунты",
    question: "Сверить ожидания по доходу и объём ответственности.",
    new: true,
  },
  {
    id: "nikita",
    name: "Никита Соколов",
    initials: "НС",
    role: "Account Manager",
    city: "Казань",
    experience: "3 года",
    salary: "120–150 тыс. ₽",
    color: "#e7e4f1",
    summary:
      "Вёл клиентов SaaS-продукта, помогал с запуском и продлением договоров. Работал в CRM с задачами на удержание.",
    strength: "Есть опыт продления контрактов",
    question: "Проверить самостоятельность в сложных переговорах.",
  },
];
export const initialMeetings: Meeting[] = [
  {
    candidateId: "mikhail",
    day: "2026-10-09",
    time: "12:00",
    duration: 30,
    status: "confirmed",
  },
];
export const chapters = [
  {
    time: 82,
    end: 133,
    title: "Опыт и зона ответственности",
    label: "Опыт",
    quote:
      "«Я вела клиентов после первой сделки: помогала разобраться с продуктом и находила повод для следующего заказа».",
  },
  {
    time: 260,
    end: 337,
    title: "Как возвращала клиентов",
    label: "Повторные продажи",
    quote:
      "«Сначала выясняла, почему клиент перестал заказывать. Делила базу по причинам и для каждой группы готовила своё предложение».",
  },
  {
    time: 575,
    end: 643,
    title: "Сложный разговор с клиентом",
    label: "Переговоры",
    quote:
      "«Важно было договориться о следующем шаге, а не просто закончить разговор. Я фиксировала конкретную дату и возвращалась к клиенту».",
  },
  {
    time: 832,
    end: 904,
    title: "Ожидания от новой команды",
    label: "Мотивация",
    quote:
      "«Хочется отвечать за развитие клиентов и видеть, как моя работа влияет на повторные продажи».",
  },
];
export const mmss = (n: number) =>
  `${String(Math.floor(n / 60)).padStart(2, "0")}:${String(Math.floor(n % 60)).padStart(2, "0")}`;
export const dayLabel = (day: string, short = false) =>
  new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: short ? "short" : "long",
    timeZone: "UTC",
  }).format(new Date(`${day}T12:00:00Z`));
export const weekday = (day: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    weekday: "short",
    timeZone: "UTC",
  }).format(new Date(`${day}T12:00:00Z`));
export function weekDays(offset: number) {
  return Array.from({ length: 7 }, (_, i) => {
    const date = new Date(Date.UTC(2026, 9, 8 + offset * 7 + i));
    return date.toISOString().slice(0, 10);
  });
}
const minutes = (t: string) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3));
// Fixture availability: a day consists of real intervals, not isolated start-time labels.
export function freeSlots(
  id: string,
  day: string,
  duration: number,
  meetings: Meeting[],
  editingId?: string,
) {
  const weekdayNumber = new Date(`${day}T12:00:00Z`).getUTCDay();
  if (weekdayNumber === 0 || weekdayNumber === 6) return [];
  const shift = id === "elena" ? 30 : id === "nikita" ? 60 : 0;
  const intervals = [
    [600 + shift, 720],
    [840, 1020 - shift],
  ];
  const booked = meetings.filter(
    (m) => m.day === day && m.candidateId !== editingId,
  );
  const slots: string[] = [];
  for (const [start, end] of intervals)
    for (let t = start; t + duration <= end; t += 30) {
      if (
        booked.some(
          (m) =>
            t < minutes(m.time) + m.duration && t + duration > minutes(m.time),
        )
      )
        continue;
      slots.push(
        `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`,
      );
    }
  return slots;
}
