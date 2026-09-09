import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { isSubscriptionPlan, type Subscription } from '../../lib/types';
import { useToast } from '../../contexts/ToastContext';

/** Фантомная «строка счёта» — тот же приём, что в пустом состоянии проектов на дашборде. */
function GhostBillingRow() {
  return (
    <div aria-hidden="true" className="flex items-center gap-space-5 opacity-[0.18]">
      <span className="h-[44px] w-[44px] shrink-0 rounded-[8px] border border-dashed border-[rgba(246,245,253,0.18)]" />
      <div className="min-w-0 flex-1">
        <span className="block h-[14px] w-[45%] rounded-[4px] bg-[rgba(246,245,253,0.10)]" />
        <span className="mt-[10px] block h-[10px] w-[70px] rounded-[4px] bg-[rgba(246,245,253,0.10)]" />
      </div>
      <span className="block h-[24px] w-[64px] shrink-0 rounded-[4px] bg-[rgba(246,245,253,0.10)]" />
    </div>
  );
}

function formatDate(iso?: string | null): string {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
}

/**
 * Состояние оплаты подписки (Профиль).
 *
 * Подписка не «заканчивается» сама: она либо продлевается, либо повисает в `past_due`,
 * пока юзер не обновит платёж. Отмена доступна всегда, включая `past_due` — это условие
 * оферты, и запирать юзера в неудачной оплате нельзя.
 */
