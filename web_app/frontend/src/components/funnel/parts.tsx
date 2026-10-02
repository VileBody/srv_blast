import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '../../lib/cn';
import type { FunnelQuestion, FunnelQuota, FunnelRules, RatingReason } from '../../lib/types';
import { Button, ButtonLink, GLYPH, Icon, Pill } from '../ui/kit';

/* Глифы воронки: те же правила, что у GLYPH (viewBox 24, обводка 1.8, ~15 единиц). */
export const FN_GLYPH = {
  send: <path d="M20 4.5 4.5 11l6 2.5 2.5 6Zm-9.5 9 9.5-9" />,
  doc: <path d="M7 4.5h7l4 4V19a.5.5 0 0 1-.5.5h-10A.5.5 0 0 1 7 19Zm7 0V9h4M9.5 13h5M9.5 16h5" />,
  clock: <path d="M12 7v5l3 2M19.5 12a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0Z" />,
  bolt: <path d="M13 4.5 6.5 13H12l-1 6.5 6.5-8.5H12Z" />,
  key: <path d="M14.5 9.5a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm-1 2.2 5 5M16.5 14.7l-1.5 1.5M18.5 16.7 17 18.2" />
};

export const RATING_REASONS: RatingReason[] = ['subtitles', 'footage', 'transitions', 'other'];

/* ------------------------------------------------------------------ оценка */

/**
 * Шкала 1–10. Одна строка из десяти квадратов: оценка ставится одним нажатием, без
 * подтверждения. Выбранная — акцентная заливка, остальные тихие.
 */
export function RatingScale({
  value,
  onChange,
  disabled,
  label
}: {
  value: number | null;
  onChange: (score: number) => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <div className="flex items-center gap-[4px] max-md:w-full" role="radiogroup" aria-label={label}>
      {Array.from({ length: 10 }, (_, index) => {
        const score = index + 1;
        const active = value === score;
        return (
          <button
            key={score}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled}
            onClick={() => onChange(score)}
            className={cn(
              'grid h-ctl-sm w-[32px] min-w-0 place-items-center rounded-r6 max-md:w-auto max-md:flex-1 text-ui-14 tabular-nums transition-[background-color,color,transform] duration-150 active:scale-[.94] disabled:pointer-events-none disabled:opacity-40',
              'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-light',
              active ? 'bg-accent-strong text-text' : value !== null && score < value ? 'bg-accent-soft text-text-80' : 'bg-field text-text-60 hover:bg-field-hover hover:text-text'
            )}
          >
            {score}
          </button>
        );
      })}
    </div>
  );
}

export function ReasonPills({
  value,
  onChange
}: {
  value: RatingReason[];
  onChange: (next: RatingReason[]) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap gap-[8px]" role="group" aria-label={t('funnel.rating.whatsWrong')}>
      {RATING_REASONS.map((reason) => {
        const on = value.includes(reason);
        return (
          <Pill key={reason} pressed={on} onClick={() => onChange(on ? value.filter((r) => r !== reason) : [...value, reason])}>
            {t(`funnel.rating.reason.${reason}`)}
          </Pill>
        );
      })}
    </div>
  );
}

/**
 * Оценка под готовым роликом (страница батча). Низкая оценка сразу спрашивает, что не
 * так, и показывает, где это правится: в Blast это таймлайн, а не новая генерация.
 */
