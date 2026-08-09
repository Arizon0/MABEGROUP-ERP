import path from 'node:path'
import { defineConfig } from 'vitest/config'

// Configuração separada porque o Vitest empacota a própria cópia do Vite, e
// misturar as duas no mesmo arquivo gera conflito de tipos entre os dois pacotes.
export default defineConfig({
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
