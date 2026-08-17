import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const apiPort = process.env.KPIR_API_PORT ?? '8756';
const frontendPort = Number(process.env.KPIR_FRONTEND_PORT ?? 5173);

// The browser never talks to the backend directly: it calls same-origin
// relative URLs and Vite proxies them. That keeps CSP `connect-src 'self'`
// valid in development and identical to the packaged same-origin build.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    host: '0.0.0.0',
    port: frontendPort,
    strictPort: true,
    // Allow the sandboxed preview hostname as well as local development.
    allowedHosts: true,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${apiPort}`,
        changeOrigin: false,
        ws: false,
      },
    },
  },
  build: { outDir: 'dist', sourcemap: false, target: 'es2022' },
});
