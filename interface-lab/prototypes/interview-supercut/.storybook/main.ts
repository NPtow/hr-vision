import type { StorybookConfig } from '@storybook/react-vite';
const config: StorybookConfig = {
  stories: ['../*.stories.tsx'],
  staticDirs: [{ from: '../../../../output/prototypes/interview-supercut-20261019/private', to: '/interview-media' }],
  framework: '@storybook/react-vite',
  addons: [],
  core: { disableTelemetry: true },
};
export default config;
