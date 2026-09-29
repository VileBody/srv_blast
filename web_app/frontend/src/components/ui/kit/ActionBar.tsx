import type { ReactNode } from 'react';
import { cn } from '../../../lib/cn';

/*
 * Строка действий (UI_RULES.md → «Строка действий»): одна на весь сайт. Слева контекст —
 * итог, статус, галочка прав; справа действия, главное последним. Её используют шаги
 * визарда, выкладка в TikTok и модалки.
 *
 * На телефоне складывается в столбец (контекст сверху, кнопки на всю ширину) и, если
 * `sticky`, прилипает к низу экрана.
 */
export function ActionBar({
  start,
  children,
  sticky = false,
  bordered = false,
  className
}: {
  start?: ReactNode;
  /** кнопки; главная — последней */
  children: ReactNode;
  sticky?: boolean;
  /** волосяная линия сверху — когда строка стоит под прокручиваемым содержимым */
  bordered?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'flex items-center gap-[12px] max-md:flex-col max-md:items-stretch',
        bordered && 'border-t border-line pt-[12px]',
        sticky && 'max-md:sticky max-md:bottom-0 max-md:z-sticky max-md:bg-card max-md:pb-[calc(12px+env(safe-area-inset-bottom,0px))]',
        className
      )}
    >
      {start && <div className="flex min-w-0 flex-1 flex-wrap items-center gap-[8px] text-ui-14 text-text-60">{start}</div>}
      <div className={cn('flex shrink-0 items-center gap-[10px] max-md:w-full', !start && 'w-full')}>{children}</div>
    </div>
  );
}
