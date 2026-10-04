import type { Meta, StoryObj } from '@storybook/react-vite';
import { PanelAlternatives } from './scenes/panel-alternatives/PanelAlternatives';
const meta = {
  title: 'HR Vision/Варианты подборки', id: 'hr-vision-panel-alternatives', component: PanelAlternatives,
  parameters: { layout: 'fullscreen', controls: { disable: true } },
} satisfies Meta<typeof PanelAlternatives>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Gallery: Story = { name: '10 способов просмотра' };
