import type { Preview } from '@storybook/react-vite';
import '../src/lab.css';
const preview: Preview = {
  parameters: {
    layout: 'fullscreen',
    options: { storySort: { order: ['Proteus', ['00 Галерея', '01 Proteus рядом', '02 Первый запуск', '03 Рабочее пространство', '04 Звонок и отчёт', '05 Настройки']] } },
    controls: { expanded: true },
  },
};
export default preview;
