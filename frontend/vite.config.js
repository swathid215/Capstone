import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import os from 'node:os'

const viteCacheDir = process.env.LOCALAPPDATA
    ? path.join(process.env.LOCALAPPDATA, 'traffic-app-vite')
    : path.join(os.tmpdir(), 'traffic-app-vite')

// https://vitejs.dev/config/
export default defineConfig({
    cacheDir: viteCacheDir,
    plugins: [react()],
    server: {
        proxy: {
            '/api': {
                target: 'http://127.0.0.1:8000',
                changeOrigin: true,
            }
        }
    }
})
