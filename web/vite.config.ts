import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In dev, point the console at a live backend:
//   VITE_API_BASE=http://127.0.0.1:8765 npm run dev
// In production the FastAPI app serves web/dist at / (same origin).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
  server: {
    port: 5173,
  },
})
