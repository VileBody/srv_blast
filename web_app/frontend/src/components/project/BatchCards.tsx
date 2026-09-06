import { PointerEvent as ReactPointerEvent, ReactNode, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { isVideoPosted, type VideoVersion } from '../../lib/types';
import { cn } from '../../lib/cn';
import { LimitsIndicator } from '../ui/LimitsIndicator';
import { FigIcon } from '../ui/FigIcon';
import { PreviewPlayer } from '../ui/PreviewPlayer';
import { useChip } from '../../i18n/useChip';

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
  const drag = useRef({ active: false, moved: false, startX: 0, startScroll: 0 });
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

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    const element = ref.current;
    if (!element) return;
    drag.current = { active: true, moved: false, startX: event.clientX, startScroll: element.scrollLeft };
    element.setPointerCapture?.(event.pointerId);
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const element = ref.current;
    if (!drag.current.active || !element) return;
    const dx = event.clientX - drag.current.startX;
    if (Math.abs(dx) > 5) {
      drag.current.moved = true;
      element.scrollLeft = drag.current.startScroll - dx;
    }
  };
  const end = () => {
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
        aria-label={t('common.download')}
        className={`ml-[12px] shrink-0 transition-opacity hover:opacity-70 ${video.downloadUrl ? '' : 'pointer-events-none opacity-40'}`}
      >
        <FigIcon name="pd-download.svg" h={20} />
      </a>
    </div>
  );
}

/** Строка-загрузка W51: диагональные полосы мягко движутся под фейдом до появления готового ролика. */
export function LoadingRow() {
  const { t } = useTranslation();
  return (
    <div className="batch-loading-row relative h-[60px] shrink-0 overflow-hidden rounded-[15px] bg-[#1d1534]" role="status" aria-label={t('processing.rendering')}>
      <span className="batch-loading-stripes absolute inset-y-0 left-[-35%] w-[170%]" aria-hidden="true" />
      <span className="pointer-events-none absolute inset-0 bg-[linear-gradient(90deg,#1d1534_0%,rgba(29,21,52,0.08)_18%,rgba(29,21,52,0.08)_82%,#1d1534_100%)]" aria-hidden="true" />
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
              'relative z-10 flex shrink-0 items-center whitespace-nowrap rounded-[15px] border-2 px-[21px] text-[24px] font-[350] leading-none transition [backdrop-filter:blur(40px)]',
              selected ? 'border-accent-light text-text' : 'border-transparent text-text-60 hover:text-text'
            )}
            style={{ background: selected ? 'var(--grad-soft-20)' : 'transparent' }}
          >
            {t('projectDetail.batchVideo', { n: batch.number })}
          </button>
        );
      })}
      <button
        type="button"
        onClick={() => { if (!scroll.moved()) onAddBatch(); }}
        aria-label={t('projects.addBatch')}
        className="relative z-0 -ml-[2px] flex w-[78px] shrink-0 items-center justify-center rounded-[15px] border-2 border-[var(--accent)] text-[24px] leading-none text-text-80 transition hover:text-text"
        style={{ background: 'var(--grad-soft-20)' }}
      >
        +
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
  // Браузер блокирует пачку одновременных скачиваний — разносим по времени
  const downloadAll = () => {
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
        <div className="no-scrollbar flex h-full flex-col gap-[20px] overflow-y-auto">
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
            videos.map((v) => (
              <GenerationRow key={v.id} video={v} onPost={postOne && v.status === 'COMPLETED' ? () => postOne(v) : undefined} />
            ))
          )}
          {loading && <LoadingRow />}
        </div>
        {/* скролл-фейды сверху/снизу (цвет карты) */}
        <div className="pointer-events-none absolute inset-x-0 -top-[10px] h-[24px]" style={{ background: 'linear-gradient(180deg, #140e24, rgba(20,14,36,0))' }} />
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-[24px]" style={{ background: 'linear-gradient(0deg, #140e24, rgba(20,14,36,0))' }} />
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
              <FigIcon name="home-arrow.svg" h={11} className="rotate-180" />
            </button>
            <span className="text-[16px] font-[350] leading-none text-accent">{current}/{total}</span>
            <button type="button" aria-label={t('common.next')} onClick={() => step(1)} disabled={total < 2} className="flex items-center transition-opacity hover:opacity-60 disabled:opacity-30">
              <FigIcon name="home-arrow.svg" h={11} />
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
            poster={video.thumbnailUrl ?? undefined}
            playsInline
            preload="metadata"
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
 * ни можно ли уйти. Теперь — «можно закрыть вкладку» (бот уже шлёт уведомления) и разбор,
 * что именно сейчас делают с треком; текущий шаг считаем от доли готовых роликов.
 */
export function ProcessingAside({ done, total, telegram, onBack }: { done: number; total: number; telegram: boolean; onBack: () => void }) {
  const { t } = useTranslation();
  const steps = [1, 2, 3, 4, 5].map((n) => ({ title: t(`processing.step${n}`), text: t(`processing.step${n}Text`) }));
  // первые два шага — разбор трека, он общий на батч; дальше шаги идут по мере готовности роликов
  const ratio = total > 0 ? done / total : 0;
  const active = done === 0 ? Math.min(1, steps.length - 1) : Math.min(steps.length - 1, 2 + Math.floor(ratio * (steps.length - 2)));
  return (
    <aside className="wizard-aside card-2 flex shrink-0 flex-col overflow-hidden p-[40px]">
      <h2 className="shrink-0 truncate text-[32px] font-[400] leading-none text-transparent" style={gradLight}>{t('processing.asideTitle')}</h2>

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
        <p className="text-[18px] leading-none text-text">{t('processing.closeTabTitle')}</p>
        <p className="mt-[8px] text-[15px] leading-[19px] text-text-60">{t(telegram ? 'processing.closeTabText' : 'processing.closeTabTextNoBot')}</p>
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
