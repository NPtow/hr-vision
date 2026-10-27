import type { Meta, StoryObj } from '@storybook/react-vite';
import { PooledBookingPreview } from './scenes/pooled-booking-preview/PooledBookingPreview';
const meta = { title: 'HR Vision/Общий календарь · макет', id: 'hr-vision-pooled-booking-preview', component: PooledBookingPreview, parameters: { layout: 'fullscreen', controls: { disable: true } } } satisfies Meta<typeof PooledBookingPreview>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Calendar: Story = { name: 'Выбор времени', args: {} };
export const Conflict: Story = { name: 'Время заняли при подтверждении', args: { simulateConflict: true } };
