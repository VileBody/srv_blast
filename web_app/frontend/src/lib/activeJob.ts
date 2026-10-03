import { queryOptions } from '@tanstack/react-query';
import { api } from './api';

/**
 * Идёт ли сейчас батч — общий кеш шапки (AppShell) и кружка лимитов.
 *
 * Опрашивает ТОЛЬКО шапка (`activeJobPollingOptions`): в react-query у каждого
 * наблюдателя свой таймер refetchInterval, и запросы схлопываются лишь пока один
 * уже в полёте. Остальные читают кеш: staleTime не даёт каждому новому монтированию
 * (кружок лимитов, перемонтирование страницы) слать свой запрос — на странице
 * проекта /api/jobs/active уходил пачкой по ~10 раз подряд.
 */
export const activeJobOptions = queryOptions({
  queryKey: ['active-job'],
  queryFn: api.activeJob,
  staleTime: 4_000
});

/** Адаптивный опрос: батч идёт — каждые 5 с, нет — раз в 30 с. Подключать в одном месте. */
export const activeJobPollingOptions = queryOptions({
  ...activeJobOptions,
  refetchInterval: (query) => (query.state.data?.job ? 5000 : 30_000)
});
