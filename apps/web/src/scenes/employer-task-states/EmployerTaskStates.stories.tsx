import type { Meta, StoryObj } from '@storybook/react-vite';
import { EmployerTaskStates } from './EmployerTaskStates';
const meta = {
  title: 'HR Vision/Задачи · текущие экраны', id: 'hr-vision-employer-task-states', component: EmployerTaskStates,
  parameters: { layout: 'fullscreen', controls: { disable: true } },
} satisfies Meta<typeof EmployerTaskStates>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Completed: Story = { name: '01 Встреча', args: { initialScreen: 'completed' } };
export const Scheduled: Story = { name: '02 Предстоящая встреча', args: { initialScreen: 'scheduled' } };
export const NextStep: Story = { name: '03 Следующий шаг', args: { initialScreen: 'next-step' } };
export const Offer: Story = { name: '04 Оффер', args: { initialScreen: 'offer' } };
export const CompletedJournal: Story = { name: '05 Встреча · Журнал', args: { initialScreen: 'completed', initialVariant: 'journal' } };
export const CompletedWorkbench: Story = { name: '06 Встреча · Панель', args: { initialScreen: 'completed', initialVariant: 'workbench' } };
export const CompletedOutcome: Story = { name: '07 Встреча · Итог', args: { initialScreen: 'completed', initialVariant: 'outcome' } };
