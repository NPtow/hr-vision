import type { StorybookConfig } from '@storybook/react-vite';
import { fileURLToPath } from 'node:url';
const config: StorybookConfig = {
  stories: ['../src/lab/*.stories.tsx'],
  staticDirs: ['../public'],
  addons: [],
  framework: { name:'@storybook/react-vite', options:{builder:{viteConfigPath:fileURLToPath(new URL('./vite.config.ts',import.meta.url))}} },
  core: { disableTelemetry: true },
  viteFinal: async config => ({...config, base:'./'}),
};
export default config;
