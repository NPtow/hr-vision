import type { Meta, StoryObj } from '@storybook/react-vite';
import { AgencyProduct } from './scenes/agency-product/AgencyProduct';
import { CandidatePortal } from './scenes/candidate-portal/CandidatePortal';
import { RecruiterPortal } from './scenes/recruiter-portal/RecruiterPortal';
import { HHOutreach } from './scenes/hh-outreach/HHOutreach';
import { MeetingNotesDemoEntry } from './scenes/meeting-notes-demo/MeetingNotesDemoEntry';
import { CandidateFlowMocks } from './scenes/candidate-portal/CandidateFlowMocks';

const meta = {
  title: 'HR Vision/Продукт · агентство', id: 'hr-vision-product', component: AgencyProduct,
  parameters: { layout: 'fullscreen', controls: { disable: true } },
} satisfies Meta<typeof AgencyProduct>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Manager: Story = {
  name: '01 Нанимающий · вакансии', args: { initialRole: 'manager' },
  render: args => new URLSearchParams(location.search).get('demo') === 'meeting-notes'
    ? <MeetingNotesDemoEntry/> : new URLSearchParams(location.search).get('workspace') === 'outreach'
    ? <HHOutreach/> : new URLSearchParams(location.search).get('workspace') === 'recruiter'
    ? <RecruiterPortal/> : <AgencyProduct {...args}/>,
};
export const Candidate: Story = {
  name: '02 Кандидат · личный кабинет', args: { initialRole: 'candidate' },
  render: args => new URLSearchParams(location.search).get('legacy') === '1'
    ? <AgencyProduct {...args}/> : <CandidatePortal/>,
};

export const CandidateCVFlow: Story = {
  name: '03 Кандидат · путь по CV (Шаги 3 и 5)',
  render: () => <CandidateFlowMocks />,
};

export const Start: Story = { name: '00 Общий вход · аккаунты команды', args: { initialRole: 'start' } };

