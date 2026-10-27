import type { StorybookConfig } from '@storybook/react-vite';

const config: StorybookConfig = {
  stories: ['../src/*.stories.tsx'],
  staticDirs: ['../public'],
  framework: '@storybook/react-vite',
  core: { disableTelemetry: true },
  viteFinal: async config => ({
    ...config,
    resolve: { ...config.resolve, dedupe: ['react', 'react-dom', 'motion', 'lucide-react'] },
  }),
};

export default config;
