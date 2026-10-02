import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import type { FunnelQuota } from '../../lib/types';

/** Состояние воронки: квиз, действия, безлимит на трек и его квота. Общий ключ для всех мест. */
export function useFunnelState(enabled = true) {
  return useQuery({ queryKey: ['funnel-state'], queryFn: api.funnelState, staleTime: 15_000, retry: false, enabled });
}

/** Сколько осталось в текущем окне трека — для шкалы: «доступно» из «за раз». */
export function quotaLeft(quota: FunnelQuota | null | undefined): { left: number; cap: number } {
  if (!quota) return { left: 0, cap: 0 };
  return { left: quota.allowed ? quota.maxVideos : 0, cap: quota.tripwire ? quota.tripwireBatchCap : quota.batchCap };
}

function tripwireKey(trackId: string): string {
  const storageKey = `blast:tripwire-attempt:${trackId}`;
  try {
    const saved = sessionStorage.getItem(storageKey);
    if (saved) return saved;
    const fresh = crypto.randomUUID();
    sessionStorage.setItem(storageKey, fresh);
    return fresh;
  } catch {
    return crypto.randomUUID();
  }
}

/** Покупка трипваера 399 ₽: уводим в банк, вернёт туда же, откуда купили. */
export function useTripwirePurchase() {
  return useMutation({
    mutationFn: (trackId: string) =>
      api.funnelTripwire({ trackId, returnPath: window.location.pathname, idempotencyKey: tripwireKey(trackId) }),
    onSuccess: (order) => window.location.assign(order.paymentUrl)
  });
}
