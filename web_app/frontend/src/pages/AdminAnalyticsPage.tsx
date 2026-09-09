import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { api, ApiError } from '../lib/api';
import { Skeleton } from '../components/ui/Skeleton';
import { QueryError, queryDown } from '../components/ui/ErrorState';

/**
 * Сквозная аналитика (админка).
 *
 * Всё считается из одного потока событий: воронка — по уникальным юзерам на шаге,
 * удержание — недельными когортами. Отдельных счётчиков нет намеренно: их пришлось бы
 * держать в консистентности руками, а из событий цифры всегда пересчитываются.
 */

const PERIODS = [7, 30, 90] as const;

function StatTile({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-r15 bg-grad-soft-10 p-space-5">
      <p className="text-[14px] leading-none text-text-60">{label}</p>
      <p className="mt-[12px] text-[32px] font-[350] leading-none text-text">{value}</p>
      {hint && <p className="mt-[8px] text-[13px] leading-[17px] text-text-40">{hint}</p>}
    </div>
  );
}

/** Полоса шага воронки: ширина = доля от входа, подпись — конверсия от предыдущего шага. */
function FunnelBar({ step, users, fromPrev, fromStart, label }: {
  step: string; users: number; fromPrev: number; fromStart: number; label: string;
}) {
  const dropped = fromPrev < 100 && fromPrev > 0;
  return (
    <div key={step} className="flex items-center gap-space-4">
      <span className="w-[190px] shrink-0 truncate text-[15px] leading-none text-text-80">{label}</span>
      <span className="relative h-[28px] min-w-0 flex-1 overflow-hidden rounded-[8px] bg-grad-soft-10">
        <span className="absolute inset-y-0 left-0 rounded-[8px] bg-grad-main transition-[width]" style={{ width: `${Math.max(fromStart, 1)}%` }} />
      </span>
      <span className="w-[64px] shrink-0 text-right text-[16px] leading-none text-text">{users}</span>
      <span className={`w-[76px] shrink-0 text-right text-[14px] leading-none ${dropped ? 'text-warning' : 'text-text-40'}`}>
        {fromPrev}%
      </span>
    </div>
  );
}

