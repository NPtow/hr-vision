import type { Meta, StoryObj } from '@storybook/react-vite';
import { JobJourney } from './scenes/job-journeys/JobJourney';
import { candidateAgencyJourney, candidateOtherJourney } from './scenes/job-journeys/candidates';

const meta = { title: 'HR Vision/CJM кандидата', id: 'hr-vision-candidate-cjm', parameters: { layout: 'fullscreen', controls: { disable: true } } } satisfies Meta;
export default meta;
type Story = StoryObj<typeof meta>;
export const Agency: Story = { name: '01 Агентская модель', render: () => <JobJourney key={candidateAgencyJourney.id} data={candidateAgencyJourney}/> };
export const Other: Story = { name: '02 Контакты и ATS', render: () => <JobJourney key={candidateOtherJourney.id} data={candidateOtherJourney}/> };
