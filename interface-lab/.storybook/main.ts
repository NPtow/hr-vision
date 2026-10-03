import type { StorybookConfig } from '@storybook/react-vite';
import tailwindcss from '@tailwindcss/vite';
const config: StorybookConfig = {
  stories: ['../src/**/*.stories.tsx'],
  staticDirs: ['../public'],
  addons: ['@storybook/addon-docs'],
  framework: '@storybook/react-vite',
  core: { disableTelemetry: true },
  viteFinal: async (config) => ({ ...config, plugins: [...(config.plugins ?? []), tailwindcss()] }),
};
export default config;
