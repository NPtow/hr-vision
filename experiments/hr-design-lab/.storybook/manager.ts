import { addons } from 'storybook/manager-api';
import { create } from 'storybook/theming';
addons.setConfig({
  theme: create({ base: 'light', brandTitle: 'Proteus · Interface Lab', brandImage: '/brand/proteus.png', colorPrimary: '#4965DB', colorSecondary: '#4965DB', appBg: '#FAF9F6', appContentBg: '#F4F3EF', barSelectedColor: '#4965DB', fontBase: 'Inter, -apple-system, BlinkMacSystemFont, sans-serif' }),
  sidebar: { showRoots: true },
  layoutCustomisations: { showPanel: () => false },
});
