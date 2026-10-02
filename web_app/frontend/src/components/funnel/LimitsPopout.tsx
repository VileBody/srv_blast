import { useTranslation } from 'react-i18next';
import { cn } from '../../lib/cn';
import type { FunnelQuota, FunnelRules } from '../../lib/types';
import { Button, ButtonLink, GLYPH, Icon } from '../ui/kit';
import { FN_GLYPH, TripwireOffer, useCountdown } from './parts';
import { quotaLeft } from './useFunnel';

/*
 * Лимиты безлимита на трек живут там же, где лимиты видео и треков, — в кружке
 * (LimitsIndicator). Здесь две части:
 * - TrackLimitBar — третья строка поповера по ховеру: сколько можно сейчас или таймер;
 * - LimitsPopoutCard — окно у кружка, которое всплывает само, когда лимит кончился.
 *   В отличие от поповера оно кликабельно: в нём трипваер или вход в безлимит.
 */

/** Строка «Безлимит на трек» в поповере лимитов (та же геометрия, что у LimitBar). */
export function TrackLimitBar({ trackTitle, quota, now }: { trackTitle: string | null; quota: FunnelQuota; now?: number }) {
  const { t } = useTranslation();
  const timer = useCountdown(quota.allowed ? null : quota.availableAt, now);
  const { left, cap } = quotaLeft(quota);
  const status = quota.tripwire
    ? t('funnel.limits.noLimits')
    : quota.allowed
      ? t('funnel.limits.available', { n: left, cap })
      : t('funnel.limits.recharge', { time: timer.text });
  return (
    <span className="flex flex-col gap-[10px]">
      <span className="flex items-baseline justify-between gap-space-4 text-ui-14">
        <span className="min-w-0 truncate text-text-60" title={trackTitle ?? undefined}>{t('funnel.limits.track')}</span>
        <span className={cn('shrink-0 tabular-nums', quota.allowed ? 'text-text' : 'text-accent-light')}>{status}</span>
      </span>
      <span className="relative h-[6px] w-full overflow-hidden rounded-full bg-white/10">
        <span
          className={`absolute inset-y-0 left-0 rounded-full ${quota.tripwire ? 'limit-unlimited' : 'bg-grad-main'}`}
          style={{ width: quota.tripwire ? '100%' : `${cap ? (left / cap) * 100 : 0}%` }}
        />
      </span>
    </span>
  );
}

export type PopoutVariant = 'cooldown' | 'daily' | 'creditsOut';

export function LimitsPopoutCard({
  variant,
  trackTitle,
  availableAt,
  rules,
  onBuy,
  buyPending,
  onUnlock,
  onClose,
  now
}: {
  variant: PopoutVariant;
  trackTitle?: string | null;
  availableAt?: string | null;
  rules: FunnelRules;
  onBuy?: () => void;
  buyPending?: boolean;
  onUnlock?: () => void;
  onClose: () => void;
  /** витрина подставляет фиксированное время, чтобы таймер не тикал */
  now?: number;
}) {
  const { t } = useTranslation();
  const timer = useCountdown(variant === 'creditsOut' ? null : availableAt ?? null, now);
  return (
    <div
      role="dialog"
      aria-label={t(`funnel.popout.${variant}.title`)}
      className="fn-step pointer-events-auto flex w-[400px] flex-col gap-[16px] rounded-r15 border border-line-strong bg-card p-[20px] shadow-[0_24px_60px_rgba(5,1,15,0.5)] max-md:w-auto"
    >
      <div className="flex items-start justify-between gap-[12px]">
        <div className="min-w-0">
          <p className="text-ui-20 text-text">{t(`funnel.popout.${variant}.title`)}</p>
          {trackTitle && variant !== 'creditsOut' && <p className="mt-[4px] truncate text-ui-14 text-text-60">{trackTitle}</p>}
        </div>
        <Button variant="ghost" size="sm" iconOnly aria-label={t('common.close')} onClick={onClose}>
          <Icon>{GLYPH.close}</Icon>
        </Button>
      </div>

      {variant === 'creditsOut' ? (
        <>
          <p className="text-ui-14 text-text-80 [text-wrap:pretty]">{t('funnel.popout.creditsOut.text')}</p>
          <div className="flex flex-wrap items-center gap-[8px]">
            {/* без трека страницы открывать безлимит не на что — остаются тарифы */}
            {onUnlock && (
              <Button variant="primary" size="sm" onClick={onUnlock} icon={<Icon>{FN_GLYPH.key}</Icon>}>
                {t('funnel.popout.creditsOut.unlock')}
              </Button>
            )}
            <ButtonLink variant={onUnlock ? 'ghost' : 'primary'} size="sm" href="/app/pricing">{t('funnel.popout.plans')}</ButtonLink>
          </div>
        </>
      ) : (
        <>
          <div className="flex items-center gap-[14px] rounded-r10 bg-panel px-[16px] py-[12px]">
            <span className="text-ui-20 text-accent-light"><Icon>{FN_GLYPH.clock}</Icon></span>
            <div className="min-w-0">
              <p className="text-ui-32 leading-none tabular-nums text-text" aria-live="polite">{timer.text}</p>
              <p className="mt-[6px] text-ui-12 text-text-60">{t(`funnel.popout.${variant}.until`)}</p>
            </div>
          </div>
          <p className="text-ui-14 text-text-60 [text-wrap:pretty]">
            {t(`funnel.popout.${variant}.text`, { batches: rules.firstDayBatches, videos: rules.dailyVideos, hours: rules.cooldownHours })}
          </p>
          <TripwireOffer rules={rules} onBuy={onBuy} pending={buyPending} stacked />
        </>
      )}
    </div>
  );
}
