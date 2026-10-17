import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// デザイン確認用の独立した入口。通常のアプリのビルド設定は変更しない。
export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5175, strictPort: true },
  build: {
    outDir: 'dist-design-demo',
    rollupOptions: { input: 'design-demo.html' },
  },
});
