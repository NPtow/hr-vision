import type { Meta, StoryObj } from '@storybook/react-vite';
import { EmployerMeetingStates } from './EmployerMeetingStates';

const meta = {
  title: 'HR Vision/Задачи · состояния встречи',
  id: 'hr-vision-employer-meeting-states',
  component: EmployerMeetingStates,
  parameters: { layout: 'fullscreen', controls: { disable: true } },
  args: { initialScenario: 'confirmed' },
} satisfies Meta<typeof EmployerMeetingStates>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Upcoming: Story = { name: '01 Ближайшая встреча' };
export const Pending: Story = { name: '02 Ожидает подтверждения', args: { initialScenario: 'pending' } };
export const Live: Story = { name: '03 Идёт встреча', args: { initialScenario: 'live' } };
export const Completed: Story = { name: '04 Проведённая встреча', args: { initialScenario: 'completed' } };
export const Repeat: Story = { name: '05 Следующая и прошлая', args: { initialScenario: 'repeat' } };
