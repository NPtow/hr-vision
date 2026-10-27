import type { StorybookConfig } from '@storybook/react-vite';
const config: StorybookConfig = {
  stories: ['../*.stories.tsx'],
  staticDirs: process.env.HR_SUPERCUT_MEDIA_DIR
    ? [{ from: process.env.HR_SUPERCUT_MEDIA_DIR, to: '/interview-media' }]
    : [],
  framework: '@storybook/react-vite',
  addons: [],
  core: { disableTelemetry: true },
};
export default config;
