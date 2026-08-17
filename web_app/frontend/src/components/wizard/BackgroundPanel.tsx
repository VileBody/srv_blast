import { CSSProperties, PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useChip } from '../../i18n/useChip';
import { useToast } from '../../contexts/ToastContext';
import { api } from '../../lib/api';
import { cn } from '../../lib/cn';
import { HUE_GRADIENT, hueAt } from '../../lib/color';
import type { Vibe } from '../../lib/types';
import { SvgMaskIcon } from '../layout/SvgMaskIcon';
import { FigIcon } from '../ui/FigIcon';
import { InlineError, queryDown } from '../ui/ErrorState';
import { ChipIcon } from './HookPanel';
import { PillsFooter } from './WizardFrame';
import { PreviewPlayer } from '../ui/PreviewPlayer';
import { useFragmentAudio } from './useFragmentAudio';
import { SourcesModal } from './SourcesModal';
import { footageTypeKey, stepFootageType } from '../../data/footageTypes';
import { BackgroundMode, backgroundPills, backgroundVariations, useWizardStore } from '../../stores/wizardStore';

/** Стили фото (Figma W13/W30) — те же, что «стиль» у эффектов-хука */
const PHOTO_STYLES = ['Ксерокс', 'Глитч', 'Неон', 'Старая камера'];

export { backgroundVariations };

/*
 * Этап «Фон» (Figma W12 → 3 → 13 → 14 → 22): разделы настраиваются параллельно,
 * пилюли футера — живое отражение, «+» переводит к следующему разделу.
 */

const ACCENT = 'var(--accent-light)';
const WHITE80 = 'var(--text-80)';

/** Типы склеек (Figma W22) — для строба и стилизации фото */
export const GLUE_TYPES = [
  { id: 'snap-wipe', label: 'Snap Wipe', icon: '/assets/figma/glue-snapwipe.svg' },
  { id: 'minimax', label: 'Minimax', icon: '/assets/figma/glue-minimax.svg' },
  { id: 'extract', label: 'Extract', icon: '/assets/figma/glue-extract.svg' },
  { id: 'invert', label: 'Invert', icon: '/assets/figma/glue-invert.svg' }
];

/** Горизонтальный скролл: драг 1:1, колесо — плавно */
export function useDragScroll() {
  const ref = useRef<HTMLDivElement>(null);
  const drag = useRef({ active: false, moved: false, startX: 0, startScroll: 0 });

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!ref.current) return;
    drag.current = { active: true, moved: false, startX: e.clientX, startScroll: ref.current.scrollLeft };
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag.current.active || !ref.current) return;
    const dx = e.clientX - drag.current.startX;
    if (Math.abs(dx) > 5) {
      drag.current.moved = true;
      ref.current.scrollLeft = drag.current.startScroll - dx;
    }
  };
  const end = () => {
    setTimeout(() => { drag.current.active = false; drag.current.moved = false; }, 0);
  };
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      if (element.scrollWidth <= element.clientWidth + 1) return;
      const delta = Math.abs(event.deltaY) >= Math.abs(event.deltaX) ? event.deltaY : event.deltaX;
      if (!delta) return;
      event.preventDefault();
      element.scrollLeft += delta;
    };
    element.addEventListener('wheel', onWheel, { passive: false });
    return () => element.removeEventListener('wheel', onWheel);
  });

  return {
    ref,
    moved: () => drag.current.moved,
    handlers: { onPointerDown, onPointerMove, onPointerUp: end, onPointerLeave: end }
  };
}

