import { PointerEvent as ReactPointerEvent, ReactNode, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { isVideoPosted, type VideoVersion } from '../../lib/types';
import { cn } from '../../lib/cn';
import { LimitsIndicator } from '../ui/LimitsIndicator';
import { FigIcon } from '../ui/FigIcon';
import { PreviewPlayer } from '../ui/PreviewPlayer';
import { useChip } from '../../i18n/useChip';
import { SvgMaskIcon } from '../layout/SvgMaskIcon';
import { api } from '../../lib/api';

/*
 * Общая оболочка батча: W36 (готовый батч) и W51 (идёт генерация) — ОДИН макет.
 * Слева шапка трека (батч-пилюли ↔ прогресс-бар) + «Генерации», справа превью + «К проектам».
 * Отличия W51: вместо пилюль батчей — прогресс, нет «Выложить все», снизу списка — строка-загрузка.
 */

/** Светлый градиент-заливка для текста (bg-clip-text), как в макетах W35–W37. */
export const gradLight = {
  backgroundImage: 'linear-gradient(184deg, #f6f5fd 8%, rgba(246,245,253,.8) 95%)',
  WebkitBackgroundClip: 'text',
  backgroundClip: 'text'
} as const;

/** Горизонтальная лента: мышью/тачем тянется, вертикальное колесо листает по горизонтали. */
function useHorizontalScroll() {
  const ref = useRef<HTMLDivElement>(null);
  const drag = useRef<{ active: boolean; moved: boolean; startX: number; startScroll: number; pointerId?: number }>({ active: false, moved: false, startX: 0, startScroll: 0 });
  const [fade, setFade] = useState({ left: false, right: false });

  const syncFades = () => {
    const element = ref.current;
    if (!element) return;
    setFade({
      left: element.scrollLeft > 4,
      right: element.scrollLeft + element.clientWidth < element.scrollWidth - 4
    });
  };

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    syncFades();
    const resize = new ResizeObserver(syncFades);
    resize.observe(element);
    const onWheel = (event: WheelEvent) => {
      if (element.scrollWidth <= element.clientWidth + 1) return;
      const delta = Math.abs(event.deltaY) >= Math.abs(event.deltaX) ? event.deltaY : event.deltaX;
      if (!delta) return;
      event.preventDefault();
      element.scrollLeft += delta;
    };
    element.addEventListener('wheel', onWheel, { passive: false });
    return () => {
      resize.disconnect();
      element.removeEventListener('wheel', onWheel);
    };
  }, []);

  /*
   * Захват указателя ставим только когда лента реально поехала. Захват на pointerdown
   * перенаправлял и последующий click на саму ленту — из-за этого пилюли батчей и «+»
   * переставали нажиматься.
   */
  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    const element = ref.current;
    if (!element) return;
    drag.current = { active: true, moved: false, startX: event.clientX, startScroll: element.scrollLeft, pointerId: event.pointerId };
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const element = ref.current;
    if (!drag.current.active || !element) return;
    const dx = event.clientX - drag.current.startX;
    if (Math.abs(dx) > 5) {
      if (!drag.current.moved) element.setPointerCapture?.(event.pointerId);
      drag.current.moved = true;
      element.scrollLeft = drag.current.startScroll - dx;
    }
  };
  const end = () => {
    const element = ref.current;
    if (drag.current.moved && drag.current.pointerId !== undefined) element?.releasePointerCapture?.(drag.current.pointerId);
    drag.current.active = false;
    window.setTimeout(() => { drag.current.moved = false; }, 0);
  };

  return {
    ref,
    fade,
    moved: () => drag.current.moved,
    handlers: { onPointerDown, onPointerMove, onPointerUp: end, onPointerCancel: end, onPointerLeave: end, onScroll: syncFades }
  };
}

function edgeMask(left: boolean, right: boolean, width = 24) {
  const leftStop = left ? width : 0;
  const rightStop = right ? width : 0;
  return `linear-gradient(to right, transparent 0, #000 ${leftStop}px, #000 calc(100% - ${rightStop}px), transparent 100%)`;
}

