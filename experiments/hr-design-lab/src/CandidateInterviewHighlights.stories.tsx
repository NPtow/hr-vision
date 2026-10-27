import type { Meta, StoryObj } from '@storybook/react-vite';
import { CandidateInterviewHighlights } from './scenes/candidate-interview-highlights/CandidateInterviewHighlights';
const meta = {
  title: 'HR Vision/Профиль кандидата · интервью',
  id: 'hr-vision-candidate-highlights',
  component: CandidateInterviewHighlights,
  parameters: { layout:'fullscreen', controls:{disable:true} },
} satisfies Meta<typeof CandidateInterviewHighlights>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Profile: Story = {name:'Макет · главное из интервью'};
