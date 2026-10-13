import { defineConfig } from 'vite';
import { resolve } from 'node:path';

export default defineConfig({
  build: {
    outDir: resolve(import.meta.dirname, '../app/web/static/market'),
    emptyOutDir: true,
    cssCodeSplit: false,
    lib: { entry: resolve(import.meta.dirname, 'chart.js'), formats: ['es'], fileName: () => 'market-terminal.js' },
    rollupOptions: { output: { assetFileNames: 'market-terminal.[ext]' } },
  },
});
