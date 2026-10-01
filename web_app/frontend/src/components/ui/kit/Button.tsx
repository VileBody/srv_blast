import { forwardRef, type AnchorHTMLAttributes, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { cn } from '../../../lib/cn';

/*
 * Кнопка (UI_RULES.md → «Кнопки»). Четыре вида: главная, вторичная, текстовая и иконка
 * (`iconOnly`, квадрат той же высоты, что соседние кнопки). Рамок у кнопок нет.
 *
 * Главная одна на область, её готовность показывает заливка: `ready={false}` — серая
 * (`field`), но нажимается — так форма может подсветить пропуски по клику, а не молчать.
 * Настоящий `disabled` — только там, где этого требует внешнее правило.
 *
 * Высота строки у каждого размера той же чётности, что высота кнопки (44/24, 32/20, 60/28,
 * 52/28): иначе Chromium округляет базовую линию на полпикселя и текст уезжает вверх.
 */
export type ButtonVariant = 'primary' | 'secondary' | 'ghost';
export type ButtonSize = 'sm' | 'md' | 'lg';

const BASE = 'relative inline-flex shrink-0 select-none items-center justify-center gap-[0.45em] whitespace-nowrap font-[400] transition-[background-color,color,transform,opacity] duration-150 ease-out active:scale-[.97] disabled:pointer-events-none disabled:opacity-45 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-light';

const SIZE: Record<ButtonSize, string> = {
  sm: 'h-ctl-sm rounded-r10 px-[12px] text-ui-14',
  md: 'h-ctl rounded-r10 px-[18px] text-ui-16',
  lg: 'h-ctl-lg rounded-r15 px-[24px] text-ui-20 max-md:h-ctl-touch'
};

const SQUARE: Record<ButtonSize, string> = {
  sm: 'w-ctl-sm px-0',
  md: 'w-ctl px-0',
  lg: 'w-ctl-lg px-0 max-md:w-ctl-touch'
};

function variantClass(variant: ButtonVariant, ready: boolean) {
  if (variant === 'primary') return ready ? 'bg-accent-strong text-text hover:brightness-110' : 'bg-field text-text-60 hover:text-text';
  if (variant === 'secondary') return 'bg-field text-text-80 hover:bg-field-hover hover:text-text';
  return 'bg-transparent text-text-60 hover:bg-panel hover:text-text';
}

type Common = {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** только для главной: готова ли форма к действию */
  ready?: boolean;
  /** квадратная кнопка с одной иконкой — обязательно передай aria-label */
  iconOnly?: boolean;
  /** иконка перед подписью */
  icon?: ReactNode;
  /** иконка после подписи (стрелка «дальше») */
  iconEnd?: ReactNode;
  loading?: boolean;
};

export type ButtonProps = Common & ButtonHTMLAttributes<HTMLButtonElement>;

export function buttonClass({ variant = 'secondary', size = 'md', ready = true, iconOnly = false }: Pick<Common, 'variant' | 'size' | 'ready' | 'iconOnly'>) {
  return cn(BASE, SIZE[size], iconOnly && SQUARE[size], variantClass(variant, ready));
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', ready = true, iconOnly = false, icon, iconEnd, loading = false, className, children, type = 'button', disabled, ...props },
  ref
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled}
      aria-busy={loading || undefined}
      className={cn(buttonClass({ variant, size, ready, iconOnly }), loading && 'cursor-progress', className)}
      {...props}
    >
      {loading ? <span className="spinner" aria-hidden="true" /> : icon}
      {children && <span className={cn(loading && 'opacity-70')}>{children}</span>}
      {!loading && iconEnd}
    </button>
  );
});

/** Ссылка с видом кнопки — для переходов (href), чтобы не вешать navigate на onClick */
export function ButtonLink({
  variant = 'secondary',
  size = 'md',
  ready = true,
  iconOnly = false,
  icon,
  iconEnd,
  className,
  children,
  ...props
}: Omit<Common, 'loading'> & AnchorHTMLAttributes<HTMLAnchorElement>) {
  return (
    <a className={cn(buttonClass({ variant, size, ready, iconOnly }), className)} {...props}>
      {icon}
      {children && <span>{children}</span>}
      {iconEnd}
    </a>
  );
}
