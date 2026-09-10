import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { isSubscriptionPlan, type Subscription } from '../../lib/types';
import { useToast } from '../../contexts/ToastContext';

/*
 * Статусы приходят кодами эквайринга Т-банка (NEW, DEADLINE_EXPIRED, INIT_FAILED…).
 * Пользователю нужен исход платежа, а не код интеграции, поэтому сводим их к пяти
 * понятным состояниям; незнакомый код — это заведомо не успешная оплата, поэтому
 * он попадает в «не завершён», а не показывается как есть.
 */
const PAYMENT_STATE: Record<string, string> = {
  CONFIRMED: 'paid', AUTHORIZED: 'paid',
  NEW: 'unfinished', FORM_SHOWED: 'unfinished', AUTHORIZING: 'unfinished', CONFIRMING: 'unfinished', INIT_IN_PROGRESS: 'unfinished',
  DEADLINE_EXPIRED: 'expired', ATTEMPTS_EXPIRED: 'expired',
  REJECTED: 'declined', INIT_FAILED: 'declined', CANCELED: 'declined', AUTH_FAIL: 'declined',
  REFUNDED: 'refunded', PARTIAL_REFUNDED: 'refunded'
};

function formatDate(iso: string | null | undefined, locale: string): string {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString(locale, { day: 'numeric', month: 'short', year: 'numeric' });
}

export function BillingCard({ subscription }: { subscription: Subscription }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language.startsWith('en') ? 'en-GB' : 'ru-RU';
  const queryClient = useQueryClient();
  const { push } = useToast();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const status = subscription.billingStatus ?? (subscription.tier === 'TRIAL' ? 'trial' : 'active');
  const renews = formatDate(subscription.renewsAt, locale);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['me'] });
  const fail = (error: unknown) => push({ variant: 'error', title: error instanceof Error ? error.message : t('simple.error') });
  const retryMutation = useMutation({ mutationFn: api.retryPayment, onSuccess: async () => { await refresh(); push({ variant: 'success', title: t('billing.retryOk') }); }, onError: fail });
  const cancelMutation = useMutation({ mutationFn: () => api.cancelSubscription(false), onSuccess: async () => { setConfirmCancel(false); await refresh(); push({ variant: 'info', title: t('billing.cancelOk') }); }, onError: fail });
  const resumeMutation = useMutation({ mutationFn: api.resumeSubscription, onSuccess: async () => { await refresh(); push({ variant: 'success', title: t('billing.resumeOk') }); }, onError: fail });
  const busy = retryMutation.isPending || cancelMutation.isPending || resumeMutation.isPending;
  if (subscription.tier === 'TRIAL') return null;

  const recurring = isSubscriptionPlan(subscription);
  const until = formatDate(subscription.expiresAt, locale);
  const view = !recurring
    ? { title: t('billing.oneTimeTitle'), text: until ? t('billing.oneTimeUntil', { date: until }) : t('billing.oneTimeText'), tone: 'ok' }
    : status === 'past_due'
      ? { title: t('billing.failedTitle'), text: t('billing.failedText'), tone: 'warning' }
      : status === 'canceled'
        ? { title: t('billing.canceledTitle'), text: renews ? t('billing.canceledText', { date: renews }) : t('billing.canceledTextNoDate'), tone: 'muted' }
        : { title: t('billing.activeTitle'), text: renews ? t('billing.activeText', { date: renews }) : '', tone: 'ok' };
  const payments = subscription.payments ?? [];
  return <section className="card-2 shrink-0 p-[40px]">
    <h2 className="text-[24px] font-[350] leading-none text-text">{t('billing.title')}</h2>
    <div className="mt-[28px] grid gap-[20px] lg:grid-cols-[minmax(280px,.8fr)_minmax(420px,1.2fr)]">
      <div className="relative flex min-h-[190px] flex-col rounded-r15 border border-[rgba(139,111,230,.28)] bg-[rgba(16,9,34,.32)] p-[24px]">
        <span className="relative z-[1] text-[14px] text-text-40">{t('billing.currentPlan')}</span>
        <strong className="relative z-[1] mt-[14px] text-[24px] font-[400] text-text">{view.title}</strong>
        {view.text && <p className="relative z-[1] mt-[8px] text-[15px] leading-[21px] text-text-60">{view.text}</p>}
        <div className="relative z-[1] mt-auto flex flex-wrap gap-[10px] pt-[24px]">
          {status === 'past_due' && <button type="button" disabled={busy} onClick={() => retryMutation.mutate()} className="h-[42px] rounded-r15 bg-accent px-[18px] text-[15px] text-text disabled:opacity-50">{t('billing.retryCta')}</button>}
          {status === 'canceled' && <button type="button" disabled={busy} onClick={() => resumeMutation.mutate()} className="h-[42px] rounded-r15 bg-accent px-[18px] text-[15px] text-text disabled:opacity-50">{t('billing.resumeCta')}</button>}
          {recurring && !subscription.cancelAtPeriodEnd && !confirmCancel && <button type="button" disabled={busy} onClick={() => setConfirmCancel(true)} className="h-[42px] rounded-r15 border border-[rgba(246,245,253,.22)] px-[18px] text-[15px] text-text-60 transition hover:border-accent-light hover:text-text">{t('billing.cancelCta')}</button>}
          {confirmCancel && <>
            <button type="button" disabled={busy} onClick={() => cancelMutation.mutate()} className="h-[42px] rounded-r15 border border-[var(--warning)] px-[18px] text-[15px] text-[var(--warning)]">{t('billing.cancelYes')}</button>
            <button type="button" onClick={() => setConfirmCancel(false)} className="h-[42px] rounded-r15 px-[16px] text-[15px] text-text-60">{t('common.cancel')}</button>
          </>}
        </div>
      </div>

      <div className="min-w-0 px-[24px] py-[8px]">
        <h3 className="text-[18px] font-[400] text-text">{t('billing.history')}</h3>
        {payments.length ? <div className="mt-[16px] divide-y divide-[rgba(246,245,253,.08)]">
          {payments.map(payment => <div key={payment.orderId} className="grid grid-cols-[1fr_auto_auto] items-center gap-[18px] py-[13px] text-[14px]">
            <span className="min-w-0 truncate text-text-80">{formatDate(payment.createdAt, locale)}</span>
            <span className="text-text">{payment.amountRub.toLocaleString(locale)} ₽</span>
            <span className="min-w-[92px] text-right text-text-60">{t(`billing.paymentStatus.${PAYMENT_STATE[payment.status.toUpperCase()] ?? 'unfinished'}`)}</span>
          </div>)}
        </div> : <p className="mt-[20px] text-[15px] text-text-60">{t('billing.noHistory')}</p>}
      </div>
    </div>
  </section>;
}