/** Чип-тег в строке генерации (h25, r5): иконбокс 25×25 + подпись (Figma 712:333/318/324). */
export function TagChip({ label, icon }: { label: string; icon: 'bg' | 'sub' | 'hook' }) {
  return (
    <span
      className="flex h-[25px] shrink-0 items-center rounded-[5px] pr-[8px] text-[16px] leading-none text-text-80"
      style={{ background: 'var(--grad-soft-20)' }}
    >
      <span
        className="flex h-[25px] w-[25px] shrink-0 items-center justify-center rounded-[5px] border border-accent-light"
        style={{ background: 'var(--grad-soft-20)' }}
      >
        {icon === 'sub' ? (
          <span className="text-[14px] font-[800] italic leading-none text-text-80">T</span>
        ) : (
          <FigIcon name={`pd-chip-${icon}.svg`} h={12} />
        )}
      </span>
      <span className="ml-[8px] translate-y-px whitespace-nowrap">{label}</span>
    </span>
  );
}

/** Строка генерации (620×60, #1d1534, r15): № + чипы + TikTok + скачивание (Figma W36). */
export function GenerationRow({ video, onPost }: { video: VideoVersion; onPost?: () => void }) {
  const { t } = useTranslation();
  const chip = useChip();
  // Опубликованный ролик выглядел ровно как неопубликованный: юзер не понимал, что уже ушло
  // в TikTok, а «Выложить все» молча пропускала выложенные.
  const posted = isVideoPosted(video);
  const chips = useHorizontalScroll();
  const chipsMask = edgeMask(chips.fade.left, chips.fade.right, 20);
  return (
    <div className={cn('relative flex h-[60px] shrink-0 items-center rounded-[15px] bg-[#1d1534] pl-[28px] pr-[24px]', posted && 'opacity-70')}>
      <span className="flex w-[110px] shrink-0 items-center gap-[8px] truncate text-[16px] leading-none text-text">
        <span className="translate-y-px truncate">{t('projectDetail.videoN', { n: video.index })}</span>
        {posted && <span className="h-[6px] w-[6px] shrink-0 rounded-full bg-success" aria-hidden="true" />}
      </span>
      <div
        ref={chips.ref}
        className="no-scrollbar mx-[20px] flex min-w-0 flex-1 cursor-grab select-none items-center gap-[10px] overflow-x-auto active:cursor-grabbing"
        style={{ maskImage: chipsMask, WebkitMaskImage: chipsMask }}
        {...chips.handlers}
      >
        {/* Через chip(): бакеты футажа и типы хуков хранятся по-русски (по ним матчит бэк),
            а показывать их надо на языке интерфейса. */}
        <TagChip icon="bg" label={chip(video.source)} />
        <TagChip icon="sub" label={chip(video.subtitleStyle)} />
        <TagChip icon="hook" label={chip(video.hook)} />
      </div>
      {/* Figma W36: звезда заменена на постинг в TikTok (18×20), скачивание рядом (gap 12) */}
      {video.status === 'FAILED' ? (
        <span className="ml-[8px] shrink-0 whitespace-nowrap text-[14px] leading-none text-warning" title={video.error ?? undefined}>
          {t('processing.failedShort')}
        </span>
      ) : posted ? (
        <span className="ml-[8px] flex shrink-0 items-center gap-[6px] whitespace-nowrap text-[14px] leading-none text-success" title={t('projectDetail.postedHint')}>
          <FigIcon name="pd-tiktok.svg" h={16} />
          {t('projectDetail.posted')}
        </span>
      ) : (
        <button type="button" onClick={onPost} disabled={!onPost} aria-label={t('projectDetail.postToTiktok')} className="ml-[8px] shrink-0 transition-opacity hover:opacity-70 disabled:cursor-not-allowed disabled:opacity-30">
          {/* размер как в кнопке «Выложить все» (Figma W36 строки: 18×20) */}
          <FigIcon name="pd-tiktok.svg" h={20} />
        </button>
      )}
      {/* download работает только для своего домена: у кросс-доменного S3-URL браузер
          атрибут игнорирует и открывает ролик во вкладке. Чтобы скачивание было настоящим,
          объекты в S3 должны отдаваться с Content-Disposition: attachment. */}
      <a
        href={video.downloadUrl ?? '#'}
        download=""
        onClick={() => { if (video.downloadUrl) void api.trackEvent('video_downloaded', { videoId: video.id }).catch(() => {}); }}
        aria-label={t('common.download')}
        className={`ml-[12px] shrink-0 transition-opacity hover:opacity-70 ${video.downloadUrl ? '' : 'pointer-events-none opacity-40'}`}
      >
        <FigIcon name="pd-download.svg" h={20} />
      </a>
    </div>
  );
}

