import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { cn } from '../../../lib/cn';

/*
 * Метка и пилюля (UI_RULES.md → «Метки и пилюли»).
 * Tag только показывает данные: 24 px, r6, без рамки, не реагирует на наведение.
 * Pill выбирается: 32 px, круглая; выбранная — мягкая акцентная заливка и рамка.
 */
export type TagTone = 'default' | 'accent' | 'ok' | 'warn' | 'error';

const TAG_TONE: Record<TagTone, string> = {
  default: 'bg-field text-text-80',
  accent: 'bg-accent-soft text-text',
  ok: 'bg-success-bg text-success',
  warn: 'bg-warning-bg text-warning',
  error: 'bg-error-bg text-error'
};

export function Tag({ tone = 'default', icon, children, className, title }: { tone?: TagTone; icon?: ReactNode; children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={cn('inline-flex h-ctl-xs max-w-full shrink-0 items-center gap-[0.4em] whitespace-nowrap rounded-r6 px-[8px] text-ui-12', TAG_TONE[tone], className)}>
      {icon}
      <span className="truncate">{children}</span>
    </span>
  );
}

export function Pill({
  pressed = false,
  count,
  icon,
  children,
  className,
  type = 'button',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  pressed?: boolean;
  /** счётчик слева (сколько выбрано в разделе) */
  count?: number;
  icon?: ReactNode;
}) {
  return (
    <button
      type={type}
      aria-pressed={pressed}
      className={cn(
        'inline-flex h-ctl-sm shrink-0 items-center gap-[8px] whitespace-nowrap rounded-full border px-[14px] text-ui-14 transition-[background-color,border-color,color,transform] duration-150 active:scale-[.97] disabled:pointer-events-none disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-light',
        count !== undefined && 'pl-[6px]',
        pressed ? 'border-accent-line bg-accent-soft text-text' : 'border-line bg-field text-text-80 hover:text-text',
        className
      )}
      {...props}
    >
      {count !== undefined && <span className="grid h-[20px] min-w-[20px] place-items-center rounded-r6 bg-field-hover px-[5px] text-ui-12 tabular-nums">{count}</span>}
      {icon}
      <span className="truncate">{children}</span>
    </button>
  );
}
