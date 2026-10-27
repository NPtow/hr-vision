import type { StorybookConfig } from '@storybook/react-vite';
const config: StorybookConfig = {
  stories: ['../src/**/*.stories.tsx'],
  staticDirs: ['../public'],
  addons: ['@storybook/addon-docs'],
  framework: '@storybook/react-vite',
  core: { disableTelemetry: true },
  viteFinal: async config => ({ ...config, resolve: { ...config.resolve, dedupe: ['react', 'react-dom', 'motion', 'lucide-react'] } }),
};
export default config;
