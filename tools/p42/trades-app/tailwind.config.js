// Цвета — переменные страницы (tk593_template.html: :root и html.dark), своих у приложения нет: тема переключается на странице.
// Стили ограничены корнем #trades-root, preflight выключен — остальная страница не затрагивается.
export default {
  darkMode: 'class',
  important: '#trades-root',
  content: ['./src/**/*.{ts,tsx}'],
  corePlugins: {preflight: false},
  theme: {
    extend: {
      screens: {mob: {max: '640px'}},
      colors: {
        background: 'var(--bg)', foreground: 'var(--fg)',
        card: {DEFAULT: 'var(--card)', foreground: 'var(--fg)'},
        popover: {DEFAULT: 'var(--card)', foreground: 'var(--fg)'},
        muted: {DEFAULT: 'var(--muted)', foreground: 'var(--mfg)'},
        accent: {DEFAULT: 'var(--hover)', foreground: 'var(--fg)'},
        primary: {DEFAULT: 'var(--fg)', foreground: 'var(--onfg)'},
        secondary: {DEFAULT: 'var(--muted)', foreground: 'var(--fg)'},
        border: 'var(--border)', input: 'var(--border)', ring: 'var(--accent)',
        brand: 'var(--accent)', hi: 'var(--hi)',
      },
      borderRadius: {lg: 'var(--r)', md: 'calc(var(--r) - 4px)', sm: 'calc(var(--r) - 8px)'},
    },
  },
  plugins: [],
};
