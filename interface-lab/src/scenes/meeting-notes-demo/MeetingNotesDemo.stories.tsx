import type { Meta, StoryObj } from '@storybook/react-vite';
import { MeetingNotesDemoEntry } from './MeetingNotesDemoEntry';

const meta = {
  title: 'HR Vision/Встреча · запись и заметки',
  id: 'hr-vision-meeting-notes-demo',
  component: MeetingNotesDemoEntry,
  parameters: { layout: 'fullscreen', controls: { disable: true } },
} satisfies Meta<typeof MeetingNotesDemoEntry>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Workspace: Story = { name: 'Рабочая встреча' };
