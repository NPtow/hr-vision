import { defineConfig } from 'vite';

export default defineConfig({
  base: '/candidate-preview/',
  build: { outDir: 'dist', emptyOutDir: true },
  server: { proxy: { '/candidate-preview/api': 'http://127.0.0.1:8397', '/api/hr/session': 'http://127.0.0.1:8397' } },
});
