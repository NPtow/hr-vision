import type { StorybookConfig } from '@storybook/react-vite';
import tailwindcss from '@tailwindcss/vite';
import buildTarget from '../build-target.json' with { type: 'json' };
const target = process.env.HR_BUILD_TARGET ?? buildTarget.target;
if (!['service', 'mockups', 'all'].includes(target)) throw new Error(`Unknown build target: ${target}`);
const config: StorybookConfig = {
  stories: target === 'service' ? ['../src/AgencyProduct.stories.tsx']
    : target === 'mockups' ? ['../src/**/!(AgencyProduct).stories.tsx'] : ['../src/**/*.stories.tsx'],
  staticDirs: target === 'service' ? [] : ['../public'],
  addons: ['@storybook/addon-docs'],
  framework: '@storybook/react-vite',
  core: { disableTelemetry: true },
  viteFinal: async (config) => ({ ...config, plugins: [...(config.plugins ?? []), tailwindcss()] }),
};
export default config;
