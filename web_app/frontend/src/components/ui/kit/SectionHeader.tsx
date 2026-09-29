import type { ReactNode } from 'react';
import { cn } from '../../../lib/cn';

/*
 * Заголовок секции (UI_RULES.md → «Заголовки секций»): заголовок слева, справа мета 14
 * и малые действия. Без двоеточий в тексте. «Смотреть всё» — действием справа
 * (текстовая кнопка со стрелкой), а не стрелкой внутри заголовка. «Назад» — в шапке страницы.
 *
 * level: page — 32 (заголовок страницы), card — 24 (карточка, секция).
 */
export function SectionHeader({
  title,
  icon,
  meta,
  actions,
  level = 'card',
  as: Tag = 'h2',
  className
}: {
  title: ReactNode;
  /** <Icon> из kit — размер в em сам подстроится под кегль */
  icon?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  level?: 'page' | 'card';
  as?: 'h1' | 'h2' | 'h3';
  className?: string;
}) {
  return (
    <div className={cn('flex min-w-0 flex-wrap items-center justify-between gap-x-[16px] gap-y-[8px]', className)}>
      <Tag className={cn('flex min-w-0 items-center gap-[0.45em] font-[400] text-text', level === 'page' ? 'text-ui-32' : 'text-ui-24')}>
        {icon}
        <span className="min-w-0 truncate">{title}</span>
      </Tag>
      {(meta || actions) && (
        <div className="flex min-w-0 flex-wrap items-center gap-[12px] text-ui-14 text-text-60">
          {meta}
          {actions}
        </div>
      )}
    </div>
  );
}
