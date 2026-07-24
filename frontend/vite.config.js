import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
var srcPath = decodeURIComponent(new URL('./src', import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1');
export default defineConfig({
    plugins: [react()],
    resolve: {
        alias: {
            '@': srcPath,
        },
    },
    server: {
        host: true,
        port: 5173,
        proxy: {
            '/api': {
                target: 'http://127.0.0.1:8000',
                changeOrigin: true,
            },
        },
    },
    preview: {
        host: true,
        port: 5174,
    },
});
