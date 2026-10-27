import type { Meta, StoryObj } from '@storybook/react-vite';
import { InterviewFeedbackLab } from './scenes/interview-feedback-lab/InterviewFeedbackLab';

const meta = {
  title: 'HR Vision/Обратная связь · варианты',
  id: 'hr-vision-interview-feedback',
  component: InterviewFeedbackLab,
  parameters: { layout: 'fullscreen', controls: { disable: true } },
} satisfies Meta<typeof InterviewFeedbackLab>;

export default meta;
type Story = StoryObj<typeof meta>;
export const Compare: Story = { name: 'Три композиции в кабинете' };
export const Tiles: Story = { name: 'Плитки · выбранный вариант', args: { preferred: true } };
