import type { Meta, StoryObj } from '@storybook/react-vite';
import { SceneHost } from './SceneHost';

const meta = {
  id: 'hr-vision-company-motion',
  title: 'HR Vision / Сайт / Анимации',
  component: SceneHost,
  parameters: { layout: 'fullscreen' },
  args: { playing: false, runId: 0, compact: false },
} satisfies Meta<typeof SceneHost>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Intro: Story = { name: '01 · Начало', args: { scene: 'intro' } };
export const Needs: Story = { name: '02 · Задача', args: { scene: 'needs' } };
export const People: Story = { name: '03 · База кандидатов', args: { scene: 'people' } };
export const Connections: Story = { name: '04 · Граф связей', args: { scene: 'connections' } };
export const Interest: Story = { name: '05 · Взаимный интерес', args: { scene: 'interest' } };
export const InterestLetters: Story = { name: '05 A · Письма', args: { scene: 'interest', interestVariant: 'letters' } };
export const InterestSignal: Story = { name: '05 B · Импульс', args: { scene: 'interest', interestVariant: 'signal' } };
export const InterestCards: Story = { name: '05 C · Карточки', args: { scene: 'interest', interestVariant: 'cards' } };
export const Interview: Story = { name: '06 · Интервью', args: { scene: 'interview' } };
export const InterviewFocus: Story = { name: '06 A · Фокус', args: { scene: 'interview', interviewVariant: 'focus' } };
export const InterviewCloseup: Story = { name: '06 B · Крупный план', args: { scene: 'interview', interviewVariant: 'closeup' } };
export const ReviewFeedback: Story = { name: '06 C · Пересборка после просмотра', args: { scene: 'review-feedback' } };
export const Calendar: Story = { name: '07 · Календарь', args: { scene: 'calendar' } };
export const Meeting: Story = { name: '08 · Встреча', args: { scene: 'meeting' } };
export const Feedback: Story = { name: '09 · Обратная связь', args: { scene: 'feedback' } };
export const FeedbackGraph: Story = { name: '09 A · Уточнение графа', args: { scene: 'feedback', feedbackVariant: 'graph' } };
export const FeedbackReshuffle: Story = { name: '09 A · Пересборка', args: { scene: 'feedback', feedbackVariant: 'reshuffle' } };
export const FeedbackCompare: Story = { name: '09 B · До и после', args: { scene: 'feedback', feedbackVariant: 'compare' } };
export const Offer: Story = { name: '10 · Оффер', args: { scene: 'offer' } };
export const Finish: Story = { name: '11 · Финал', args: { scene: 'finish' } };

export const Cta: Story = { name: '00 · Кнопка с таймером', args: { scene: 'cta' } };
export const CtaLiquid: Story = { name: '00 A · Уровень скидки', args: { scene: 'cta', ctaVariant: 'liquid' } };
export const CtaPerimeter: Story = { name: '00 B · Контур времени', args: { scene: 'cta', ctaVariant: 'perimeter' } };
export const CtaRibbon: Story = { name: '00 C · Лента скидки', args: { scene: 'cta', ctaVariant: 'ribbon' } };
