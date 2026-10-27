import { addons } from 'storybook/manager-api';
import { create } from 'storybook/theming';
addons.setConfig({
  theme: create({ base: 'light', brandTitle: 'HR Vision · Interface Lab', colorPrimary: '#292a25', colorSecondary: '#55574e', appBg: '#f4f1e8', appContentBg: '#f4f1e8', barSelectedColor: '#292a25', fontBase: '-apple-system, BlinkMacSystemFont, sans-serif' }),
  sidebar: { showRoots: true },
  layoutCustomisations: { showPanel: () => false },
});
