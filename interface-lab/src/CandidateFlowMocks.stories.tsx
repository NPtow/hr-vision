import type { Meta, StoryObj } from '@storybook/react-vite';
import { CandidateFlowMocks } from './scenes/candidate-portal/CandidateFlowMocks';

const meta = {
  title: 'HR Vision/Кандидат · путь по CV',
  id: 'candidate-cv-flow',
  component: CandidateFlowMocks,
  parameters: {
    layout: 'fullscreen',
    controls: { disable: true },
  },
} satisfies Meta<typeof CandidateFlowMocks>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Step3BookingConfirmation: Story = {
  name: '01 Подтверждение записи (без шума)',
  render: () => <CandidateFlowMocks initialStep="booking" />,
};

export const Step4CalendarOnboarding: Story = {
  name: '02 Вкладка встреч + Онбординг-тур',
  render: () => <CandidateFlowMocks initialStep="calendar" />,
};

export const Step5PostInterviewDebrief: Story = {
  name: '03 Итоги встречи и воронка бота',
  render: () => <CandidateFlowMocks initialStep="debrief" />,
};

