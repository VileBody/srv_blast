import { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { api, durationLabel } from '../../lib/api';
import { cn } from '../../lib/cn';
import type { TrackUsageEntry, TrackUsageFragment } from '../../lib/types';
import { Skeleton } from '../ui/Skeleton';
import { QueryError, queryDown } from '../ui/ErrorState';
import { FigIcon } from '../ui/FigIcon';

/*
 * «Куда ушли треки» — расшифровка шкалы «Треки» из «Лимитов».
 *
 * Шкала говорила только «n/m использовано», и было непонятно, какой трек что съел и почему
 * новый отрывок того же трека счётчик не двигает. Здесь — ровно то, что списано: трек,
 * дата списания и отрывки, которые из него сделаны (их генерации тратят уже «Генерации»).
 * Список, а не сетка карточек: это операционный экран, его сканируют сверху вниз.
 */

const COLLAPSED_LIMIT = 5;

function formatDate(iso: string | null | undefined, locale: string): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  // Год — только для прошлых лет: «12 сент. 2026 г.» в каждой строке списка — шум.
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString(locale, sameYear ? { day: 'numeric', month: 'short' } : { day: 'numeric', month: 'short', year: 'numeric' });
}

function FragmentRow({ fragment, locale }: { fragment: TrackUsageFragment; locale: string }) {
  const { t } = useTranslation();
  const span = fragment.from !== null && fragment.to !== null
    ? `${durationLabel(fragment.from)}–${durationLabel(fragment.to)}`
    : t('trackUsage.wholeTrack');
  const inWork = fragment.status === 'PENDING' || fragment.status === 'PROCESSING';
  return (
    <li className="grid grid-cols-[112px_minmax(0,1fr)_auto] items-baseline gap-x-[16px] py-[10px] text-[16px] leading-[20px] max-md:grid-cols-[88px_minmax(0,1fr)] max-md:gap-y-[4px]">
      <span className="tabular-nums text-text">{span}</span>
      <span className="min-w-0 truncate text-text-60">
        {fragment.projectId && fragment.projectName ? (
          <Link to={`/app/projects/${fragment.projectId}`} className="underline decoration-[rgba(246,245,253,0.25)] underline-offset-[3px] transition-colors hover:text-text hover:decoration-current">
            {fragment.projectName}
          </Link>
        ) : t('trackUsage.projectDeleted')}
        {fragment.createdAt ? <span className="text-text-40"> · {formatDate(fragment.createdAt, locale)}</span> : null}
      </span>
      <span className={cn('whitespace-nowrap text-right tabular-nums max-md:col-start-2 max-md:text-left', fragment.status === 'FAILED' ? 'text-[var(--warning)]' : 'text-text-60')}>
        {fragment.status === 'FAILED'
          ? t('trackUsage.failed')
          : inWork
            ? t('trackUsage.inWork', { count: fragment.videos })
            : t('trackUsage.videos', { count: fragment.videos })}
        {fragment.status !== 'FAILED' && fragment.videosFailed > 0 ? (
          <span className="text-[var(--warning)]"> · {t('trackUsage.failedPart', { count: fragment.videosFailed })}</span>
        ) : null}
      </span>
    </li>
  );
}

