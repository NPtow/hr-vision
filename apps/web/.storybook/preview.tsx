import type { Preview } from '@storybook/react-vite';
import '../src/base.css';
const preview: Preview = {
  parameters: {
    layout: 'fullscreen',
    options: { storySort: { order: ['HR Vision', ['Продукт · агентство', 'Агентский подбор', 'Пример ATS', 'CJM', 'CJM кандидата', 'Архив CJM v3']] } },
    controls: { expanded: true },
  },
};
export default preview;
