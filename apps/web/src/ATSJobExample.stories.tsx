import type { Meta, StoryObj } from '@storybook/react-vite';
import { ATSJobExample } from './scenes/ats-job-example/ATSJobExample';

const meta = {
  title: 'HR Vision/ATS · джобы и события',
  id: 'hr-vision-ats-example',
  component: ATSJobExample,
  parameters: { layout: 'fullscreen', controls: { disable: true } },
} satisfies Meta<typeof ATSJobExample>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Overview: Story = { name: 'Один пример' };