export function VideoRatingRow({
  score,
  reasons,
  onRate,
  onReasons,
  fixHref,
  pending
}: {
  score: number | null;
  reasons: RatingReason[];
  onRate: (score: number) => void;
  onReasons: (next: RatingReason[]) => void;
  fixHref?: string;
  pending?: boolean;
}) {
  const { t } = useTranslation();
  const low = score !== null && score <= 6;
  return (
    <div className="border-t border-line px-[28px] py-[12px] max-md:px-[14px]">
      <div className="flex flex-wrap items-center justify-between gap-[12px]">
        <span className="text-ui-14 text-text-60">
          {score === null ? t('funnel.rating.prompt') : low ? t('funnel.rating.thanksLow') : t('funnel.rating.thanksHigh')}
        </span>
        <RatingScale value={score} onChange={onRate} disabled={pending} label={t('funnel.rating.prompt')} />
      </div>
      {low && (
        <div className="fn-step mt-[12px] flex flex-wrap items-center justify-between gap-[12px]">
          <ReasonPills value={reasons} onChange={onReasons} />
          {fixHref && (
            <ButtonLink variant="ghost" size="sm" href={fixHref} iconEnd={<Icon>{GLYPH.arrowRight}</Icon>}>
              {t('funnel.rating.fix')}
            </ButtonLink>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ квиз */

/**
 * Один вопрос квиза. Ответ — одно нажатие на строку, дальше вопрос меняется сам:
 * кнопка «Дальше» для одного выбора — лишний шаг.
 */
export function QuizQuestion({
  question,
  onAnswer,
  pendingId,
  selectedId
}: {
  question: FunnelQuestion;
  onAnswer: (answerId: string) => void;
  pendingId?: string | null;
  selectedId?: string | null;
}) {
  return (
    <fieldset>
      <legend className="mb-[14px] text-ui-20 text-text [text-wrap:balance]">{question.text}</legend>
      <div className="flex flex-col gap-[8px]">
        {question.options.map((option) => {
          const chosen = selectedId === option.id || pendingId === option.id;
          return (
            <button
              key={option.id}
              type="button"
              disabled={Boolean(pendingId)}
              onClick={() => onAnswer(option.id)}
              className={cn(
                'group flex min-h-ctl items-center justify-between gap-[12px] rounded-r15 border px-[18px] py-[10px] text-left text-ui-16 transition-[background-color,border-color,transform] duration-150 active:scale-[.985] disabled:cursor-wait',
                'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-light',
                chosen ? 'border-accent-line bg-accent-soft text-text' : 'border-line bg-field text-text-80 hover:border-line-strong hover:text-text'
              )}
            >
              <span>{option.label}</span>
              <Icon tone={chosen ? 'accent' : 'muted'} className="transition-transform duration-150 group-hover:translate-x-[2px]">
                {chosen ? GLYPH.check : GLYPH.right}
              </Icon>
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

/* ------------------------------------------------------------------ методичка */

export type MethodologyState = 'idle' | 'sending' | 'sent' | 'link' | 'needBot' | 'error';

export function MethodologyCard({
  state,
  url,
  botLink,
  onGet
}: {
  state: MethodologyState;
  url?: string | null;
  botLink?: string;
  onGet: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-[16px] rounded-r15 bg-panel p-[16px] max-md:flex-col max-md:items-stretch">
      <span className="grid h-[44px] w-[44px] shrink-0 place-items-center rounded-r10 bg-accent-soft text-ui-20 text-accent-light max-md:hidden">
        <Icon>{FN_GLYPH.doc}</Icon>
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-ui-16 text-text">{t('funnel.methodology.title')}</p>
        <p className="mt-[4px] text-ui-14 text-text-60">
          {state === 'sent'
            ? t('funnel.methodology.sent')
            : state === 'needBot'
              ? t('funnel.methodology.needBot')
              : state === 'error'
                ? t('funnel.methodology.error')
                : t('funnel.methodology.text')}
        </p>
      </div>
      {state === 'link' && url ? (
        <ButtonLink variant="primary" size="sm" href={url} target="_blank" rel="noreferrer" icon={<Icon>{GLYPH.download}</Icon>}>
          {t('funnel.methodology.open')}
        </ButtonLink>
      ) : state === 'needBot' && botLink ? (
        <ButtonLink variant="primary" size="sm" href={botLink} target="_blank" rel="noreferrer" icon={<Icon>{FN_GLYPH.send}</Icon>}>
          {t('funnel.methodology.openBot')}
        </ButtonLink>
      ) : state === 'sent' ? (
        <span className="flex items-center gap-[6px] text-ui-14 text-success">
          <Icon>{GLYPH.check}</Icon>
          {t('funnel.methodology.sentShort')}
        </span>
      ) : (
        <Button variant="primary" size="sm" loading={state === 'sending'} onClick={onGet}>
          {t('funnel.methodology.get')}
        </Button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ питч */

/**
 * Три способа собирать больше — лестница, а не три одинаковые карточки: строки одной
 * таблицы, слева что даёт, справа цена. Бесплатная ступень выделена: это следующий шаг.
 */
export function PitchLadder({ rules, highlight = 'free' }: { rules: FunnelRules; highlight?: 'free' | 'tripwire' }) {
  const { t } = useTranslation();
  const rows = [
    {
      id: 'free' as const,
      title: t('funnel.pitch.freeTitle'),
      text: t('funnel.pitch.freeText', { cap: rules.batchCap, hours: rules.cooldownHours }),
      price: t('funnel.pitch.freePrice')
    },
    {
      id: 'tripwire' as const,
      title: t('funnel.pitch.tripwireTitle'),
      text: t('funnel.pitch.tripwireText', { cap: rules.tripwireBatchCap }),
      price: t('funnel.pitch.tripwirePrice', { price: rules.tripwirePriceRub })
    },
    {
      id: 'blast' as const,
      title: t('funnel.pitch.blastTitle'),
      text: t('funnel.pitch.blastText'),
      price: t('funnel.pitch.blastPrice')
    }
  ];
  return (
    <ol className="overflow-hidden rounded-r15 border border-line">
      {rows.map((row, index) => {
        const on = row.id === highlight;
        return (
          <li
            key={row.id}
            className={cn(
              'flex items-start justify-between gap-[16px] px-[18px] py-[14px]',
              index > 0 && 'border-t border-line',
              on && 'bg-accent-soft'
            )}
          >
            <div className="min-w-0">
              <p className="text-ui-16 text-text">{row.title}</p>
              <p className="mt-[4px] text-ui-14 text-text-60 [text-wrap:pretty]">{row.text}</p>
            </div>
            <span className={cn('shrink-0 whitespace-nowrap text-ui-16 tabular-nums', on ? 'text-accent-light' : 'text-text-80')}>{row.price}</span>
          </li>
        );
      })}
    </ol>
  );
}

/* ------------------------------------------------------------------ шаги разблокировки */

export type ActionStatus = 'todo' | 'checking' | 'done' | 'missing';

export function UnlockActionRow({
  index,
  status,
  title,
  text,
  children
}: {
  index: number;
  status: ActionStatus;
  title: string;
  text: ReactNode;
  children?: ReactNode;
}) {
  const done = status === 'done';
  return (
    <li className="flex items-start gap-[14px] py-[16px] [&+&]:border-t [&+&]:border-line">
      <span
        className={cn(
          'grid h-ctl-xs w-[24px] shrink-0 place-items-center rounded-full text-ui-14 tabular-nums transition-[background-color,color] duration-300',
          done ? 'bg-success-bg text-success' : 'bg-field text-text-60'
        )}
        aria-hidden="true"
      >
        {done ? <Icon>{GLYPH.check}</Icon> : index}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-ui-16 text-text">{title}</p>
        <div className="mt-[4px] text-ui-14 text-text-60 [text-wrap:pretty]">{text}</div>
        {children && <div className="mt-[12px] flex flex-wrap gap-[8px]">{children}</div>}
      </div>
    </li>
  );
}

/* ------------------------------------------------------------------ безлимит открыт */

/**
 * Авторский момент воронки: «билет» безлимита. Шкала квоты набирается один раз
 * (fn-meter-fill), ниже — правила простым языком, без мелкого шрифта.
 */
export function UnlockedTicket({
  trackTitle,
  rules,
  quota
}: {
  trackTitle: string;
  rules: FunnelRules;
  quota: FunnelQuota | null;
}) {
  const { t } = useTranslation();
  const left = quota ? (quota.allowed ? quota.maxVideos : 0) : rules.batchCap;
  const cap = quota?.batchCap ?? rules.batchCap;
  return (
    <div className="overflow-hidden rounded-r15 border border-accent-line bg-panel">
      <div className="px-[18px] pb-[16px] pt-[18px]">
        <div className="flex items-baseline justify-between gap-[12px]">
          <p className="min-w-0 truncate text-ui-20 text-text" title={trackTitle}>{trackTitle}</p>
          <span className="shrink-0 text-ui-14 tabular-nums text-text-60">{t('funnel.done.left', { n: left, cap })}</span>
        </div>
        <div className="mt-[12px] h-[6px] overflow-hidden rounded-full bg-field" aria-hidden="true">
          <div className="fn-meter-fill h-full rounded-full bg-accent-light" style={{ width: `${cap ? (left / cap) * 100 : 0}%` }} />
        </div>
      </div>
      <ul className="grid grid-cols-3 border-t border-line max-md:grid-cols-1">
        {[
          t('funnel.done.ruleCap', { cap: rules.batchCap }),
          t('funnel.done.ruleCooldown', { hours: rules.cooldownHours }),
          t('funnel.done.ruleDaily', { batches: rules.firstDayBatches, videos: rules.dailyVideos })
        ].map((rule, index) => (
          <li key={index} className={cn('px-[18px] py-[12px] text-ui-14 text-text-80 [text-wrap:pretty]', index > 0 && 'border-l border-line max-md:border-l-0 max-md:border-t')}>
            {rule}
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------------ перезарядка */

export function useCountdown(target: string | null, now?: number) {
  const [tick, setTick] = useState(() => now ?? Date.now());
  useEffect(() => {
    if (now !== undefined || !target) return undefined;
    const id = window.setInterval(() => setTick(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [now, target]);
  const ms = target ? Math.max(0, new Date(target).getTime() - (now ?? tick)) : 0;
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return { done: ms === 0, text: `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` };
}

/** Оффер трипваера: одна строка что даёт, справа цена; это не тариф и не подписка. */
export function TripwireOffer({
  rules,
  onBuy,
  pending,
  stacked = false,
  expiresAt,
  now
}: {
  rules: FunnelRules;
  onBuy?: () => void;
  pending?: boolean;
  /** узкое место (окно у кружка лимитов): текст на всю ширину, кнопка под ним */
  stacked?: boolean;
  /** конец суточного окна предложения — показываем, сколько осталось */
  expiresAt?: string | null;
  now?: number;
}) {
  const { t } = useTranslation();
  const left = useCountdown(expiresAt ?? null, now);
  return (
    <div className={cn('flex gap-[16px] rounded-r15 border border-accent-line bg-accent-soft p-[16px]', stacked ? 'flex-col' : 'items-center max-md:flex-col max-md:items-stretch')}>
      {!stacked && (
        <span className="grid h-[44px] w-[44px] shrink-0 place-items-center rounded-r10 bg-card text-ui-20 text-accent-light max-md:hidden">
          <Icon>{FN_GLYPH.bolt}</Icon>
        </span>
      )}
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-[8px] text-ui-16 text-text">
          {stacked && <Icon tone="accent">{FN_GLYPH.bolt}</Icon>}
          {t('funnel.tripwire.title', { price: rules.tripwirePriceRub })}
        </p>
        <p className="mt-[4px] text-ui-14 text-text-60 [text-wrap:pretty]">{t('funnel.tripwire.text', { cap: rules.tripwireBatchCap })}</p>
        {expiresAt && (
          <p className="mt-[8px] flex items-center gap-[6px] text-ui-12 tabular-nums text-accent-light">
            <Icon>{FN_GLYPH.clock}</Icon>
            {t('funnel.tripwire.expires', { time: left.text })}
          </p>
        )}
      </div>
      {onBuy && (
        <Button variant="primary" size="sm" loading={pending} onClick={onBuy} className={stacked ? 'self-start' : undefined}>
          {t('funnel.tripwire.buy')}
        </Button>
      )}
    </div>
  );
}
