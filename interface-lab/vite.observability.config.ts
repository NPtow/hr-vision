import { defineConfig } from 'vite';
import { collectStaticCopy } from './scripts/replay-static-copy.mjs';
export default defineConfig({
  define: {
    __HR_RELEASE__: JSON.stringify(process.env.GITHUB_SHA ?? process.env.HR_RELEASE ?? 'development'),
    __HR_REPLAY_STATIC_TEXT__: JSON.stringify(collectStaticCopy(['src/AgencyProduct.stories.tsx','corporate-demo/src/main.tsx'])),
  },
  build: {
    outDir: 'storybook-static', emptyOutDir: false,
    lib: {entry: 'src/observability/browser.ts', name: 'MashaDashaObservability', formats: ['iife'], fileName: () => 'observability.js'},
    sourcemap: false,
  },
});
