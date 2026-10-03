import { useQuery } from '@tanstack/react-query';
import { api } from './api';
import type { VideoVersion } from './types';

/*
 * Категории падения ролика — их проставляет бэк (`failureKind`, app/job_errors.py).
 * Сырой текст ошибки (трейсбек, пути рендер-ноды) приходит только админам.
 * Незнакомый код (бэк новее фронта) показываем как «unknown», а не пустотой.
 */
const KINDS = new Set([
  'timeout', 'busy', 'lost', 'render', 'footage', 'lyrics', 'source', 'color',
  'previous', 'cancelled', 'service', 'storyboard', 'drop', 'hook', 'build', 'unknown'
]);

/** i18n-ключ человеческой причины падения ролика. */
export function failureKey(video: Pick<VideoVersion, 'failureKind'> | undefined): string {
  const kind = video?.failureKind ?? 'unknown';
  return `processing.failure.${KINDS.has(kind) ? kind : 'unknown'}`;
}

/** Админ ли смотрит: тот же кеш ['me'], что у шапки, — без лишнего запроса. */
export function useIsAdmin(): boolean {
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: 15_000 });
  return Boolean(meQuery.data?.isAdmin);
}