/** Строка-загрузка W51: диагональные полосы мягко движутся под фейдом до появления готового ролика. */
export function LoadingRow({ video, active = true }: { video?: VideoVersion; active?: boolean }) {
  const { t } = useTranslation();
  return (
    <div className="batch-loading-row relative flex h-[60px] shrink-0 items-center overflow-hidden rounded-[15px] bg-[#1d1534] px-[28px]" role="status" aria-label={t('processing.rendering')}>
      {active && <span className="batch-loading-stripes absolute inset-0" aria-hidden="true" />}
      <span className="relative z-[1] text-[16px] text-text">{video ? t('projectDetail.videoN', { n: video.index }) : t('processing.rendering')}</span>
      <span className="relative z-[1] ml-auto text-[14px] text-text-60">
        {active ? t('processing.videoProgress', { progress: Math.max(1, Math.round(video?.progress ?? 1)) }) : t('processing.queued')}
      </span>
    </div>
  );
}

/**
 * Шапка трека. Внизу — пилюли батчей (W36) или прогресс генерации (W51).
 * Принимает строки, а не Project: на W51 шапка обязана рисоваться СРАЗУ (там прогресс),
 * ждать загрузки проекта и показывать скелетон нельзя — это читается как «зависло».
 */
export function TrackCard({
  title,
  artistNick,
  current,
  onMakeCurrent,
  children
}: {
  title?: string;
  artistNick?: string;
  /** undefined — статус текущего проекта неизвестен (грузится), метку не рисуем */
  current?: boolean;
  onMakeCurrent?: () => void;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <section className="card-2 h-[240px] shrink-0 px-[40px] pb-[35px] pt-[35px]">
      <div className="flex items-start justify-between gap-[20px]">
        <h1 className="min-w-0 truncate text-[32px] font-[400] leading-[38px] text-transparent" style={gradLight}>{title ?? t('projectDetail.trackFallback')}</h1>
        {/* Плашки «Текущий проект» здесь нет: ты и так внутри этого проекта, метка ничего
            не сообщала. Осталось только действие — сделать текущим, если он им не является. */}
        {current === false && onMakeCurrent && (
          <button
            type="button"
            onClick={onMakeCurrent}
            className="shrink-0 whitespace-nowrap rounded-[15px] border border-accent-light px-[14px] py-[6px] text-[14px] leading-none text-text-80 transition hover:text-text focus-visible:outline-none"
          >
            {t('projectDetail.makeCurrent')}
          </button>
        )}
      </div>
      <p className="mt-[12px] truncate text-[24px] font-[350] leading-[29px] text-transparent" style={gradLight}>{artistNick ?? t('projectDetail.artistFallback')}</p>
      <div className="mt-[31px]">{children}</div>
    </section>
  );
}

