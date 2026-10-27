import type { Meta, StoryObj } from '@storybook/react-vite';
import { ATSJobExample } from './scenes/ats-job-example/ATSJobExample';
import { JobJourney } from './scenes/job-journeys/JobJourney';
import { contactsJourney } from './scenes/job-journeys/contacts';
import { agencyJourney } from './scenes/job-journeys/agency';

const meta = { title: 'HR Vision/CJM', id: 'hr-vision-cjm', parameters: { layout: 'fullscreen', controls: { disable: true } } } satisfies Meta;
export default meta;
type Story = StoryObj<typeof meta>;
export const Agency: Story = { name: '02 HR-агентство', render: () => <JobJourney key={agencyJourney.id} data={agencyJourney}/> };
export const Contacts: Story = { name: '01 База контактов', render: () => <JobJourney key={contactsJourney.id} data={contactsJourney}/> };
export const Ats: Story = { name: '03 Бесплатная ATS', render: () => <ATSJobExample/> };
