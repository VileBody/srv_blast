import { useRef, type KeyboardEvent, type ReactNode } from 'react';
import { cn } from '../../../lib/cn';
import { GLYPH, Icon } from './Icon';

/*
 * Табы и переключатели (UI_RULES.md → «Табы и переключатели»): этапы визарда, батчи,
 * итерации, режимы фона, фильтры внутри панели — один компонент в двух размерах.
 *
 * md — подложка panel, активный залит accent-strong. sm — для фильтров внутри панели:
 * активный на field-hover с тонкой рамкой, чтобы не спорить с главным действием.
 * «Добавить» (батч, итерация) — последним сегментом, а не отдельной кнопкой рядом.
 * Стрелки ← → переводят выбор; фокус один на группу (roving tabindex).
 */
export type SegmentedItem<T extends string> = {
  value: T;
  label: ReactNode;
  icon?: ReactNode;
  /** пройденный шаг — зелёная галочка вместо иконки */
  done?: boolean;
  disabled?: boolean;
  title?: string;
};

export function Segmented<T extends string>({
  items,
  value,
  onChange,
  size = 'md',
  semantics = 'radio',
  fill = false,
  onAdd,
  addLabel,
  ariaLabel,
  className
}: {
  items: SegmentedItem<T>[];
  value: T | null;
  onChange: (value: T) => void;
  size?: 'md' | 'sm';
  /** tabs — переключает разделы (tablist), radio — выбор значения (radiogroup) */
  semantics?: 'tabs' | 'radio';
  /** растянуть на всю ширину равными колонками */
  fill?: boolean;
  onAdd?: () => void;
  /** подпись кнопки «добавить» для скринридера — обязательна вместе с onAdd */
  addLabel?: string;
  ariaLabel: string;
  className?: string;
}) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const enabled = items.filter((item) => !item.disabled);
  const focusValue = value !== null && enabled.some((item) => item.value === value) ? value : enabled[0]?.value;

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = ({ ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 } as Record<string, number>)[event.key];
    if (step === undefined || !enabled.length) return;
    event.preventDefault();
    const at = Math.max(0, enabled.findIndex((item) => item.value === focusValue));
    const next = enabled[(at + step + enabled.length) % enabled.length];
    onChange(next.value);
    refs.current[items.indexOf(next)]?.focus();
  };

  const tabs = semantics === 'tabs';
  return (
    <div
      role={tabs ? 'tablist' : 'radiogroup'}
      aria-label={ariaLabel}
      onKeyDown={onKeyDown}
      className={cn(
        'grid-flow-col gap-[4px] bg-panel',
        fill ? 'grid w-full auto-cols-fr' : 'inline-grid auto-cols-auto',
        size === 'md' ? 'h-ctl rounded-r15 p-[4px]' : 'h-ctl-sm rounded-r10 p-[3px]',
        className
      )}
    >
      {items.map((item, index) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            ref={(el) => { refs.current[index] = el; }}
            type="button"
            role={tabs ? 'tab' : 'radio'}
            aria-selected={tabs ? active : undefined}
            aria-checked={tabs ? undefined : active}
            tabIndex={item.value === focusValue ? 0 : -1}
            disabled={item.disabled}
            title={item.title}
            onClick={() => onChange(item.value)}
            className={cn(
              'inline-flex h-full min-w-0 items-center justify-center gap-[0.4em] whitespace-nowrap transition-[background-color,color] duration-150 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-light',
              size === 'md' ? 'rounded-r10 px-[14px] text-ui-16' : 'rounded-r6 px-[10px] text-ui-14',
              active
                ? size === 'md' ? 'bg-accent-strong text-text' : 'bg-field-hover text-text shadow-[inset_0_0_0_1px_var(--line-strong)]'
                : 'text-text-60 enabled:hover:bg-field enabled:hover:text-text'
            )}
          >
            {item.done
              ? <span className="grid h-[0.9em] w-[0.9em] place-items-center rounded-full bg-success-bg text-success"><Icon className="!h-[0.62em] !w-[0.62em]">{GLYPH.check}</Icon></span>
              : item.icon}
            <span className="truncate">{item.label}</span>
          </button>
        );
      })}
      {onAdd && (
        <button
          type="button"
          onClick={onAdd}
          aria-label={addLabel}
          className={cn(
            'inline-flex h-full items-center justify-center text-text-40 transition-colors hover:bg-field hover:text-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-light',
            size === 'md' ? 'aspect-square rounded-r10 text-ui-16' : 'aspect-square rounded-r6 text-ui-14'
          )}
        >
          <Icon>{GLYPH.plus}</Icon>
        </button>
      )}
    </div>
  );
}
