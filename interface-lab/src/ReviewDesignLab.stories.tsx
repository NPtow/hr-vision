import type { Meta, StoryObj } from '@storybook/react-vite';
import { ReviewDesignLab } from './scenes/review-design-lab/ReviewDesignLab';

const meta = {
  title: 'HR Vision/Кандидат · шесть дизайн-вариантов',
  id: 'hr-vision-review-design',
  component: ReviewDesignLab,
  parameters: { layout: 'fullscreen', controls: { disable: true } },
} satisfies Meta<typeof ReviewDesignLab>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Gallery: Story = { name: 'Сравнить шесть вариантов' };
export const System: Story = { name: 'Инспектор', args: { initialDesign: 'system' } };
export const Soft: Story = { name: 'Поверхности', args: { initialDesign: 'soft' } };
export const Editorial: Story = { name: 'Типографика', args: { initialDesign: 'editorial' } };
export const Compact: Story = { name: 'Компактный', args: { initialDesign: 'compact' } };
export const Continuous: Story = { name: 'Сегменты', args: { initialDesign: 'continuous' } };
export const Focus: Story = { name: 'Контекст', args: { initialDesign: 'focus' } };
