import { useTranslation } from 'react-i18next';
import { setLanguage, type Lang } from '../../i18n';
import { cn } from '../../lib/cn';

const LANGS: Lang[] = ['ru', 'en'];

/** Компактный переключатель RU/EN (каркас локализации). */
export function LanguageSwitcher({ className }: { className?: string }) {
  const { i18n } = useTranslation();
  const current = (i18n.language?.slice(0, 2) as Lang) || 'ru';
  return (
    <div
      className={cn('flex items-center gap-[2px] rounded-[10px] p-[3px] text-[13px]', className)}
      style={{ background: 'var(--grad-soft-10)' }}
      role="group"
      aria-label="Language"
    >
      {LANGS.map((lng) => (
        <button
          key={lng}
          type="button"
          onClick={() => setLanguage(lng)}
          aria-pressed={current === lng}
          className={cn(
            'rounded-[8px] px-[8px] py-[2px] uppercase transition',
            current === lng ? 'text-text' : 'text-text-60 hover:text-text-80'
          )}
          style={current === lng ? { background: 'var(--grad-soft-20)', boxShadow: 'inset 0 0 0 1px var(--accent-light)' } : undefined}
        >
          <span className="block translate-y-px">{lng}</span>
        </button>
      ))}
    </div>
  );
}
