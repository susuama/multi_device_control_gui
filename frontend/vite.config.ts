import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/

export default defineConfig({
    plugins: [react()],

    // npm run dev で起動するサーバー
    server: {
        host: "127.0.0.1",
        port: 5173,
        strictPort: true,
        
        // 特定のURLへのアクセスを別のサーバーへつなげる
        proxy: {
            "/api": "http://127.0.0.1:8000",
            "/ws": {
                target: "ws://127.0.0.1:8000",
                ws: true,
            },
        },
    },
});