/** Переключатель сохранённых батчей проекта; «+» ведёт в визард на этап фона. */
export function BatchTrack({
  batches,
  selectedId,
  onSelect,
  onAddBatch
}: {
  batches: { id: string; number: number }[];
  selectedId?: string;
  onSelect: (id: string) => void;
  onAddBatch: () => void;
}) {
  const { t } = useTranslation();
  const scroll = useHorizontalScroll();
  const mask = edgeMask(scroll.fade.left, scroll.fade.right, 28);
  const lastBatchSelected = Boolean(batches.length && batches[batches.length - 1]?.id === selectedId);
  useEffect(() => {
    const rail = scroll.ref.current;
    if (!rail || !selectedId) return;
    const selected = Array.from(rail.querySelectorAll<HTMLElement>('[data-batch-id]'))
      .find((element) => element.dataset.batchId === selectedId);
    if (!selected) return;
    const isLast = batches[batches.length - 1]?.id === selectedId;
    if (isLast) {
      // У последнего батча сразу показываем и соседний «+», иначе он остаётся за краем.
      rail.scrollLeft = rail.scrollWidth - rail.clientWidth;
    } else {
      const left = selected.offsetLeft;
      const right = left + selected.offsetWidth;
      if (left < rail.scrollLeft) rail.scrollLeft = left;
      else if (right > rail.scrollLeft + rail.clientWidth) rail.scrollLeft = right - rail.clientWidth;
    }
    rail.dispatchEvent(new Event('scroll'));
  }, [batches.length, selectedId, scroll.ref]);
  return (
    <div
      ref={scroll.ref}
      className="no-scrollbar flex h-[60px] cursor-grab select-none items-stretch overflow-x-auto rounded-[15px] active:cursor-grabbing"
      style={{ background: 'var(--grad-soft-10)', maskImage: mask, WebkitMaskImage: mask }}
      {...scroll.handlers}
    >
      {(batches.length ? batches : [{ id: 'empty', number: 1 }]).map((batch) => {
        const selected = batches.length === 0 || batch.id === selectedId;
        return (
          <button
            key={batch.id}
            data-batch-id={batch.id}
            type="button"
            disabled={batches.length === 0}
            onClick={() => { if (!scroll.moved()) onSelect(batch.id); }}
            aria-pressed={selected}
            className={cn(
              'relative z-10 flex shrink-0 items-center whitespace-nowrap rounded-[15px] border-2 px-[21px] text-[24px] font-[350] leading-none transition',
              selected ? 'border-accent-light text-text' : 'border-transparent text-text-60 hover:text-text'
            )}
            style={{ background: selected ? '#34245d' : 'transparent' }}
          >
            {t('projectDetail.batchVideo', { n: batch.number })}
          </button>
        );
      })}
      <button
        type="button"
        onClick={() => { if (!scroll.moved()) onAddBatch(); }}
        aria-label={t('projects.addBatch')}
        className={cn(
          'relative z-0 flex shrink-0 items-center justify-center rounded-[15px] border-2 border-[var(--accent)] text-[24px] leading-none text-text-80 transition-[width,margin,padding,color] hover:text-text',
          lastBatchSelected
            ? '-ml-[33px] w-[78px] pl-[33px]'
            : 'ml-[12px] w-[48px] px-[12px]'
        )}
        style={{ background: 'var(--grad-soft-20)' }}
      >
        <span className="translate-y-[1px]" aria-hidden="true">+</span>
      </button>
    </div>
  );
}

/**
 * Прогресс-бар генерации (Figma W51 763:2953): трек 620×60 r15 grad-soft-10,
 * заливка = pct×620 под grad-main, подписи 16 по краям (паддинг 28).
 */
export function ProgressTrack({ done, total, minutesLeft }: { done: number; total: number; minutesLeft: number }) {
  const { t } = useTranslation();
  const pct = total ? Math.max(0, Math.min(1, done / total)) : 0;
  // Батч собран — «осталось N минут» превращается в враньё. Показываем итог.
  const finished = total > 0 && done >= total;
  return (
    <div className="relative flex h-[60px] items-center overflow-hidden rounded-[15px]" style={{ background: 'var(--grad-soft-10)' }}>
      <span
        aria-hidden="true"
        className="absolute inset-y-0 left-0 rounded-[15px] bg-grad-main transition-[width] duration-500"
        style={{ width: `${pct * 100}%` }}
      />
      <span className="relative z-[1] pl-[28px] text-[16px] leading-none text-text">{t('processing.progress', { done, total })}</span>
      <span className="relative z-[1] ml-auto pr-[28px] text-[16px] leading-none text-text">
        {finished ? t('processing.allDone') : t('processing.minutesLeft', { count: minutesLeft })}
      </span>
    </div>
  );
}

