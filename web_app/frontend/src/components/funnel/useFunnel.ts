import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../../lib/api';
import { currentAppPath } from '../../lib/appPath';
import { useToast } from '../../contexts/ToastContext';
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

const TRIPWIRE_ATTEMPT_PREFIX = 'blast:tripwire-attempt:';

function tripwireKey(trackId: string): string {
  const storageKey = `${TRIPWIRE_ATTEMPT_PREFIX}${trackId}`;
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

/**
 * Забыть ключи попыток оплаты трипваера. После возврата из банка (успех или отказ)
 * следующая попытка — новый заказ: со старым ключом бэк вернул бы тот же, уже
 * отклонённый банком заказ.
 */
export function forgetTripwireAttempts(): void {
  try {
    for (let i = sessionStorage.length - 1; i >= 0; i -= 1) {
      const key = sessionStorage.key(i);
      if (key?.startsWith(TRIPWIRE_ATTEMPT_PREFIX)) sessionStorage.removeItem(key);
    }
  } catch {
    /* приватный режим — ключей и не было */
  }
}

/** Покупка трипваера 399 ₽: уводим в банк, вернёт туда же, откуда купили (путь + query). */
export function useTripwirePurchase() {
  return useMutation({
    mutationFn: (trackId: string) => {
      // ?project=… обязателен визарду: без него возврат из банка открыл бы не тот проект
      const returnPath = currentAppPath();
      if (!returnPath) throw new Error(`tripwire: page ${window.location.pathname} cannot be a return path`);
      return api.funnelTripwire({ trackId, returnPath, idempotencyKey: tripwireKey(trackId) });
    },
    onSuccess: (order) => window.location.assign(order.paymentUrl)
  });
}

/**
 * Возврат из банка после трипваера (`?payment=success|failed` на любой странице /app):
 * тост, свежие лимиты и баланс, параметр снимаем, ключ попытки забываем. Живёт в
 * AppShell — вернуть могут и на батч, и в визард, и на генерацию. Тарифы (/app/pricing)
 * разбирают свой возврат сами.
 */
export function usePaymentReturn() {
  const { t } = useTranslation();
  const { push } = useToast();
  const queryClient = useQueryClient();
  const location = useLocation();
  const [search, setSearch] = useSearchParams();
  const payment = search.get('payment');
  useEffect(() => {
    if (!payment || location.pathname.startsWith('/app/pricing')) return;
    push(payment === 'success'
      ? { variant: 'success', title: t('funnel.tripwire.paid') }
      : { variant: 'error', title: t('funnel.tripwire.failed') });
    forgetTripwireAttempts();
    void queryClient.invalidateQueries({ queryKey: ['funnel-state'] });
    void queryClient.invalidateQueries({ queryKey: ['me'] });
    const next = new URLSearchParams(search);
    next.delete('payment');
    setSearch(next, { replace: true });
  }, [payment, location.pathname, search, setSearch, push, t, queryClient]);
}
