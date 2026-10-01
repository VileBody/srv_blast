import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        nav: 'var(--nav-bg)',
        card: 'var(--card)',
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
        // Единая шкала (docs: UI_RULES.md): поверхности и акцент для новых компонентов
        panel: 'var(--panel)',
        field: 'var(--field)',
        'field-hover': 'var(--field-hover)',
        'accent-strong': 'var(--accent-strong)',
        'accent-soft': 'var(--accent-soft)',
        'accent-line': 'var(--accent-line)',
        line: 'var(--line)',
        'line-strong': 'var(--line-strong)',
        scrim: 'var(--scrim)',
        'success-bg': 'var(--success-bg)',
        'warning-bg': 'var(--warning-bg)',
        'error-bg': 'var(--error-bg)',
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
        'space-8': 'var(--space-8)',
        // Высоты контролов (UI_RULES.md): метка, малый, обычный, крупный на телефоне, крупный
        'ctl-xs': 'var(--ctl-xs)',
        'ctl-sm': 'var(--ctl-sm)',
        ctl: 'var(--ctl)',
        'ctl-touch': 'var(--ctl-touch)',
        'ctl-lg': 'var(--ctl-lg)'
      },
      /* Шкала кеглей (UI_RULES.md): шесть ступеней, высота строки задаётся вместе с кеглем */
      fontSize: {
        'ui-12': ['12px', { lineHeight: '16px' }],
        'ui-14': ['14px', { lineHeight: '20px' }],
        'ui-16': ['16px', { lineHeight: '24px' }],
        'ui-20': ['20px', { lineHeight: '28px' }],
        'ui-24': ['24px', { lineHeight: '32px' }],
        'ui-32': ['32px', { lineHeight: '40px' }]
      },
      borderRadius: {
        // Шкала: r6, r10, r15, r25 и rounded-full. r9, r12, r20, r40 — наследие, ui:check считает их долгом
        r6: 'var(--r6)',
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
        guidance: 'var(--z-guidance)',
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
