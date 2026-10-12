import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  base: './',
  plugins: [vue()],
  build: {
    outDir: fileURLToPath(new URL('../../src/calibrate3dp/assets/gcode_viewer', import.meta.url)),
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 700,
    target: 'es2022',
  },
});
