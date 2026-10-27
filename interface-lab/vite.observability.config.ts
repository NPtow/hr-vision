import { defineConfig } from 'vite';
export default defineConfig({
  define: {__HR_RELEASE__: JSON.stringify(process.env.GITHUB_SHA ?? process.env.HR_RELEASE ?? 'development')},
  build: {
    outDir: 'storybook-static', emptyOutDir: false,
    lib: {entry: 'src/observability/browser.ts', name: 'MashaDashaObservability', formats: ['iife'], fileName: () => 'observability.js'},
    sourcemap: false,
  },
});
