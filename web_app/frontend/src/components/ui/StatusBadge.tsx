import { cn } from '../../lib/cn';

type BadgeVariant = 'success' | 'info' | 'neutral' | 'error' | 'warning';

const map: Record<BadgeVariant, string> = {
  success: 'bg-[var(--success-bg)] text-success',
  info: 'bg-[var(--info-bg)] text-info',
  neutral: 'bg-[var(--neutral-bg)] text-text-40',
  error: 'bg-[var(--error-bg)] text-error',
  warning: 'bg-[var(--warning-bg)] text-warning'
};

export function StatusBadge({ variant = 'neutral', children, className }: { variant?: BadgeVariant; children: React.ReactNode; className?: string }) {
  return <span className={cn('status-badge', map[variant], className)}>{children}</span>;
}

export function statusVariant(status?: string): BadgeVariant {
  if (status === 'ACTIVE' || status === 'COMPLETED' || status === 'Готово' || status === 'Опубликовано') return 'success';
  if (status === 'IN_PROGRESS' || status === 'PROCESSING' || status === 'PENDING') return 'info';
  if (status === 'FAILED') return 'error';
  return 'neutral';
}

export function statusLabel(status?: string): string {
  switch (status) {
    // «Новый», а не «Активный»: ACTIVE — это стадия «проект создан, генераций ещё не было».
    // «Активный» путался с «текущим» (тот, в который идёт генерация по умолчанию) — это
    // разные вещи, а выглядели одинаково.
    case 'ACTIVE': return 'Новый';
    case 'IN_PROGRESS': return 'В процессе';
    case 'COMPLETED': return 'Готово';
    case 'PENDING': return 'В очереди';
    case 'PROCESSING': return 'Генерируется';
    case 'FAILED': return 'Ошибка';
    default: return status ?? '—';
  }
}
