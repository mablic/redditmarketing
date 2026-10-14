import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  root: 'ui', plugins: [react()],
  server: {host: '127.0.0.1', port: 5173, strictPort: true,
    proxy: Object.fromEntries(['/api', '/media'].map(path => [path, {target: 'http://127.0.0.1:8765', changeOrigin: true}]))},
  build: {outDir: '../dist', emptyOutDir: true}
});
