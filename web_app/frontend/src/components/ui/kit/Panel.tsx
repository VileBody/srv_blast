import { forwardRef, useRef, useState, type DragEvent, type HTMLAttributes, type ReactNode } from 'react';
import { cn } from '../../../lib/cn';
import { GLYPH, Icon } from './Icon';

/*
 * Поверхности (UI_RULES.md → «Панели и пунктир»): карточка страницы (card, r25) →
 * панель внутри карточки (panel, r15, заливка и волосяная рамка) → поле (field, r10).
 * Пунктир только у пустого места, куда можно что-то положить или создать (DropZone).
 * Заполненное всегда сплошное.
 */
export const Surface = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement> & { level?: 'card' | 'panel' | 'field' }>(function Surface(
  { level = 'panel', className, ...props },
  ref
) {
  return (
    <div
      ref={ref}
      className={cn(
        level === 'card' && 'rounded-r25 border border-line bg-card',
        level === 'panel' && 'rounded-r15 border border-line bg-panel',
        level === 'field' && 'rounded-r10 bg-field',
        className
      )}
      {...props}
    />
  );
});

/**
 * Пустое место: загрузка файла или создание нового. Один стиль на весь сайт — пунктир
 * accent-line, мягкая акцентная заливка, белый квадрат с плюсом.
 */
export function DropZone({
  title,
  hint,
  accept,
  multiple = false,
  busy = false,
  invalid = false,
  onFiles,
  onClick,
  icon,
  className
}: {
  title: ReactNode;
  hint?: ReactNode;
  /** если задан — зона принимает файлы (клик открывает выбор, можно перетащить) */
  accept?: string;
  multiple?: boolean;
  busy?: boolean;
  /** подсветить как пропуск (форма нажала «дальше» без файла) */
  invalid?: boolean;
  onFiles?: (files: File[]) => void;
  /** без файлов — просто действие «создать» */
  onClick?: () => void;
  icon?: ReactNode;
  className?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const takesFiles = Boolean(onFiles);
  const drop = (event: DragEvent) => {
    event.preventDefault();
    setOver(false);
    const files = Array.from(event.dataTransfer.files ?? []);
    if (files.length && onFiles) onFiles(multiple ? files : files.slice(0, 1));
  };
  return (
    <>
      {takesFiles && (
        <input
          ref={input}
          type="file"
          className="sr-only"
          accept={accept}
          multiple={multiple}
          tabIndex={-1}
          onChange={(event) => { const files = Array.from(event.target.files ?? []); if (files.length) onFiles?.(files); event.target.value = ''; }}
        />
      )}
      <button
        type="button"
        disabled={busy}
        aria-busy={busy || undefined}
        onClick={() => (takesFiles ? input.current?.click() : onClick?.())}
        onDragOver={takesFiles ? (event) => { event.preventDefault(); setOver(true); } : undefined}
        onDragLeave={takesFiles ? () => setOver(false) : undefined}
        onDrop={takesFiles ? drop : undefined}
        className={cn(
          'flex w-full items-center gap-[16px] rounded-r15 border-[1.5px] border-dashed px-[20px] py-[16px] text-left transition-[background-color,border-color] duration-150 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-light disabled:cursor-progress',
          invalid ? 'border-warning bg-warning-bg' : 'border-accent-line bg-accent-soft hover:bg-field-hover',
          over && 'bg-field-hover',
          className
        )}
      >
        <span className="grid h-ctl w-ctl shrink-0 place-items-center rounded-r10 bg-text text-ui-20 text-accent-strong">
          {busy ? <span className="spinner" aria-hidden="true" /> : icon ?? <Icon>{GLYPH.plus}</Icon>}
        </span>
        <span className="flex min-w-0 flex-col">
          <span className="text-ui-16 text-text">{title}</span>
          {hint && <span className="text-ui-14 text-text-60">{hint}</span>}
        </span>
      </button>
    </>
  );
}
