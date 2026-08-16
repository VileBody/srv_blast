import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        nav: 'var(--nav-bg)',
        card: 'var(--card-bg)',
        'card-2': 'var(--card-2)',
        text: 'var(--text)',
        'text-80': 'var(--text-80)',
        'text-60': 'var(--text-60)',
        'text-40': 'var(--text-40)',
        'text-20': 'var(--text-20)',
        accent: 'var(--accent)',
        'accent-80': 'var(--accent-80)',
        'accent-20': 'var(--accent-20)',
        'accent-10': 'var(--accent-10)',
        'accent-light': 'var(--accent-light)',
        border: 'var(--border)',
        success: 'var(--success)',
        error: 'var(--error)',
        warning: 'var(--warning)',
        info: 'var(--info)'
      },
      spacing: {
        'space-1': 'var(--space-1)',
        'space-2': 'var(--space-2)',
        'space-3': 'var(--space-3)',
        'space-4': 'var(--space-4)',
        'space-5': 'var(--space-5)',
        'space-6': 'var(--space-6)',
        'space-7': 'var(--space-7)',
        'space-8': 'var(--space-8)'
      },
      borderRadius: {
        r40: 'var(--r40)',
        r25: 'var(--r25)',
        r20: 'var(--r20)',
        r15: 'var(--r15)',
        r12: 'var(--r12)',
        r10: 'var(--r10)',
        r9: 'var(--r9)'
      },
      backgroundImage: {
        'grad-main': 'var(--grad-main)',
        'grad-btn': 'var(--grad-btn)',
        'grad-card': 'var(--grad-card)',
        'grad-soft-10': 'var(--grad-soft-10)',
        'grad-soft-20': 'var(--grad-soft-20)',
        'grad-text': 'var(--grad-text)'
      },
      fontFamily: {
        point: ['Point', '-apple-system', 'BlinkMacSystemFont', 'sans-serif']
      },
      zIndex: {
        sticky: 'var(--z-sticky)',
        sidebar: 'var(--z-sidebar)',
        drawer: 'var(--z-drawer)',
        overlay: 'var(--z-overlay)',
        modal: 'var(--z-modal)',
        toast: 'var(--z-toast)'
      },
      boxShadow: {
        glow: '0 0 24px var(--accent-20)',
        soft: '0 20px 80px rgba(0,0,0,.35)'
      }
    }
  },
  plugins: []
} satisfies Config;
