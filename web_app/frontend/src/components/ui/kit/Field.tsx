import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from 'react';
import { cn } from '../../../lib/cn';
import { GLYPH, Icon } from './Icon';

/*
 * Поля, галочки, тумблеры (UI_RULES.md → «Поля, галочки, тумблеры»).
 * Поле 44 px, r10, заливка field; рамка только в фокусе и при ошибке. Ошибка — текстом
 * рядом с полем, а не тостом. Галочка 20 px, r6. Тумблер один размер на сайт: 40 × 24.
 */
export const TextField = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
}>(function TextField({ label, hint, error, id, className, ...props }, ref) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const noteId = `${inputId}-note`;
  return (
    <div className={cn('flex min-w-0 flex-col gap-[6px]', className)}>
      {label && <label htmlFor={inputId} className="text-ui-14 text-text-60">{label}</label>}
      <input
        ref={ref}
        id={inputId}
        aria-invalid={error ? true : undefined}
        aria-describedby={error || hint ? noteId : undefined}
        className={cn(
          'h-ctl w-full rounded-r10 border bg-field px-[12px] text-ui-16 text-text caret-accent-light outline-none transition-[border-color] duration-150 placeholder:text-text-40 focus:border-accent-line disabled:opacity-50',
          error ? 'border-warning' : 'border-transparent'
        )}
        {...props}
      />
      {(error || hint) && <span id={noteId} className={cn('text-ui-12', error ? 'text-warning' : 'text-text-40')}>{error || hint}</span>}
    </div>
  );
});

export function Checkbox({
  checked,
  onChange,
  label,
  hint,
  invalid = false,
  disabled = false,
  className
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: ReactNode;
  hint?: ReactNode;
  /** подсветить как пропуск */
  invalid?: boolean;
  disabled?: boolean;
  className?: string;
}) {
  const id = useId();
  return (
    <span className={cn('inline-flex items-start gap-[10px] text-ui-14', invalid ? 'text-warning' : 'text-text-80', disabled && 'opacity-45', className)}>
      <button
        type="button"
        role="checkbox"
        aria-checked={checked}
        aria-labelledby={label ? id : undefined}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          'grid h-[20px] w-[20px] shrink-0 place-items-center rounded-r6 border-[1.5px] text-ui-16 transition-[background-color,border-color] duration-150 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-light',
          checked ? 'border-accent-light bg-accent-light text-bg' : invalid ? 'border-warning' : 'border-line-strong'
        )}
      >
        <Icon className={cn('transition-opacity duration-100', checked ? 'opacity-100' : 'opacity-0')} style={{ strokeWidth: 2.6 }}>{GLYPH.check}</Icon>
      </button>
      {label && (
        <span id={id} className="flex min-w-0 flex-col" onClick={() => !disabled && onChange(!checked)}>
          <span>{label}</span>
          {hint && <span className="text-ui-12 text-text-40">{hint}</span>}
        </span>
      )}
    </span>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  labelledBy,
  disabled = false,
  className
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  /** aria-label, если видимой подписи рядом нет */
  label?: string;
  labelledBy?: string;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={labelledBy ? undefined : label}
      aria-labelledby={labelledBy}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        'relative h-[24px] w-[40px] shrink-0 rounded-full transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-45 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-light',
        checked ? 'bg-accent-light' : 'bg-line-strong',
        className
      )}
    >
      <span
        aria-hidden="true"
        className={cn('absolute left-[3px] top-[3px] h-[18px] w-[18px] rounded-full bg-text shadow-[0_1px_3px_var(--scrim)] transition-transform duration-200 ease-out', checked && 'translate-x-[16px]')}
      />
    </button>
  );
}