export function BillingCard({ subscription }: { subscription: Subscription }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { push } = useToast();
  const [confirmCancel, setConfirmCancel] = useState(false);

  const status = subscription.billingStatus ?? (subscription.tier === 'TRIAL' ? 'trial' : 'active');
  const renews = formatDate(subscription.renewsAt);

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['me'] });
  const fail = (error: unknown) => push({
    variant: 'error',
    title: error instanceof Error ? error.message : t('simple.error')
  });

  const retryMutation = useMutation({
    mutationFn: api.retryPayment,
    onSuccess: async () => { await refresh(); push({ variant: 'success', title: t('billing.retryOk') }); },
    onError: fail
  });
  const cancelMutation = useMutation({
    mutationFn: () => api.cancelSubscription(false),
    onSuccess: async () => { setConfirmCancel(false); await refresh(); push({ variant: 'info', title: t('billing.cancelOk') }); },
    onError: fail
  });
  const resumeMutation = useMutation({
    mutationFn: api.resumeSubscription,
    onSuccess: async () => { await refresh(); push({ variant: 'success', title: t('billing.resumeOk') }); },
    onError: fail
  });

  const busy = retryMutation.isPending || cancelMutation.isPending || resumeMutation.isPending;

  // Триал оплачивать нечего — карточка состояния оплаты не нужна
  if (subscription.tier === 'TRIAL') return null;

  /*
   * Разовая покупка (Glow/Impulse) — не подписка: продлевать и отменять нечего.
   * Раньше ей рисовали ровно ту же карточку, что и подписке Blast, и человек считал,
   * что с него будут списывать каждый месяц.
   */
  if (!isSubscriptionPlan(subscription)) {
    const until = formatDate(subscription.expiresAt);
    return (
      <section className="card-2 shrink-0 p-[40px]">
        <div className="flex items-baseline justify-between gap-space-4">
          <h2 className="text-[24px] font-[350] leading-none text-text">{t('billing.title')}</h2>
          <span
            className="rounded-[15px] px-[14px] py-[6px] text-[14px] leading-none"
            style={{ background: 'var(--success-bg)', color: 'var(--success)' }}
          >
            {t('billing.status.paid')}
          </span>
        </div>
        <p className="mt-[20px] text-[18px] leading-[23px] text-text">{t('billing.oneTimeTitle')}</p>
        <p className="mt-[8px] max-w-[520px] text-[15px] leading-[20px] text-text-60">
          {until ? t('billing.oneTimeUntil', { date: until }) : t('billing.oneTimeText')}
        </p>
      </section>
    );
  }

  const view = status === 'past_due'
    ? { title: t('billing.failedTitle'), text: t('billing.failedText'), cta: t('billing.retryCta'), onCta: () => retryMutation.mutate(), tone: 'warning' as const }
    : status === 'canceled'
      ? { title: t('billing.canceledTitle'), text: renews ? t('billing.canceledText', { date: renews }) : t('billing.canceledTextNoDate'), cta: t('billing.resumeCta'), onCta: () => resumeMutation.mutate(), tone: 'muted' as const }
      : { title: t('billing.activeTitle'), text: renews ? t('billing.activeText', { date: renews }) : '', cta: null, onCta: undefined, tone: 'ok' as const };

  return (
    <section className="card-2 shrink-0 p-[40px]">
      <div className="flex items-baseline justify-between gap-space-4">
        <h2 className="text-[24px] font-[350] leading-none text-text">{t('billing.title')}</h2>
        <span
          className="rounded-[15px] px-[14px] py-[6px] text-[14px] leading-none"
          style={{
            background: view.tone === 'warning' ? 'var(--warning-bg)' : view.tone === 'ok' ? 'var(--success-bg)' : 'var(--neutral-bg)',
            color: view.tone === 'warning' ? 'var(--warning)' : view.tone === 'ok' ? 'var(--success)' : 'var(--text-40)'
          }}
        >
          {t(`billing.status.${status}`)}
        </span>
      </div>

      <div className="relative mt-[28px]">
        {/* фантомы — фон под сообщением, как в пустом состоянии проектов */}
        <GhostBillingRow />
        <div className="my-[24px] h-px w-full bg-[rgba(246,245,253,0.06)]" />
        <GhostBillingRow />

        <div className="absolute inset-[-12px] flex flex-col items-center justify-center gap-space-3 rounded-r15 bg-[rgba(16,9,34,0.72)] text-center backdrop-blur-[2px]">
          <p className="text-[18px] leading-[23px] text-text">{view.title}</p>
          {view.text && <p className="max-w-[360px] text-[15px] leading-[20px] text-text-60">{view.text}</p>}
          {view.cta && (
            <button
              type="button"
              onClick={view.onCta}
              disabled={busy}
              className="mt-[4px] flex h-[48px] items-center rounded-r15 bg-accent px-space-5 text-[18px] font-[400] leading-none text-text transition hover:brightness-110 disabled:opacity-60 focus-visible:outline-none"
            >
              {busy ? t('common.loading') : view.cta}
            </button>
          )}
        </div>
      </div>

      {/* Отмена — всегда на виду и доступна в любом состоянии платного плана (условие оферты) */}
      <div className="mt-[28px] flex flex-wrap items-center justify-between gap-space-3">
        {confirmCancel ? (
          <div className="flex flex-wrap items-center gap-space-3">
            <span className="text-[15px] leading-[20px] text-text-80">
              {renews ? t('billing.cancelConfirm', { date: renews }) : t('billing.cancelConfirmNoDate')}
            </span>
            <button
              type="button"
              onClick={() => cancelMutation.mutate()}
              disabled={busy}
              className="h-[40px] rounded-r10 border border-[var(--warning)] px-[18px] text-[15px] leading-none text-[var(--warning)] transition hover:brightness-125 disabled:opacity-60 focus-visible:outline-none"
            >
              {t('billing.cancelYes')}
            </button>
            <button
              type="button"
              onClick={() => setConfirmCancel(false)}
              className="h-[40px] rounded-r10 px-[14px] text-[15px] leading-none text-text-60 transition hover:text-text focus-visible:outline-none"
            >
              {t('common.cancel')}
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setConfirmCancel(true)}
            disabled={busy || subscription.cancelAtPeriodEnd}
            className="text-[15px] leading-none text-text-60 underline underline-offset-4 transition hover:text-text disabled:opacity-40 focus-visible:outline-none"
          >
            {t('billing.cancelCta')}
          </button>
        )}
      </div>
    </section>
  );
}
