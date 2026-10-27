import type { Meta, StoryObj } from '@storybook/react-vite';
import { CandidateHeaderLab } from './scenes/candidate-header-lab/CandidateHeaderLab';

const meta = {
  title: 'HR Vision/Шапка кандидата · варианты',
  id: 'hr-vision-candidate-header',
  component: CandidateHeaderLab,
  parameters: { layout: 'fullscreen', controls: { disable: true } },
} satisfies Meta<typeof CandidateHeaderLab>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Inline: Story = { name: '01 · Одна строка', args: { initialVariant: 'inline' } };
export const Overlay: Story = { name: '02 · Внутри видео', args: { initialVariant: 'overlay' } };
export const Side: Story = { name: '03 · Рядом с видео', args: { initialVariant: 'side' } };
