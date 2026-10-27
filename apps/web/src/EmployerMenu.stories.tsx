import type { Meta, StoryObj } from '@storybook/react-vite';
import { EmployerMenuLab } from './scenes/employer-menu/EmployerMenuLab';

const meta = {
  title: 'HR Vision/Меню работодателя',
  id: 'hr-vision-employer-menu',
  component: EmployerMenuLab,
  parameters: { layout: 'fullscreen', controls: { disable: true } },
} satisfies Meta<typeof EmployerMenuLab>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Gallery: Story = { name: 'Пять вариантов' };