/** Карточка «Генерации» (Figma W36/W51). `postAll` — фокус-кнопка «Выложить все» (в W51 её нет). */
export function GenerationsCard({
  videos,
  postAll,
  postOne,
  onEmptyAction,
  loading,
  rating,
  onRate,
  ratingPending
}: {
  videos: VideoVersion[];
  postAll?: () => void;
  /** постинг одного ролика: индекс в списке (Figma W36 — иконка TikTok в строке) */
  postOne?: (video: VideoVersion) => void;
  /** Пустой триал не подделываем демо-роликами: ведём в реальный визард создания батча. */
  onEmptyAction?: () => void;
  loading?: boolean;
  rating?: number | string | null;
  onRate?: (rating: number) => void;
  ratingPending?: boolean;
}) {
  const { t } = useTranslation();
  const ready = videos.filter((video) => video.status === 'COMPLETED');
  const postedCount = ready.filter(isVideoPosted).length;
  const downloadable = videos.filter((video) => video.downloadUrl);
  const pending = videos.filter((video) => video.status === 'PENDING' || video.status === 'PROCESSING');
  const activePending = pending.find((video) => video.status === 'PROCESSING' || video.stage !== 'waiting_previous') ?? pending[0];
  // Браузер блокирует пачку одновременных скачиваний — разносим по времени
  const downloadAll = () => {
    void api.trackEvent('video_download_all', { videos: downloadable.length }).catch(() => {});
    downloadable.forEach((video, index) => {
      setTimeout(() => {
        const link = document.createElement('a');
        link.href = video.downloadUrl as string;
        link.download = '';
        document.body.appendChild(link);
        link.click();
        link.remove();
      }, index * 350);
    });
  };
  return (
    <section data-limits-dim className="card-2 relative flex min-h-0 flex-1 flex-col overflow-hidden p-[40px]">
      <div className="mb-[28px] flex items-center justify-between gap-space-4">
        <h2 className="shrink-0 text-[24px] font-[400] leading-none text-transparent" style={gradLight}>{t('projectDetail.generations')}</h2>
        {/* Figma W36: фокус-кнопка «Выложить все» + TikTok; справа кружок лимита (W47 — поповер) */}
        <span className="flex shrink-0 items-center gap-[20px]">
          <button
            type="button"
            onClick={() => postAll?.()}
            disabled={!postAll}
            className={cn(
              'flex h-[38px] shrink-0 items-center gap-[8px] whitespace-nowrap rounded-r10 border border-accent bg-grad-soft-20 px-[14px] text-[16px] font-[350] leading-none transition',
              postAll ? 'text-text-80 hover:text-text' : 'cursor-not-allowed text-text-40'
            )}
          >
            <FigIcon name="pd-tiktok.svg" h={20} />
            {postedCount > 0 && ready.length > 0
              ? t('projectDetail.postAllProgress', { done: postedCount, total: ready.length })
              : t('projectDetail.postAll')}
          </button>
          {/* Скачивание всего батча: раньше ролики можно было забрать только по одному */}
          <button
            type="button"
            onClick={downloadAll}
            disabled={!downloadable.length}
            className={cn(
              'flex h-[38px] shrink-0 items-center gap-[8px] whitespace-nowrap rounded-r10 border border-[rgba(246,245,253,0.2)] px-[14px] text-[16px] font-[350] leading-none transition',
              downloadable.length ? 'text-text-80 hover:border-accent-light hover:text-text' : 'cursor-not-allowed text-text-40'
            )}
          >
            <FigIcon name="pd-download.svg" h={18} />
            {t('projectDetail.downloadAll')}
          </button>
          <LimitsIndicator offsetY={28} />
        </span>
      </div>
      <div className="relative min-h-0 flex-1">
        <div
          className="no-scrollbar flex h-full flex-col gap-[20px] overflow-y-auto"
          /* Прячем строки прозрачностью, а не цветной накладкой. Тогда сквозь
             фейд всегда виден фактический многослойный фон card-2 без шва. */
          style={{
            maskImage: 'linear-gradient(to bottom, transparent 0, #000 2px, #000 calc(100% - 12px), transparent 100%)',
            WebkitMaskImage: 'linear-gradient(to bottom, transparent 0, #000 2px, #000 calc(100% - 12px), transparent 100%)',
          }}
        >
          {videos.length === 0 && !loading ? (
            <div className="flex h-full min-h-[160px] flex-col items-center justify-center gap-[20px] text-center">
              <p className="text-[16px] leading-[19px] text-text-60">{t('projectDetail.noGenerations')}</p>
              {onEmptyAction && (
                <button type="button" onClick={onEmptyAction} className="flex h-[60px] items-center justify-center rounded-r15 border border-accent-light bg-grad-soft-20 px-[28px] text-[20px] font-[350] leading-none text-text-80 transition hover:text-text">
                  {t('projectDetail.createBatch')}
                </button>
              )}
            </div>
          ) : (
            videos.map((v) => v.status === 'PENDING' || v.status === 'PROCESSING'
              ? <LoadingRow key={v.id} video={v} active={v.id === activePending?.id} />
              : <GenerationRow key={v.id} video={v} onPost={postOne && v.status === 'COMPLETED' ? () => postOne(v) : undefined} />)
          )}
          {loading && pending.length === 0 && <LoadingRow />}
        </div>
      </div>
      {onRate && (
        <div className="mt-[18px] flex shrink-0 items-center justify-between gap-[16px] border-t border-[rgba(246,245,253,0.12)] pt-[18px]">
          <span className="text-[15px] text-text-60">
            {rating ? t('projectDetail.ratingThanks') : t('projectDetail.ratingPrompt')}
          </span>
          <div className="flex h-[34px] shrink-0 overflow-hidden rounded-[8px] border border-accent-light" role="group" aria-label={t('projectDetail.ratingPrompt')}>
            {[1, 2, 3, 4, 5].map((value) => (
              <button
                key={value}
                type="button"
                disabled={ratingPending}
                aria-pressed={Number(rating) === value}
                onClick={() => onRate(value)}
                className={cn(
                  'h-[32px] w-[36px] border-r border-accent-light text-[15px] transition last:border-r-0 disabled:cursor-wait',
                  Number(rating) === value ? 'bg-accent text-white' : 'bg-grad-soft-10 text-text-80 hover:bg-grad-soft-20'
                )}
              >
                {value}
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

/** Правая колонка: превью видео + пагинация + кнопка «К проектам» (Figma 712:3). */
export function PreviewColumn({ videos, onBack }: { videos: VideoVersion[]; onBack: () => void }) {
  const { t } = useTranslation();
  const total = Math.max(1, videos.length);
  const [current, setCurrent] = useState(1);
  // листаем по кругу: на батче из 15 роликов упираться в край на каждом конце неудобно
  const step = (d: number) => setCurrent((c) => ((c - 1 + d + total) % total) + 1);
  const video = videos[current - 1];
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [previewError, setPreviewError] = useState(false);

  // смена ролика — всегда с начала и на паузе, иначе звук едет из предыдущего
  useEffect(() => {
    setPlaying(false);
    setPreviewError(false);
    const element = videoRef.current;
    if (element) element.pause();
  }, [current]);

  const togglePlay = () => {
    const element = videoRef.current;
    if (!element) return;
    if (playing) {
      element.pause();
      setPlaying(false);
      return;
    }
    void element.play();
    setPlaying(true);
    void api.trackEvent('video_previewed', { videoId: video?.id }).catch(() => {});
  };

  return (
    <aside className="wizard-aside card-2 flex shrink-0 flex-col p-[40px]">
      <div className="flex items-center justify-between gap-space-3">
        <h2 className="min-w-0 truncate text-[32px] font-[400] leading-none text-transparent" style={gradLight}>{t('projectDetail.previewVideo')}</h2>
        {/* Пилюля только когда есть что листать: на пустом проекте «1/1» обещала ролик,
            которого нет. */}
        {videos.length > 0 && (
          <div className="flex h-[30px] shrink-0 items-center gap-[10px] rounded-[15px] px-[12px]" style={{ background: 'var(--grad-whitey)' }}>
            <button type="button" aria-label={t('common.prev')} onClick={() => step(-1)} disabled={total < 2} className="flex items-center transition-opacity hover:opacity-60 disabled:opacity-30">
              <SvgMaskIcon src="/assets/figma/home-arrow.svg" style={{ width: 7, height: 11, color: 'var(--accent)', transform: 'rotate(180deg)' }} />
            </button>
            <span className="text-[16px] font-[350] leading-none text-accent">{current}/{total}</span>
            <button type="button" aria-label={t('common.next')} onClick={() => step(1)} disabled={total < 2} className="flex items-center transition-opacity hover:opacity-60 disabled:opacity-30">
              <SvgMaskIcon src="/assets/figma/home-arrow.svg" style={{ width: 7, height: 11, color: 'var(--accent)' }} />
            </button>
          </div>
        )}
      </div>
      {/* Кадр ролика с управлением внутри: раньше здесь была пустая белая панель, и
          посмотреть готовый ролик прямо на батче было нельзя — только скачать. */}
      <PreviewPlayer
        className="dash-panel-white mt-[28px] min-h-0 flex-1"
        playing={playing}
        onTogglePlay={video?.downloadUrl ? togglePlay : undefined}
        onPrev={() => step(-1)}
        onNext={() => step(1)}
        showSteps={total > 1}
      >
        {video && (video.playbackUrl || video.downloadUrl) && !previewError ? (
          <video
            ref={videoRef}
            key={video.id}
            src={video.playbackUrl ?? video.downloadUrl ?? undefined}
            poster={video.thumbnailUrl && !video.thumbnailUrl.endsWith('/cover-placeholder.svg') ? video.thumbnailUrl : undefined}
            playsInline
            preload="auto"
            onError={() => setPreviewError(true)}
            onEnded={() => setPlaying(false)}
            className="absolute inset-0 h-full w-full rounded-r15 bg-black object-contain"
          />
        ) : (
          <span className="absolute inset-0 flex flex-col items-center justify-center gap-[14px] px-space-5 text-center text-[16px] text-text-60">
            {/* Пустой проект и ролик без файла — разные вещи, и текст у них разный */}
            {previewError ? t('projectDetail.previewFailed') : videos.length === 0 ? t('projectDetail.previewEmpty') : t('projectDetail.videoN', { n: current })}
            {previewError && video?.downloadUrl && (
              <a href={video.downloadUrl} className="text-accent-light underline underline-offset-4">{t('common.download')}</a>
            )}
          </span>
        )}
      </PreviewPlayer>
      <button
        type="button"
        onClick={onBack}
        className="mt-[28px] flex h-[60px] items-center justify-center gap-[16px] whitespace-nowrap rounded-[15px] border-2 border-accent-light text-[24px] font-[350] leading-none text-text-80 transition hover:text-text"
        style={{ background: 'var(--grad-soft-20)' }}
      >
        {t('common.toProjects')}
        <FigIcon name="pd-arrow-right.svg" w={25} />
      </button>
    </aside>
  );
}

/**
 * Правая колонка на время генерации (W51). Раньше здесь стояла та же пустая панель превью,
 * что и на готовом батче: человек смотрел в белый прямоугольник и не понимал ни сколько ждать,
 * ни можно ли уйти. Теперь — «можно закрыть вкладку» (бот уже шлёт уведомления) и разбор
 * этапа активного ролика, который приходит от оркестратора.
 */
export function ProcessingAside({ done, total, activeVideo, renderFormat, telegram, onBack }: { done: number; total: number; activeVideo?: VideoVersion; renderFormat?: VideoVersion['format']; telegram: boolean; onBack: () => void }) {
  const { t } = useTranslation();
  const legacyFormat = activeVideo?.source.match(/(?:^|[·\s])(16:9|9:16|4:3|1:1)(?:$|[·\s])/)?.[1];
  const renderSize: Record<string, string> = {
    '9:16': '1080×1920', '16:9': '1920×1080', '4:3': '1920×1440', '1:1': '1080×1080',
  };
  const exactFormat = renderFormat ?? activeVideo?.format ?? legacyFormat;
  const size = exactFormat ? renderSize[exactFormat] : t('processing.renderSizePending');
  const steps = [1, 2, 3, 4, 5].map((n) => ({
    title: t(`processing.step${n}`),
    text: t(`processing.step${n}Text`, n === 5 ? { size } : undefined),
  }));
  // Оркестратор отдаёт этап активной вариации. Поэтому правая колонка сбрасывается
  // для каждого следующего ролика и больше не опережает строки слева по общему проценту батча.
  const stage = activeVideo?.stage ?? 'queued';
  const stageIndex: Record<string, number> = { queued: 0, build: 0, alignment: 1, dispatch: 2, render: 3, poll: 4, done: 4 };
  const active = activeVideo
    ? (stageIndex[stage] ?? Math.min(4, Math.floor(Math.max(0, activeVideo.progress) / 20)))
    : (total > 0 && done >= total ? 4 : 0);
  return (
    <aside className="wizard-aside card-2 flex shrink-0 flex-col overflow-hidden p-[40px]">
      <div className="flex shrink-0 items-center justify-between gap-[16px]">
        <h2 className="min-w-0 truncate text-[32px] font-[400] leading-none text-transparent" style={gradLight}>{t('processing.asideTitle')}</h2>
        {activeVideo && <span className="shrink-0 rounded-r10 bg-grad-soft-20 px-[12px] py-[7px] text-[14px] text-text-80">{t('processing.videoOf', { current: activeVideo.index, total })}</span>}
      </div>

      <div className="no-scrollbar mt-[28px] flex min-h-0 flex-1 flex-col gap-[10px] overflow-y-auto">
        {steps.map((step, index) => {
          const state = index < active ? 'done' : index === active ? 'now' : 'next';
          return (
            <div
              key={step.title}
              // на невысоком окне список шагов скроллится — держим текущий шаг в поле зрения
              ref={state === 'now' ? (node) => node?.scrollIntoView({ block: 'nearest' }) : undefined}
              className={cn(
                'shrink-0 rounded-r15 px-[20px] py-[12px] transition-colors',
                state === 'now' ? 'bg-grad-soft-20 shadow-[inset_0_0_0_1px_var(--accent-light)]' : 'bg-grad-soft-10'
              )}
            >
              <div className="flex items-center gap-[10px]">
                <span className={cn('h-[8px] w-[8px] shrink-0 rounded-full', state === 'done' ? 'bg-success' : state === 'now' ? 'bg-accent-light' : 'bg-[rgba(246,245,253,0.25)]')} aria-hidden="true" />
                <span className={cn('min-w-0 flex-1 truncate text-[18px] leading-none', state === 'next' ? 'text-text-60' : 'text-text')}>{step.title}</span>
                {state !== 'next' && (
                  <span className="shrink-0 whitespace-nowrap text-[13px] leading-none text-text-60">
                    {t(state === 'done' ? 'processing.stepDone' : 'processing.stepNow')}
                  </span>
                )}
              </div>
              <p className={cn('mt-[6px] text-[15px] leading-[18px]', state === 'next' ? 'text-text-40' : 'text-text-60')}>{step.text}</p>
            </div>
          );
        })}
      </div>

      {/* главное сообщение экрана: ждать необязательно */}
      <div className="mt-[20px] shrink-0 rounded-r15 border border-accent-light bg-grad-soft-10 px-[20px] py-[16px]">
        <p className="text-[16px] leading-none text-text">{t('processing.closeTabTitle')}</p>
        <p className="mt-[8px] text-[14px] leading-[18px] text-text-60">{t(telegram ? 'processing.closeTabText' : 'processing.closeTabTextNoBot')}</p>
      </div>

      <div className="mt-[10px] flex shrink-0 items-center justify-between gap-[16px] rounded-r15 bg-grad-soft-10 px-[20px] py-[14px]">
        <span className="min-w-0">
          <span className="block truncate text-[16px] leading-none text-text">{t('processing.guideTitle')}</span>
          <span className="mt-[6px] block text-[14px] leading-[18px] text-text-60">PDF · {t('processing.guideCaption')}</span>
        </span>
        <span className="flex shrink-0 items-center gap-[8px]">
          <a href="/assets/resources/blast-tiktok-guide.pdf" target="_blank" rel="noreferrer" onClick={() => { void api.trackEvent('guide_opened').catch(() => {}); }} className="rounded-r10 border border-[rgba(246,245,253,.18)] px-[12px] py-[8px] text-[14px] text-text-80 transition hover:border-accent-light hover:text-text">{t('common.view')}</a>
          <a href="/assets/resources/blast-tiktok-guide.pdf" download onClick={() => { void api.trackEvent('guide_downloaded').catch(() => {}); }} className="rounded-r10 border border-accent bg-grad-soft-20 px-[12px] py-[8px] text-[14px] text-text-80 transition hover:text-text">{t('common.download')}</a>
        </span>
      </div>

      <button
        type="button"
        onClick={onBack}
        className="mt-[20px] flex h-[60px] shrink-0 items-center justify-center gap-[16px] whitespace-nowrap rounded-[15px] border-2 border-accent-light text-[24px] font-[350] leading-none text-text-80 transition hover:text-text"
        style={{ background: 'var(--grad-soft-20)' }}
      >
        {t('common.toProjects')}
        <FigIcon name="pd-arrow-right.svg" w={25} />
      </button>
    </aside>
  );
}

/** Общий каркас страницы батча (W36/W51): две колонки, fill-height по сайдбару. */
export function BatchLayout({ left, right }: { left: ReactNode; right: ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-[20px] md:h-[var(--app-page-h)] md:flex-none md:flex-row md:py-[calc(var(--rail-pad-y)_-_var(--space-6))]">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-[25px]">{left}</div>
      {right}
    </div>
  );
}
