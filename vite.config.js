import { defineConfig } from 'vite';
import { resolve } from 'node:path';

export default defineConfig({
  root: 'site',
  build: {
    outDir: '../dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        index: resolve(import.meta.dirname, 'site/index.html'),
        methodology: resolve(import.meta.dirname, 'site/methodology.html'),
      },
    },
  },
});