export function AdminAnalyticsPage() {
  const { t } = useTranslation();
  const [days, setDays] = useState<number>(30);
  const query = useQuery({
    queryKey: ['admin-analytics', days],
    queryFn: () => api.adminAnalytics(days),
    retry: (count, error) => !(error instanceof ApiError && error.status === 403) && count < 1
  });

  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  if (forbidden) {
    return (
      <div className="card-2 flex flex-1 flex-col items-center justify-center gap-space-3 p-[40px] text-center">
        <h1 className="text-[32px] font-[400]">{t('admin.forbidden')}</h1>
        <p className="max-w-[420px] text-[16px] leading-[21px] text-text-60">{t('admin.forbiddenText')}</p>
      </div>
    );
  }
  if (queryDown(query)) return <QueryError query={query} className="min-h-[560px]" />;
  if (query.isLoading) return <Skeleton className="h-full min-h-[560px]" />;

  const data = query.data;
  const s = data?.summary;
  const d = data?.delivery;
  const f = data?.flow;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-[20px] pb-space-6">
      <section className="card-2 shrink-0 p-[40px]">
        <div className="flex flex-wrap items-center justify-between gap-space-4">
          <h1 className="text-[32px] font-[400] leading-none text-text">{t('admin.title')}</h1>
          <div className="flex items-center gap-[8px]">
            {PERIODS.map((period) => (
              <button
                key={period}
                type="button"
                onClick={() => setDays(period)}
                className={`h-[36px] rounded-r10 px-[14px] text-[15px] leading-none transition focus-visible:outline-none ${
                  days === period ? 'bg-accent text-text' : 'bg-grad-soft-10 text-text-60 hover:text-text'
                }`}
              >
                {t('admin.days', { count: period })}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-[28px] grid gap-[16px] sm:grid-cols-2 lg:grid-cols-4">
          <StatTile label={t('admin.activeUsers')} value={s?.activeUsers ?? 0} />
          <StatTile label={t('admin.signups')} value={s?.signups ?? 0} />
          <StatTile label={t('admin.paying')} value={s?.payingUsers ?? 0} hint={t('admin.conversion', { value: s?.conversionToPaid ?? 0 })} />
          <StatTile label={t('admin.videosGenerated')} value={s?.videosGenerated ?? 0} hint={t('admin.posted', { count: s?.videosPosted ?? 0 })} />
          <StatTile label={t('admin.failRate')} value={`${s?.generationFailRate ?? 0}%`} />
          <StatTile label={t('admin.paymentFailures')} value={s?.paymentFailures ?? 0} />
          <StatTile label={t('admin.cancellations')} value={s?.cancellations ?? 0} />
          <StatTile label={t('admin.limitHits')} value={s?.limitHits ?? 0} hint={t('admin.limitHitsHint')} />
        </div>
      </section>

      <section className="card-2 shrink-0 p-[40px]">
        <h2 className="text-[24px] font-[350] leading-none text-text">{t('admin.funnel')}</h2>
        <div className="mt-[8px] flex items-center gap-space-4 text-[13px] leading-none text-text-40">
          <span className="w-[190px] shrink-0">{t('admin.step')}</span>
          <span className="min-w-0 flex-1" />
          <span className="w-[64px] shrink-0 text-right">{t('admin.users')}</span>
          <span className="w-[76px] shrink-0 text-right">{t('admin.fromPrev')}</span>
        </div>
        <div className="mt-[16px] flex flex-col gap-[14px]">
          {(data?.funnel ?? []).map((row) => (
            <FunnelBar key={row.step} {...row} label={t(`admin.steps.${row.step}`)} />
          ))}
        </div>
      </section>

      {/* Выкладка: главный вопрос продукта — доходит ли сгенерированное до площадки */}
      <section className="card-2 shrink-0 p-[40px]">
        <h2 className="text-[24px] font-[350] leading-none text-text">{t('delivery.title')}</h2>
        <p className="mt-[8px] text-[14px] leading-[19px] text-text-60">{t('delivery.hint')}</p>

        <div className="mt-[24px] grid gap-[16px] sm:grid-cols-2 lg:grid-cols-4">
          <StatTile label={t('delivery.generated')} value={d?.generated ?? 0} />
          <StatTile label={t('delivery.posted')} value={d?.posted ?? 0} hint={`${d?.postRate ?? 0}% ${t('delivery.postRate')}`} />
          <StatTile label={t('delivery.notPosted')} value={d?.generatedNotPosted ?? 0} hint={t('delivery.stuck') + ': ' + (d?.stuckAfterGeneration ?? 0)} />
          <StatTile label={t('delivery.avgBatches')} value={d?.avgBatchesPerUser ?? 0} />
          <StatTile label={t('delivery.sawPricing')} value={d?.sawPricing ?? 0} hint={`${d?.pricingToPaid ?? 0}% ${t('delivery.pricingToPaid')}`} />
          <StatTile label={t('delivery.hitLimit')} value={d?.hitLimit ?? 0} hint={`${d?.limitToPaid ?? 0}% ${t('delivery.limitToPaid')}`} />
          <StatTile label={t('delivery.postRatePaid')} value={`${d?.postRateByPaid.paid ?? 0}%`} />
          <StatTile label={t('delivery.postRateFree')} value={`${d?.postRateByPaid.free ?? 0}%`} />
        </div>

        {/* Распределение «где остановились» — дополняет воронку: она показывает переходы,
            а это — сколько людей осело на каждом шаге прямо сейчас */}
        <h3 className="mt-[32px] text-[16px] leading-none text-text-80">{t('delivery.byStage')}</h3>
        <div className="mt-[14px] flex flex-col gap-[10px]">
          {Object.entries(d?.byStage ?? {}).map(([step, count]) => {
            const share = d && d.users ? Math.round((count / d.users) * 100) : 0;
            return (
              <div key={step} className="flex items-center gap-space-4">
                <span className="w-[190px] shrink-0 truncate text-[15px] leading-none text-text-80">{t(`admin.steps.${step}`)}</span>
                <span className="relative h-[22px] min-w-0 flex-1 overflow-hidden rounded-[8px] bg-grad-soft-10">
                  <span className="absolute inset-y-0 left-0 rounded-[8px] bg-accent" style={{ width: `${Math.max(share, count ? 2 : 0)}%` }} />
                </span>
                <span className="w-[52px] shrink-0 text-right text-[15px] leading-none text-text">{count}</span>
              </div>
            );
          })}
        </div>
      </section>

      {/* Прохождение: три метрики из ревью интерфейса — ожидание, «Пул», возвраты назад */}
      <section className="card-2 shrink-0 p-[40px]">
        <h2 className="text-[24px] font-[350] leading-none text-text">{t('flow.title')}</h2>
        <p className="mt-[8px] text-[14px] leading-[19px] text-text-60">{t('flow.hint')}</p>
        <div className="mt-[24px] grid gap-[16px] sm:grid-cols-2 lg:grid-cols-4">
          <StatTile
            label={t('flow.abandonRate')}
            value={`${f?.waitAbandonRate ?? 0}%`}
            hint={t('flow.ofSessions', { count: f?.waitSessions ?? 0 })}
          />
          <StatTile
            label={t('flow.abandonMedian')}
            value={`${f?.abandonMedianSeconds ?? 0} ${t('flow.sec')}`}
            hint={t('flow.waitMedian', { value: f?.waitMedianSeconds ?? 0 })}
          />
          <StatTile label={t('flow.poolMedian')} value={`${f?.poolMedianSeconds ?? 0} ${t('flow.sec')}`} />
          <StatTile
            label={t('flow.backClicks')}
            value={f?.backClicks ?? 0}
            hint={Object.entries(f?.backByStage ?? {}).map(([stage, count]) => `${t('flow.stage')} ${stage}: ${count}`).join(' · ') || undefined}
          />
        </div>
      </section>

      <section className="card-2 shrink-0 p-[40px]">
        <h2 className="text-[24px] font-[350] leading-none text-text">{t('delivery.journeys')}</h2>
        {(data?.journeys ?? []).length === 0 ? (
          <p className="mt-[20px] text-[16px] text-text-40">{t('delivery.noUsers')}</p>
        ) : (
          <div className="mt-[20px] overflow-x-auto">
            <table className="w-full min-w-[720px] border-collapse text-left">
              <thead>
                <tr className="text-[13px] text-text-40">
                  <th className="pb-[10px] font-[400]">{t('delivery.user')}</th>
                  <th className="pb-[10px] font-[400]">{t('delivery.stage')}</th>
                  <th className="pb-[10px] font-[400]">{t('delivery.gen')}</th>
                  <th className="pb-[10px] font-[400]">{t('delivery.post')}</th>
                  <th className="pb-[10px] font-[400]">{t('delivery.rate')}</th>
                  <th className="pb-[10px] font-[400]">{t('delivery.tier')}</th>
                  <th className="pb-[10px] font-[400]">{t('delivery.flags')}</th>
                  <th className="pb-[10px] font-[400]">{t('delivery.last')}</th>
                </tr>
              </thead>
              <tbody>
                {(data?.journeys ?? []).map((row) => (
                  <tr key={row.userId} className="border-t border-[rgba(246,245,253,0.08)]">
                    <td className="py-[12px] pr-[12px] text-[15px] text-text-80">{row.userId}</td>
                    <td className="py-[12px] pr-[12px] text-[15px] text-text">{t(`admin.steps.${row.stage}`)}</td>
                    <td className="py-[12px] pr-[12px] text-[15px] text-text-80">{row.generated}</td>
                    <td className="py-[12px] pr-[12px] text-[15px] text-text-80">{row.posted}</td>
                    <td className="py-[12px] pr-[12px] text-[15px]">
                      <span className={row.generated && row.postRate < 50 ? 'text-warning' : 'text-text'}>{row.postRate}%</span>
                    </td>
                    <td className="py-[12px] pr-[12px] text-[15px] text-text-80">{row.tier ?? t('delivery.free')}</td>
                    <td className="py-[12px] pr-[12px] text-[13px] text-text-40">
                      {[row.sawPricing && t('delivery.flagPricing'), row.hitLimit && t('delivery.flagLimit'),
                        row.failedGenerations > 0 && t('delivery.flagFail')].filter(Boolean).join(' · ') || '—'}
                    </td>
                    <td className="py-[12px] text-[13px] text-text-40">
                      {row.lastSeen ? new Date(row.lastSeen).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card-2 shrink-0 p-[40px]">
        <h2 className="text-[24px] font-[350] leading-none text-text">{t('admin.retention')}</h2>
        <p className="mt-[8px] text-[14px] leading-[19px] text-text-60">{t('admin.retentionHint')}</p>
        {(data?.retention ?? []).length === 0 ? (
          <p className="mt-[20px] text-[16px] text-text-40">{t('admin.noData')}</p>
        ) : (
          <div className="mt-[20px] overflow-x-auto">
            <table className="w-full min-w-[520px] border-collapse text-left">
              <thead>
                <tr className="text-[13px] text-text-40">
                  <th className="pb-[10px] font-[400]">{t('admin.cohort')}</th>
                  <th className="pb-[10px] font-[400]">{t('admin.users')}</th>
                  {[0, 1, 2, 3].map((week) => (
                    <th key={week} className="pb-[10px] font-[400]">{t('admin.week', { n: week })}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(data?.retention ?? []).map((row) => (
                  <tr key={row.cohort} className="border-t border-[rgba(246,245,253,0.08)]">
                    <td className="py-[12px] text-[15px] text-text-80">{row.cohort}</td>
                    <td className="py-[12px] text-[15px] text-text-80">{row.users}</td>
                    {row.percent.map((value, index) => (
                      <td key={index} className="py-[12px] text-[15px] text-text">
                        <span className="rounded-[6px] px-[8px] py-[4px]" style={{ background: `rgba(139,111,230,${Math.min(0.6, value / 160)})` }}>
                          {value}%
                        </span>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
