/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: ['class', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        // Superfícies e tinta seguem tokens, nunca cor de série — texto jamais
        // recebe a cor do dado (ver skill de visualização).
        surface: {
          DEFAULT: 'var(--surface-1)',
          raised: 'var(--surface-2)',
          sunken: 'var(--surface-0)',
        },
        ink: {
          DEFAULT: 'var(--text-primary)',
          soft: 'var(--text-secondary)',
          muted: 'var(--text-muted)',
        },
        line: 'var(--border)',
        brand: {
          DEFAULT: 'var(--series-1)',
          soft: 'var(--brand-soft)',
          line: 'var(--brand-line)',
        },
        // Cores de estado são reservadas: nunca reaproveitadas como "série 4".
        good: {
          DEFAULT: 'var(--status-good)',
          soft: 'var(--good-soft)',
          line: 'var(--good-line)',
        },
        warn: {
          DEFAULT: 'var(--status-warning)',
          soft: 'var(--warn-soft)',
          line: 'var(--warn-line)',
        },
        bad: {
          DEFAULT: 'var(--status-critical)',
          soft: 'var(--bad-soft)',
          line: 'var(--bad-line)',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