/** Скролл-зависимые горизонтальные фейды: видны только когда есть контент за краем */
export function useScrollFades(ref: React.RefObject<HTMLDivElement>, deps: unknown[] = []) {
  const [fade, setFade] = useState({ left: false, right: false });
  const sync = () => {
    const el = ref.current;
    if (!el) return;
    setFade({ left: el.scrollLeft > 4, right: el.scrollLeft + el.clientWidth < el.scrollWidth - 4 });
  };
  useEffect(() => {
    sync();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { fade, sync };
}

export function TagIcon({ color, size = 20 }: { color: string; size?: number }) {
  return (
    <SvgMaskIcon
      src="/assets/figma/icon-tag.svg"
      style={{ width: size, height: size * 0.81, color, transform: 'rotate(-22.23deg)' }}
    />
  );
}

function BgSquaresIcon({ color }: { color: string }) {
  return (
    <span aria-hidden="true" className="relative inline-block h-[19px] w-[19px] shrink-0">
      <span className="absolute bottom-0 left-0 h-[11px] w-[11px] border border-dashed" style={{ borderColor: color }} />
      <span className="absolute right-0 top-0 h-[15px] w-[15px]" style={{ backgroundColor: color }} />
    </span>
  );
}

export function ArrowRight() {
  return (
    <svg viewBox="0 0 26 16" width="25" height="15" fill="none" aria-hidden="true">
      <path d="M1 8h22.5M17 1.5 24.5 8 17 14.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

const modes: { value: BackgroundMode; label: string; icon: string; w: number; h: number }[] = [
  { value: 'footage', label: 'wizard.bg.modeFootage', icon: '/assets/figma/icon-tag.svg', w: 20, h: 16 },
  { value: 'photo', label: 'wizard.bg.modePhoto', icon: '/assets/figma/icon-photo.svg', w: 21, h: 19 },
  { value: 'color', label: 'wizard.bg.modeColor', icon: '/assets/figma/icon-colorwheel.svg', w: 20, h: 20 }
];

export function ModeSwitch() {
  const { t } = useTranslation();
  const mode = useWizardStore((state) => state.background.mode);
  const setBackground = useWizardStore((state) => state.setBackground);
  const index = Math.max(0, modes.findIndex((m) => m.value === mode));
  return (
    <div className="mode-switch" role="tablist" aria-label={t('wizard.bg.typeAria')}>
      <span className="mode-switch-thumb" style={{ transform: `translateX(${index * 100}%)` }} aria-hidden="true" />
      {modes.map((item) => {
        const active = mode === item.value;
        const iconColor = active ? ACCENT : WHITE80;
        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={active}
            className={cn('mode-switch-btn', active && 'is-active')}
            onClick={() => setBackground({ mode: item.value })}
          >
            {item.value === 'footage' ? (
              <TagIcon color={iconColor} />
            ) : (
              <SvgMaskIcon src={item.icon} style={{ width: item.w, height: item.h, color: iconColor }} />
            )}
            {t(item.label)}
          </button>
        );
      })}
    </div>
  );
}

/**
 * Степпер типа футажей (Figma W12, 737:275): «‹ Личности ›» — текст 24 Book под grad-main,
 * стрелки 6.69×11.78 по краям зоны 429..580 в панели 620. Список из реестра, не хардкод.
 * ГОЧА: на W12 нарисован шаг «Личности», но по смыслу дефолт — «Стандартные» (registry.default).
 */
function FootageTypeStepper() {
  const { t } = useTranslation();
  const footageType = useWizardStore((state) => state.background.footageType);
  const setBackground = useWizardStore((state) => state.setBackground);
  const label = t(footageTypeKey(footageType));

  const arrow = (dir: -1 | 1) => (
    <button
      type="button"
      aria-label={dir === -1 ? t('wizard.bg.prevType') : t('wizard.bg.nextType')}
      onClick={() => setBackground({ footageType: stepFootageType(footageType, dir) })}
      className="flex h-[24px] w-[16px] shrink-0 items-center justify-center transition hover:brightness-125"
    >
      <SvgMaskIcon
        src="/assets/figma/bg-step-arrow.svg"
        style={{ width: 6.69, height: 11.78, color: ACCENT, transform: dir === -1 ? 'rotate(180deg)' : undefined }}
      />
    </button>
  );

  return (
    <span className="flex items-center gap-[15px]">
      {arrow(-1)}
      <span
        className="min-w-[120px] text-center text-[24px] font-[350] leading-normal text-transparent"
        style={{ backgroundImage: 'var(--grad-main)', WebkitBackgroundClip: 'text', backgroundClip: 'text' }}
      >
        {label}
      </span>
      {arrow(1)}
    </span>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (value: boolean) => void; label: string }) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} className="toggle" onClick={() => onChange(!checked)} />;
}

/**
 * Бокс под контент 1920×1440 (4:3), выровненный по целым пикселям.
 *
 * Высота карточек тянется из флекс-раскладки и приходит дробной (263.7px), из-за чего
 * ширина по aspect-ratio тоже дробная — реальное фото в таком боксе ложится с подпиксельным
 * масштабом: тонкие тёмные полосы по краям и лишний срез сверху/снизу от object-fit: cover.
 * Снапим высоту к кратной 3 — тогда ширина = h/3*4 целая, и кадр 4:3 садится ровно.
 */
/** Ширина фото-карточки: кратна 4, чтобы высота 4:3 (×3/4) вышла целым числом пикселей. */
const PHOTO_CARD_W = 348;

function MediaCard({ item, selected, wide, onToggle }: { item: Vibe; selected: boolean; wide?: boolean; onToggle: () => void }) {
  const chip = useChip();
  const [broken, setBroken] = useState(false);
  const isVideo = /\.(mp4|webm|mov)$/i.test(item.previewUrl);
  /*
   * Размер обеих карточек задаёт ВЫСОТА РЯДА, ширину выводит aspect-ratio: фото 4:3
   * (кадр 1920×1440), футаж — вертикаль 142:253.
   *
   * Ширину пробовали считать в JS от высоты ряда, чтобы она выходила целой и кадр не мылился
   * подпиксельным масштабом. Но пересчёт живёт на ResizeObserver и отстаёт: после смены
   * размера окна карточка оставалась от прежней высоты ряда и вылезала за него — сверху
   * обрезался угол вместе с меткой выбора. Чистый CSS пересчитывается синхронно с версткой
   * и разъехаться не может; возможная полупиксельная кромка — цена меньшая, чем битый ряд.
   */
  const sizing: CSSProperties = wide
    ? { height: '100%', aspectRatio: '4 / 3', flexShrink: 0 }
    : { aspectRatio: '142 / 253' };
  return (
    <button type="button" onClick={onToggle} aria-pressed={selected} className={cn('media-card', wide ? 'media-card--fit' : 'h-full')} style={sizing}>
      {!broken && (isVideo
        ? <video src={item.previewUrl} muted loop playsInline autoPlay onError={() => setBroken(true)} />
        : <img src={item.previewUrl} alt="" onError={() => setBroken(true)} />)}
      {broken && <span className="media-card-fallback">{chip(item.name)}</span>}
      {!broken && <span className="absolute bottom-space-2 left-0 right-0 z-[1] text-center text-[11px] text-text" style={{ textShadow: '0 1px 4px rgba(0,0,0,.8)' }}>{chip(item.name)}</span>}
      {selected && (
        <span
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 z-[2] rounded-r10"
          style={{ boxShadow: wide ? 'inset 0 0 0 2px var(--accent-light)' : 'inset 0 0 0 3px var(--accent)' }}
        />
      )}
      {/* Галочек на примерах нет ни у футажа, ни у фото, ни у субтитров: кружок в углу
          лип к скруглению, читался как артефакт и закрывал кадр. Выбор показывает обводка —
          у узких карточек она толще (3px), чтобы её точно было видно. */}
    </button>
  );
}

/* Выбор цвета (Figma W22): повторный клик по выбранному свотчу выключает цвет */
function ColorRow({ value, onPick }: { value?: string; onPick: (hex?: string) => void }) {
  const { t } = useTranslation();
  const [huePct, setHuePct] = useState(50);
  const barRef = useRef<HTMLDivElement>(null);

  const pickFromBar = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!barRef.current) return;
    const rect = barRef.current.getBoundingClientRect();
    const pct = Math.min(100, Math.max(0, ((e.clientX - rect.left) / rect.width) * 100));
    setHuePct(pct);
    onPick(hueAt(pct));
  };

  const onBarDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!barRef.current) return;
    try {
      barRef.current.setPointerCapture(e.pointerId);
    } catch {
      /* синтетический pointerId */
    }
    pickFromBar(e);
    barRef.current.onpointermove = ((ev: PointerEvent) => {
      if (ev.buttons) pickFromBar(ev as unknown as ReactPointerEvent<HTMLDivElement>);
    }) as never;
    barRef.current.onpointerup = () => {
      if (barRef.current) { barRef.current.onpointermove = null; barRef.current.onpointerup = null; }
    };
  };

  const custom = Boolean(value && value !== '#f6f5fd' && value !== '#05010f');

  return (
    <div className="flex items-center gap-[20px]">
      <button
        type="button"
        aria-label={t('wizard.bg.whiteBg')}
        className="h-[60px] w-[60px] shrink-0 rounded-r15 bg-[#f6f5fd] transition"
        style={{ boxShadow: value === '#f6f5fd' ? '0 0 0 2px var(--accent-light)' : undefined }}
        onClick={() => onPick(value === '#f6f5fd' ? undefined : '#f6f5fd')}
      />
      <button
        type="button"
        aria-label={t('wizard.bg.blackBg')}
        className="h-[60px] w-[60px] shrink-0 rounded-r15 bg-[#05010f] transition"
        style={{ boxShadow: value === '#05010f' ? '0 0 0 2px var(--accent-light)' : 'inset 0 0 0 1px var(--text-20)' }}
        onClick={() => onPick(value === '#05010f' ? undefined : '#05010f')}
      />
      <div
        ref={barRef}
        className="color-slider h-[60px] flex-1"
        style={{ background: HUE_GRADIENT }}
        onPointerDown={onBarDown}
        role="slider"
        aria-label={t('wizard.bg.colorBg')}
        aria-valuenow={Math.round(huePct)}
      >
        {custom && <span className="color-slider-thumb" style={{ left: `${huePct}%` }} />}
      </div>
    </div>
  );
}

