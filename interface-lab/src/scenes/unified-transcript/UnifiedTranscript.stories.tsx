import type { Meta, StoryObj } from '@storybook/react-vite';
import { UnifiedTranscriptPreview } from './UnifiedTranscriptPreview';

const meta = {
  id: 'hr-vision-unified-transcript',
  title: 'HR Vision/Главы внутри расшифровки',
  component: UnifiedTranscriptPreview,
  parameters: { layout: 'fullscreen', controls: { disable: true } },
} satisfies Meta<typeof UnifiedTranscriptPreview>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Reading: Story = { name: 'Единая расшифровка' };
export const Outline: Story = { name: 'Оглавление раскрыто', args: { initiallyOpen: true } };
