import type { Meta, StoryObj } from '@storybook/react-vite';
import { XpmMap } from './scenes/xpm/XpmMap';
import { agencyStrategy } from './scenes/xpm/agency';
import { contactsStrategy } from './scenes/xpm/contacts';
import { atsStrategy } from './scenes/xpm/ats';
const strategies = [agencyStrategy, contactsStrategy, atsStrategy];
const meta = {
  title: 'HR Vision/XPM · карта процесса', id: 'hr-vision-xpm',
  component: XpmMap,
  parameters: { layout: 'fullscreen', controls: { disable: true } },
  args: { strategies, initialStrategy: 'agency' },
} satisfies Meta<typeof XpmMap>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Overview: Story = { name: '01 Агентство' };
export const Contacts: Story = { name: '02 База контактов', args: { initialStrategy: 'contacts' } };
export const Ats: Story = { name: '03 Бесплатная ATS', args: { initialStrategy: 'ats' } };