export function StageBackground() {
  const { t } = useTranslation();
  const { push } = useToast();
  const background = useWizardStore((state) => state.background);
  const setBackground = useWizardStore((state) => state.setBackground);
  const toggleVibe = useWizardStore((state) => state.toggleVibe);
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: 15_000 });
  const vibesQuery = useQuery({ queryKey: ['vibes'], queryFn: api.vibes, enabled: background.mode === 'footage' });
  const photosQuery = useQuery({ queryKey: ['photos'], queryFn: api.photos, enabled: background.mode === 'photo' });
  const cardsScroll = useDragScroll();
  const gluesScroll = useDragScroll();
  const [sourcesOpen, setSourcesOpen] = useState(false);

  /*
   * Фото-карточки жили фиксированными 348×261, пока футажи тянулись во всю высоту ряда:
   * на высоком окне фото выглядели заметно мельче и с лишними полями. Считаем ширину от
   * реальной высоты ряда и округляем до кратного 4 — так 4:3 остаётся целым числом пикселей.
   */
  useEffect(() => {
    const row = cardsScroll.ref.current;
    if (!row) return;
    row.scrollTo({ left: 0, behavior: 'smooth' });
  }, [background.mode, background.footageType, cardsScroll.ref]);

  // Ссылка для загрузки с телефона (её же кодирует QR). Одноразовый токен выдаст бэкенд — эндпоинта пока нет.
  const shareUrl = `${window.location.origin}/upload`;

  const uploadSource = useMutation({
    mutationFn: api.uploadSource,
    onSuccess: (data) => {
      const uploads = background.uploads.includes(data.source.name) ? background.uploads : [...background.uploads, data.source.name];
      setBackground({ uploads });
      push({ variant: 'success', title: t('wizard.sources.uploaded'), text: data.source.name });
    },
    onError: () => push({ variant: 'error', title: t('wizard.sources.uploadFail') })
  });

  const onSourceFiles = (files: FileList | null) => {
    Array.from(files ?? []).forEach((file) => uploadSource.mutate(file));
  };

  const isMedia = background.mode !== 'color';
  const listQuery = background.mode === 'photo' ? photosQuery : vibesQuery;
  const list = background.mode === 'photo' ? photosQuery.data?.photos : vibesQuery.data?.vibes;
  const loading = listQuery.isLoading;
  const selected = background.mode === 'photo' ? background.photo : background.footage;

  const heading = loading && isMedia ? t('wizard.bg.headingShort') : t('wizard.bg.heading');
  const panelTitle = {
    footage: t('wizard.bg.typeFootage'),
    photo: t('wizard.bg.typePhoto'),
    color: t('wizard.bg.typeColor')
  }[background.mode];

  return (
    <div className="flex h-full flex-col">
      {/* Figma W12: слева заголовок, справа «Загрузить футажи» (иконка 20 + текст 24) */}
      <div className="flex items-center justify-between gap-space-4">
        <h2 className="wizard-h flex items-center gap-space-3">
          <BgSquaresIcon color="var(--accent-light)" />
          {heading}
        </h2>
        {isMedia && meQuery.data?.capabilities?.customSources && (
          <button
            type="button"
            onClick={() => setSourcesOpen(true)}
            className="wizard-body flex shrink-0 items-center gap-[10px] whitespace-nowrap transition hover:text-text"
          >
            <SvgMaskIcon src="/assets/figma/bg-upload.svg" style={{ width: 20, height: 20, color: WHITE80 }} />
            {background.mode === 'photo' ? t('wizard.bg.uploadPhoto') : t('wizard.bg.uploadFootage')}
          </button>
        )}
      </div>

      <div className="mt-[20px]">
        <ModeSwitch />
      </div>

      <SourcesModal
        open={sourcesOpen}
        shareUrl={shareUrl}
        onClose={() => setSourcesOpen(false)}
        onFiles={onSourceFiles}
      />

      <div className="relative mt-[40px] flex min-h-[382px] w-full flex-1 flex-col overflow-hidden rounded-r15 bg-grad-soft-10 pb-[40px] pt-[40px]">
        <div className="flex items-center justify-between px-[40px]">
          <span className="wizard-body">{panelTitle}</span>
          {/* Figma W12: у футажей на месте счётчика — степпер типа футажей */}
          {background.mode === 'footage' && <FootageTypeStepper />}
          {background.mode === 'photo' && (
            <span className="wizard-body flex items-center gap-space-3">
              <TagIcon color={ACCENT} />
              {t('wizard.videosCount', { count: selected.length })}
            </span>
          )}
          {background.mode === 'color' && background.color && (
            <button type="button" className="text-[15px] text-text-60 underline decoration-dotted underline-offset-4 transition hover:text-text" onClick={() => setBackground({ color: undefined })}>
              {t('wizard.bg.disable')}
            </button>
          )}
        </div>

        {isMedia ? (
          loading ? (
            <div className="flex flex-1 items-center justify-center">
              <span className="spinner !h-[48px] !w-[48px] !border-[3px] !border-accent-20 !border-t-accent-light" />
            </div>
          ) : queryDown(listQuery) ? (
            /* библиотека фонов не пришла — без этого экран оставался пустым молча */
            <div className="flex flex-1 items-center justify-center">
              <InlineError error={listQuery.error} offline={listQuery.fetchStatus === 'paused'} onRetry={() => listQuery.refetch()} retrying={listQuery.isFetching} />
            </div>
          ) : (
            <div className="relative mt-[12px] min-h-[253px] flex-1">
              <span className="scroll-fade-l" />
              <span className="scroll-fade-r" />
              <div
                ref={cardsScroll.ref}
                className="media-row cursor-grab select-none items-center gap-[20px] px-[40px] active:cursor-grabbing"
                {...cardsScroll.handlers}
              >
                {list?.map((item) => (
                  <MediaCard
                    key={item.id}
                    item={item}
                    wide={background.mode === 'photo'}
                    selected={selected.includes(item.name)}
                    onToggle={() => { if (!cardsScroll.moved()) toggleVibe(item.name); }}
                  />
                ))}
              </div>
            </div>
          )
        ) : (
          <div className="mt-[28px] flex flex-col gap-[40px]">
            <div className="px-[40px]">
              <ColorRow value={background.color} onPick={(hex) => setBackground({ color: hex })} />
            </div>
            <div className="flex items-center justify-between gap-space-4 px-[40px]">
              <span className="wizard-body flex items-center gap-space-3">
                <SvgMaskIcon src="/assets/figma/icon-strobe.svg" style={{ width: 20, height: 20, color: background.strobe ? ACCENT : WHITE80 }} />
                {t('wizard.bg.strobe')}
                <span className="ml-space-2">
                  <Toggle checked={background.strobe} onChange={(value) => setBackground({ strobe: value })} label={t('wizard.bg.strobe')} />
                </span>
              </span>
              <span className={cn('wizard-body transition-opacity', !background.strobe && 'opacity-40')}>{t('wizard.bg.glueType')}</span>
            </div>
            <div className="relative h-[60px]">
              <span className="scroll-fade-l !h-[60px]" />
              <span className="scroll-fade-r !h-[60px]" />
              <div
                ref={gluesScroll.ref}
                className="media-row cursor-grab select-none items-center gap-[20px] px-[40px] active:cursor-grabbing"
                {...gluesScroll.handlers}
              >
                {GLUE_TYPES.map((glue) => (
                  <button
                    key={glue.id}
                    type="button"
                    disabled={!background.strobe}
                    className={cn('glue-chip', background.glue === glue.id && background.strobe && 'is-selected')}
                    onClick={() => { if (!gluesScroll.moved()) setBackground({ glue: glue.id }); }}
                  >
                    <img src={glue.icon} width="40" height="40" alt="" aria-hidden="true" />
                    {glue.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** «+»: переход к следующему разделу фона (футажи → фото → цвет) */
const MODE_ORDER: BackgroundMode[] = ['footage', 'photo', 'color'];

export function BackgroundWorkZone({ ready, canContinue, loading, onBack, onNext }: { ready: boolean; canContinue: boolean; loading?: boolean; onBack: () => void; onNext: () => void }) {
  const { t } = useTranslation();
  const chip = useChip();
  const background = useWizardStore((state) => state.background);
  const setBackground = useWizardStore((state) => state.setBackground);
  const vibesQuery = useQuery({ queryKey: ['vibes'], queryFn: api.vibes, enabled: background.mode === 'footage' });
  const photosQuery = useQuery({ queryKey: ['photos'], queryFn: api.photos, enabled: background.mode === 'photo' });
  const [index, setIndex] = useState(0);
  const [broken, setBroken] = useState<Record<string, boolean>>({});
  const pillsScroll = useDragScroll();
  const stylesScroll = useDragScroll();
  const styleFades = useScrollFades(stylesScroll.ref, [background.photoEffects, background.mode]);

  const fragmentAudio = useFragmentAudio();
  const isMedia = background.mode !== 'color';
  const list = (background.mode === 'photo' ? photosQuery.data?.photos : vibesQuery.data?.vibes) ?? [];
  const selectedNames = background.mode === 'photo' ? background.photo : background.footage;
  const selected = list.filter((item) => selectedNames.includes(item.name));
  const safeIndex = selected.length ? Math.min(index, selected.length - 1) : 0;
  const current = selected.length ? selected[safeIndex] : null;
  const activeColor = background.mode === 'color' ? background.color : undefined;
  const variations = backgroundVariations(background);
  const pills = backgroundPills(background);
  const footerPills = pills.length > 0
    ? pills
    : [{ mode: background.mode, label: modes.find((item) => item.value === background.mode)?.label ?? 'wizard.bg.modeFootage', count: 0 }];

  const emptyText = { footage: t('wizard.bg.emptyFootage'), photo: t('wizard.bg.emptyPhoto'), color: t('wizard.bg.emptyColor') }[background.mode];
  const step = (delta: number) => {
    if (!selected.length) return;
    setIndex((safeIndex + delta + selected.length) % selected.length);
  };

  useEffect(() => setIndex(0), [background.mode, selectedNames.join('|')]);
  const isVideo = current ? /\.(mp4|webm|mov)$/i.test(current.previewUrl) : false;

  const nextMode = MODE_ORDER[MODE_ORDER.indexOf(background.mode) + 1];

  const fillStyle: CSSProperties | undefined = activeColor
    ? background.strobe
      ? ({ '--strobe-color': activeColor, animation: 'strobeFlicker 1s steps(1) infinite' } as CSSProperties)
      : { backgroundColor: activeColor }
    : undefined;

  /*
   * Управление живёт В САМОМ плеере: плей по центру и стрелки по бокам. Счётчик примеров
   * вернулся в шапку карточки компактной пилюлей (как на превью батча) — верхний ряд из
   * двух кнопок и двух чипов закрывал кадр и спорил с превью за внимание.
   *
   * Плей запускает выбранный отрывок трека поверх футажа: до этого фон выбирался «в тишине»,
   * и как он ляжет на музыку, человек узнавал только из готового ролика.
   */
  const playerProps = {
    playing: fragmentAudio.playing,
    onTogglePlay: fragmentAudio.available ? fragmentAudio.toggle : undefined,
    playLabel: t('wizard.bg.playTrack'),
    pauseLabel: t('wizard.bg.stopTrack'),
    onPrev: () => step(-1),
    onNext: () => step(1),
    showSteps: isMedia && selected.length > 1
  };

  const renderMedia = (item: Vibe) => (
    <>
      {!broken[item.id] && (isVideo
        ? <video key={item.id} className="h-full w-full object-cover" src={item.previewUrl} muted loop playsInline autoPlay onError={() => setBroken((b) => ({ ...b, [item.id]: true }))} />
        : <img key={item.id} className="h-full w-full object-cover" src={item.previewUrl} alt="" onError={() => setBroken((b) => ({ ...b, [item.id]: true }))} />)}
      {broken[item.id] && <div className="flex h-full w-full items-center justify-center bg-grad-card text-[15px] text-text-80">{chip(item.name)}</div>}
    </>
  );

  return (
    <aside className="wizard-aside flex min-h-0 shrink-0 flex-col gap-[20px] max-lg:w-full">
      <div className="card-2 flex min-h-0 flex-1 flex-col px-space-6 py-space-6 max-lg:px-space-5">
        <div className="mb-space-5 flex shrink-0 flex-nowrap items-center justify-between gap-space-3">
          <h2 className="wizard-h whitespace-nowrap">{t('wizard.workZone')}</h2>
          {/* Компактная пилюля «‹ N/M ›» — тот же элемент, что на превью батча. Листать
              можно и ей, и стрелками внутри плеера: она вспомогательная. */}
          {isMedia && selected.length > 0 ? (
            <div className="flex h-[30px] shrink-0 items-center gap-[10px] rounded-[15px] px-[12px]" style={{ background: 'var(--grad-whitey)' }}>
              <button type="button" aria-label={t('wizard.bg.prevExample')} onClick={() => step(-1)} disabled={selected.length < 2} className="flex items-center transition-opacity hover:opacity-60 disabled:opacity-30">
                <FigIcon name="home-arrow.svg" h={11} className="rotate-180" />
              </button>
              <span className="text-[16px] font-[350] leading-none text-accent">{safeIndex + 1}/{selected.length}</span>
              <button type="button" aria-label={t('wizard.bg.nextExample')} onClick={() => step(1)} disabled={selected.length < 2} className="flex items-center transition-opacity hover:opacity-60 disabled:opacity-30">
                <FigIcon name="home-arrow.svg" h={11} />
              </button>
            </div>
          ) : (
            /* «N вариаций» — внутренний термин: снаружи это просто счётчик выбранных фонов */
            <span className="wizard-body whitespace-nowrap">{t('wizard.bg.chosenCount', { count: variations })}</span>
          )}
        </div>

        {background.mode === 'photo' ? (
          // Figma W13: превью сверху, блок эффектов прижат к низу карточки
          <div className="subtle-scroll flex min-h-0 flex-1 flex-col justify-start gap-[20px] overflow-y-auto">
            {/* Превью 4:3 ужимается по доступной высоте (раньше оба блока были shrink-0 и на
                низком окне 720px сумма 426px выдавливала блок эффектов за карточку), но не
                мельче 180px: ниже кадр нечитаемый — тогда колонка уходит в прокрутку. */}
            {/* Высоту превью задаёт его ширина (ровно 4:3) — так кадр 1920×1440 не режется
                ни по одной оси. Если на низком окне столбец перестаёт вмещать превью вместе
                с блоком эффектов, он уходит в прокрутку (раньше блок эффектов выдавливало
                за карточку, потому что оба были shrink-0). */}
            <div className="flex shrink-0 justify-center">
              <PreviewPlayer
                key={current?.id ?? 'empty-photo'}
                className="w-full rounded-r15"
                {...playerProps}
                showSteps={playerProps.showSteps && Boolean(current)}
                onTogglePlay={current ? playerProps.onTogglePlay : undefined}
              >
                <div className="w-full" style={{ aspectRatio: '4 / 3' }}>
                  {current ? renderMedia(current) : (
                    <div className="flex h-full items-center justify-center bg-grad-soft-10">
                      <p className="wizard-body max-w-[223px] text-center">{emptyText}</p>
                    </div>
                  )}
                </div>
                <span className="dash-panel-plain pointer-events-none absolute inset-0 z-[3]" aria-hidden="true" />
              </PreviewPlayer>
            </div>
            <div className="shrink-0 rounded-r15 bg-grad-soft-10 p-space-5">
              <div className="flex items-center gap-space-3">
                <span className="wizard-body">✦ {t('wizard.bg.effects')}</span>
                <Toggle checked={background.photoEffects} onChange={(value) => setBackground({ photoEffects: value })} label={t('wizard.bg.effects')} />
              </div>
              <div className="mt-space-4 flex items-center gap-space-4">
                <span className={cn('wizard-body shrink-0 transition-opacity', !background.photoEffects && 'opacity-40')}>{t('wizard.bg.style')}</span>
                <div className="relative min-w-0 flex-1">
                  <div
                    ref={stylesScroll.ref}
                    className="media-row cursor-grab select-none items-center gap-[12px] active:cursor-grabbing"
                    style={{ height: 48 }}
                    onScroll={styleFades.sync}
                    {...stylesScroll.handlers}
                  >
                    {PHOTO_STYLES.map((style) => (
                      <button
                        key={style}
                        type="button"
                        disabled={!background.photoEffects}
                        className={cn('glue-chip !h-[48px] !gap-space-2 !pl-[6px] !pr-space-4 !text-[18px]', background.photoStyle === style && background.photoEffects && 'is-selected')}
                        onClick={() => { if (!stylesScroll.moved()) setBackground({ photoStyle: style }); }}
                      >
                        <span className="scale-[0.72]"><ChipIcon label={style} /></span>
                        {chip(style)}
                      </button>
                    ))}
                  </div>
                  {styleFades.fade.left && <span className="pointer-events-none absolute inset-y-0 left-0 z-[1] w-[26px]" style={{ background: 'linear-gradient(-90deg, rgba(30,22,53,0) 0%, #1e1635 92%)' }} />}
                  {styleFades.fade.right && <span className="pointer-events-none absolute inset-y-0 right-0 z-[1] w-[26px]" style={{ background: 'linear-gradient(90deg, rgba(30,22,53,0) 0%, #1e1635 92%)' }} />}
                </div>
              </div>
            </div>
          </div>
        ) : (
          <PreviewPlayer
            className={cn('min-h-0 flex-1 rounded-r15', !activeColor && 'bg-grad-soft-10')}
            {...playerProps}
            showSteps={playerProps.showSteps && Boolean(current)}
            onTogglePlay={isMedia && current ? playerProps.onTogglePlay : undefined}
          >
            <div className="absolute inset-0" style={fillStyle}>
              {background.mode === 'footage' && current && (
                <>
                  {renderMedia(current)}
                  <span className="absolute bottom-space-6 left-0 right-0 text-center text-[22px] text-text" style={{ textShadow: '0 1px 6px rgba(0,0,0,.8)' }}>{chip(current.name)}</span>
                </>
              )}
              {!current && !activeColor && (
                <div className="flex h-full items-center justify-center p-space-5">
                  <p className="wizard-body max-w-[223px] text-center">{emptyText}</p>
                </div>
              )}
            </div>
            <span className="dash-panel-plain pointer-events-none absolute inset-0 z-[3]" aria-hidden="true" />
          </PreviewPlayer>
        )}
      </div>

      <PillsFooter
        pills={footerPills.map((pill) => ({
          key: pill.mode,
          label: pill.label.startsWith('wizard.') ? t(pill.label) : chip(pill.label),
          // Figma W22: счётчик выбранных стейтов. Было «Хn» — читалось как код, а не как
          // «столько выбрано»; оставили голое число и подписали его в title.
          icon: <span className="text-[20px] font-[350] text-text-80" title={t('wizard.bg.pillCount', { count: pill.count })}>{pill.count}</span>
        }))}
        activeKey={background.mode}
        emptyLabel={t('wizard.bg.addNew')}
        onPill={(key) => setBackground({ mode: key as BackgroundMode })}
        onPlus={() => { if (nextMode) setBackground({ mode: nextMode }); }}
        plusDisabled={!nextMode}
        ready={ready}
        canContinue={canContinue}
        loading={loading}
        onBack={onBack}
        onNext={onNext}
        dragScroll={pillsScroll}
      />
    </aside>
  );
}
