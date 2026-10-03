import type { Meta, StoryObj } from '@storybook/react-vite';
import { AgencyProduct } from './scenes/agency-product/AgencyProduct';
const meta = {
  title: 'HR Vision/Продукт · агентство', id: 'hr-vision-product', component: AgencyProduct,
  parameters: { layout: 'fullscreen', controls: { disable: true } },
} satisfies Meta<typeof AgencyProduct>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Manager: Story = { name: '01 Нанимающий · первая подборка', args: { initialRole: 'manager' } };
export const Candidate: Story = { name: '02 Кандидат · предложение и процесс', args: { initialRole: 'candidate' } };
