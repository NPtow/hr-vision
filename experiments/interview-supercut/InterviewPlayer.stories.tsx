import type { Meta, StoryObj } from '@storybook/react-vite';
import { InterviewPlayer } from './InterviewPlayer';

const meta = {
  title: 'HR Vision/Просмотр интервью · Supercut',
  id: 'hr-vision-interview-supercut',
  component: InterviewPlayer,
  parameters: { layout: 'fullscreen', controls: { disable: true } },
} satisfies Meta<typeof InterviewPlayer>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Review: Story = { name: '01 · Просмотр', args: { initialView: 'review' } };
export const Fullscreen: Story = { name: '02 · Во весь экран', args: { initialView: 'fullscreen' } };
export const Comments: Story = { name: '03 · Комментарий к моменту', args: { initialView: 'comments' } };
