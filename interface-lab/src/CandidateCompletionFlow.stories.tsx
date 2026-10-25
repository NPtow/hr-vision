import type { Meta, StoryObj } from '@storybook/react-vite';
import { CandidateJourneyPreview } from './scenes/candidate-portal/CandidateJourneyPreview';
const meta = { title: 'HR Vision/Путь кандидата после интервью', id: 'hr-vision-candidate-completion', component: CandidateJourneyPreview, parameters: { layout: 'fullscreen', controls: { disable: true } } } satisfies Meta<typeof CandidateJourneyPreview>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Guest: Story = { name: '01 После встречи — почта есть', args: { entry: 'guest' } };
export const NoEmail: Story = { name: '02 После встречи — почты нет', args: { entry: 'no-email' } };
export const Registered: Story = { name: '03 После встречи — кабинет есть', args: { entry: 'registered' } };
export const Employer: Story = { name: '04 После встречи с работодателем', args: { entry: 'employer' } };
