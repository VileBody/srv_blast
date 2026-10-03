import { queryOptions } from '@tanstack/react-query';
import { api } from './api';

/**
 * Идёт ли сейчас батч — один запрос на шапку (AppShell) и кружок лимитов. Опрос
 * адаптивный: батч идёт — каждые 5 с, нет — раз в 30 с. Свой интервал у второго
 * наблюдателя перебивал бы общий: react-query берёт самый частый из подписанных.
 */
export const activeJobOptions = queryOptions({
  queryKey: ['active-job'],
  queryFn: api.activeJob,
  refetchInterval: (query) => (query.state.data?.job ? 5000 : 30_000)
});
