import { reviewFeedbackSteps } from './feedback-variants';
import { ctaSteps } from './cta-variants';
export const scenes = [
  { id: 'intro', story: 'intro', label: 'Начало', title: 'От найма — к задаче бизнеса', description: 'Первая фраза уходит, а причина найма становится главной.', duration: 9, end: .88, steps: [[0, 'Нанять сотрудника'], [.24, 'Зачеркнуть'], [.57, 'Сменить мысль'], [1, 'Решить задачу']] },
  { id: 'needs', story: 'needs', label: 'Задача', title: 'Потребность своими словами', description: 'Задача бизнеса становится отправной точкой подбора. Текст можно изменить.', duration: 8, end: .86, steps: [[0, 'Появление'], [.42, 'Ваша задача'], [1, 'Готово к подбору']] },
  { id: 'people', story: 'people', label: 'База', title: 'Две тысячи историй', description: 'Профили движутся в ленте, новые люди пополняют базу.', duration: 18, end: 1, steps: [[0, 'Лента профилей'], [.34, 'Новые люди'], [.67, 'Новый опыт'], [1, 'База растёт']] },
  { id: 'connections', story: 'connections', label: 'Граф', title: 'Связи, которые находят человека', description: 'Задача, опыт и интересы соединяются. Подходящие связи остаются в фокусе.', duration: 12, end: .9, steps: [[0, 'Потребность'], [.25, 'Появляются люди'], [.59, 'Паутина связей'], [1, 'Совпадения']] },
  { id: 'interest', story: 'interest', label: 'Интерес', title: 'Взаимный интерес', description: 'Вакансия отправляется подходящим людям. В ответ приходит готовность познакомиться.', duration: 11, end: .95, steps: [[0, 'Ваша вакансия'], [.3, 'Подходящие люди'], [.56, 'Отправка'], [1, 'Есть интерес']] },
  { id: 'interview', story: 'interview', label: 'Интервью', title: 'Мы уже провели интервью со всеми кандидатами и оценили их.', description: 'Запись, ключевые ответы, оценка и причины рекомендации в одном профиле.', duration: 24, end: 1, steps: [[0, 'Опыт'], [.26, 'Возвращение клиентов'], [.52, 'Работа в CRM'], [.79, 'Ожидания']] },
  { id: 'review-feedback', story: 'review-feedback', label: 'После просмотра', title: 'Ваше впечатление меняет следующего кандидата', description: 'Фидбэк после первичного интервью уточняет подборку: карточки пересобираются, подходящий опыт выходит вперёд.', duration: 20, end: 1, steps: reviewFeedbackSteps },
  { id: 'calendar', story: 'calendar', label: 'Календарь', title: 'Встреча без переписки', description: 'Выбор свободного времени → приглашение → подтверждение кандидата.', duration: 12, end: 1, steps: [[0, 'Свободное время'], [.28, 'Выбор слота'], [.58, 'Приглашение в пути'], [.86, 'Подтверждение']] },
  { id: 'meeting', story: 'meeting', label: 'Встреча', title: 'Разговор с кандидатом', description: 'Встреча помогает уточнить то, что важно для вашей задачи.', duration: 10, end: .85, steps: [[0, 'Подключение'], [.34, 'Разговор'], [.78, 'Вопрос о Битрикс24'], [1, 'Важный вывод']] },
  { id: 'feedback', story: 'feedback', label: 'После встречи', title: 'Уточняем подбор после встречи', description: 'Недостаток опыта в Битрикс24 превращается в новое требование и нового кандидата.', duration: 12, end: 1, steps: [[0, 'После встречи'], [.3, 'Чего не хватило'], [.63, 'Требование в графе'], [.87, 'Новый кандидат']] },
  { id: 'offer', story: 'offer', label: 'Оффер', title: 'Найти своего и не потерять', description: 'От согласованных условий — до первого рабочего дня.', duration: 12, end: 1, steps: [[0, 'Условия'], [.4, 'Оффер отправлен'], [.65, 'Оффер принят'], [.91, 'Первый день']] },
  { id: 'finish', story: 'finish', label: 'Финал', title: 'Оплата после найма', description: 'Завершение истории и начало вашей собственной задачи.', duration: 8, end: 1, steps: [[0, 'Нашли своего'], [.45, 'Оплата после найма'], [1, 'Начать подбор']] },
  { id: 'cta', story: 'cta', label: 'Кнопка', title: 'Кнопка с таймером', description: '50% в первые 10 секунд, затем 30% на 30 секунд. Контур показывает оставшееся время.', duration: 40, end: 1, steps: ctaSteps },
] as const;
export type SceneId = typeof scenes[number]['id'];
export type SceneDefinition = typeof scenes[number];
export const sceneById = (id: string) => scenes.find(scene => scene.id === id) ?? scenes[0];
export type LabState = { progress: number; playing: boolean; speed: number; runId: number };
export type Command = { kind: 'play' | 'pause' | 'replay' | 'reset' } | { kind: 'seek' | 'speed'; value: number };
export const CHANNEL = 'hr-vision-company-motion-v1';
