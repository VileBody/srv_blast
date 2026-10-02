import { useMutation, useQuery } from '@tanstack/react-query';
import { api, ApiError } from '../../lib/api';
import type { FunnelQuota, FunnelState } from '../../lib/types';

/** Название трека для людей — без расширения файла («Нет любви.mp3» → «Нет любви»). */
export function trackTitleOf(filename?: string | null): string | undefined {
  return filename ? filename.replace(/\.[A-Za-z0-9]{1,5}$/, '') : undefined;
}

/** Код ошибки бэка из тела `{detail: {code}}` (или undefined). */
export function apiErrorCode(error: unknown): string | undefined {
  if (!(error instanceof ApiError)) return undefined;
  return (error.detail as { detail?: { code?: string } } | null)?.detail?.code;
}

/** Трек, о котором речь на странице: id и хэш (по хэшу сверяем надёжнее). */
export interface TrackRef {
  id?: string;
  audioHash?: string;
}

/**
 * Тот ли это трек, на котором открыт безлимит. Сверяем по хэшу: у безлимита
 * SavedTrack мог пропасть (trackId null), а id одного трека бывает не один.
 * `null` — сравнить нечем (у страницы нет ни хэша, ни id).
 */
export function isUnlimitedTrack(unlimited: FunnelState['unlimited'], track?: TrackRef): boolean | null {
  if (!unlimited || !track) return null;
  if (track.audioHash && unlimited.audioHash) return track.audioHash === unlimited.audioHash;
  if (track.id && unlimited.trackId) return track.id === unlimited.trackId;
  return track.id || track.audioHash ? false : null;
}

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
