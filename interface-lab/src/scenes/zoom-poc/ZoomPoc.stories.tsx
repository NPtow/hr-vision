import type { Meta, StoryObj } from '@storybook/react-vite';
import './zoom-poc.css';

function ZoomPoc() {
  return <div className="zoom-poc-story">
    <iframe
      title="МашаДаша · изолированная проверка Zoom"
      src="http://127.0.0.1:4321/"
      allow="camera; microphone; display-capture; fullscreen; autoplay; clipboard-write"
      referrerPolicy="no-referrer"
    />
    <a className="zoom-poc-story-link" href="http://127.0.0.1:4321/" target="_blank" rel="noreferrer">Открыть отдельно</a>
  </div>;
}

const meta = {
  title: 'HR Vision/Zoom · отдельная проверка',
  id: 'hr-vision-zoom-poc',
  component: ZoomPoc,
  parameters: { layout: 'fullscreen', controls: { disable: true } },
} satisfies Meta<typeof ZoomPoc>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Workspace: Story = { name: 'Встреча и заметки' };
