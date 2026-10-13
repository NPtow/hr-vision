import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  base: './',
  build: { outDir: 'dist', assetsDir: 'site-assets', emptyOutDir: true, target: 'es2022', rolldownOptions: { input: { site: fileURLToPath(new URL('./index.html',import.meta.url)), lab:fileURLToPath(new URL('./lab.html',import.meta.url)) } } },
});
