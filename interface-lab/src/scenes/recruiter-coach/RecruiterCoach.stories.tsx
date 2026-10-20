import type { Meta, StoryObj } from '@storybook/react-vite';
import { RecruiterCoach } from './RecruiterCoach';

const meta = {
  title: 'HR Vision/Рекрутёр · персональное интервью',
  id: 'hr-vision-recruiter-coach',
  component: RecruiterCoach,
  parameters: { layout: 'fullscreen', controls: { disable: true } },
} satisfies Meta<typeof RecruiterCoach>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Kretova: Story = { name: 'Александра Кретова · 16:00', args: { candidate: 'kretova' } };
export const Rybkina: Story = { name: 'Елена Рыбкина · 16:30', args: { candidate: 'rybkina' } };
