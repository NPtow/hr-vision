import type { Meta, StoryObj } from '@storybook/react-vite';
import { HHOutreach } from './scenes/hh-outreach/HHOutreach';
const meta = {
  title: 'HR Vision/Рассылка HH', id: 'hr-vision-outreach', component: HHOutreach,
  parameters: { layout: 'fullscreen', controls: { disable: true } },
} satisfies Meta<typeof HHOutreach>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Campaigns: Story = { name: 'Кампании и записи на интервью' };