function TrackRow({ track, locale }: { track: TrackUsageEntry; locale: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const panelId = useId();
  // `inert` на свёрнутой панели: фокус табом по скрытым ссылкам на проекты был бы невидимым.
  // Атрибутом через ref — React 18 проп `inert` не знает.
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => { panel.current?.toggleAttribute('inert', !open); }, [open]);
  const videos = track.fragments.reduce((sum, item) => sum + item.videos, 0);
  const fromBot = track.source === 'bot';
  const expandable = track.fragments.length > 0;
  const spent = formatDate(track.spentAt, locale);
  return (
    <li className="border-t border-[rgba(246,245,253,0.1)] first:border-t-0">
      <button
        type="button"
        disabled={!expandable}
        aria-expanded={expandable ? open : undefined}
        aria-controls={expandable ? panelId : undefined}
        onClick={() => setOpen((value) => !value)}
        className="group flex w-full items-center gap-[16px] py-[18px] text-left enabled:cursor-pointer disabled:cursor-default"
      >
        <span className="flex h-[44px] w-[44px] shrink-0 items-center justify-center rounded-r15 bg-grad-soft-20" aria-hidden="true">
          <FigIcon name="pf-note.svg" h={18} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[20px] leading-[24px] text-text">
            {fromBot ? t('trackUsage.botTrack') : track.name || t('trackUsage.untitled')}
          </span>
          <span className="mt-[4px] block text-[14px] leading-[18px] text-text-40">
            {spent ? t('trackUsage.spentAt', { date: spent }) : t('trackUsage.spent')}
            {fromBot ? ` · ${t('trackUsage.botHint')}` : null}
          </span>
        </span>
        <span className="shrink-0 text-right text-[16px] leading-[20px] tabular-nums text-text-60 max-md:hidden">
          {expandable
            ? `${t('trackUsage.fragments', { count: track.fragments.length })} · ${t('trackUsage.videos', { count: videos })}`
            : fromBot ? null : t('trackUsage.noFragments')}
        </span>
        {expandable ? (
          <span
            aria-hidden="true"
            className={cn(
              'flex h-[32px] w-[32px] shrink-0 items-center justify-center rounded-full text-text-60 transition-[transform,color] duration-200 ease-[cubic-bezier(.16,1,.3,1)] group-hover:text-text',
              open && 'rotate-90'
            )}
          >
            <svg width="8" height="14" viewBox="0 0 8 14" fill="none"><path d="M1 1l6 6-6 6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
          </span>
        ) : null}
      </button>
      {expandable ? (
        /* Раскрытие через grid-rows 0fr→1fr: высота списка заранее не известна, а
           анимировать height:auto CSS не умеет. */
        <div
          id={panelId}
          ref={panel}
          className={cn(
            'grid transition-[grid-template-rows,opacity] duration-200 ease-[cubic-bezier(.16,1,.3,1)] motion-reduce:transition-none',
            open ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'
          )}
        >
          {/* minHeight инлайном, а не `min-h-0`: мобильный хак в index.css глобально
              возвращает этому классу min-height:auto и overflow:visible — панель не сворачивалась. */}
          <div className="overflow-hidden" style={{ minHeight: 0 }}>
            <ul className="mb-[18px] ml-[60px] divide-y divide-[rgba(246,245,253,0.06)] rounded-r15 bg-[rgba(16,9,34,.35)] px-[20px] max-md:ml-0">
              {track.fragments.map((fragment) => <FragmentRow key={fragment.jobId} fragment={fragment} locale={locale} />)}
            </ul>
          </div>
        </div>
      ) : null}
    </li>
  );
}

export function TrackUsageCard() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language.startsWith('en') ? 'en-GB' : 'ru-RU';
  const [showAll, setShowAll] = useState(false);
  const query = useQuery({ queryKey: ['track-usage'], queryFn: api.trackUsage });

  if (query.data?.billingLinkRequired) return null;

  const tracks = query.data?.tracks ?? [];
  const visible = showAll ? tracks : tracks.slice(0, COLLAPSED_LIMIT);

  return (
    <section className="card-2 shrink-0 p-[40px] max-md:p-[20px]" aria-labelledby="track-usage-title">
      <h2 id="track-usage-title" className="text-[24px] font-[400] leading-[29px] text-text">{t('trackUsage.title')}</h2>
      <p className="mt-[8px] max-w-[720px] text-[16px] leading-[22px] text-text-60">{t('trackUsage.explain')}</p>

      <div className="mt-[24px]">
        {queryDown(query) ? (
          <QueryError query={query} className="min-h-[160px]" />
        ) : query.isLoading ? (
          <Skeleton className="h-[160px]" />
        ) : tracks.length === 0 ? (
          <p className="rounded-r15 bg-[rgba(16,9,34,.35)] px-[20px] py-[18px] text-[16px] leading-[22px] text-text-60">{t('trackUsage.empty')}</p>
        ) : (
          <>
            <ul>
              {visible.map((track) => <TrackRow key={track.id} track={track} locale={locale} />)}
            </ul>
            {tracks.length > COLLAPSED_LIMIT ? (
              <button type="button" className="soft-btn mt-[8px] h-[44px] px-[18px] text-[16px]" onClick={() => setShowAll((value) => !value)}>
                {showAll ? t('trackUsage.showLess') : t('trackUsage.showAll', { count: tracks.length })}
              </button>
            ) : null}
          </>
        )}
      </div>
    </section>
  );
}
