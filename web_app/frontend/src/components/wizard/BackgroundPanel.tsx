import { CSSProperties, ReactNode, useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { create } from 'zustand';
import { useChip } from '../../i18n/useChip';
import { api } from '../../lib/api';
import { catalogPosterOf, isVideoUrl } from '../../lib/media';
import { cn } from '../../lib/cn';
import { HUE_GRADIENT, hueAt } from '../../lib/color';
import { nearestHuePercent } from '../../lib/huePosition';
import type { Vibe } from '../../lib/types';
import { SvgMaskIcon } from '../layout/SvgMaskIcon';
import { InlineError, queryDown } from '../ui/ErrorState';
import { ChipIcon, EffectPreview, previewIdFor } from './hookCatalog';
import { PreviewVideo } from './CatalogPreview';
import { PAUSE, PLAY, PillsFooter, Svg, W12 } from './WizardFrame';
import { useFragmentAudio } from './useFragmentAudio';
import { SourcesModal } from './SourcesEditor';
import { useTried } from './wizardAttempt';
import { ActionGuideOverlay } from '../guidance/ActionGuideOverlay';
import { useGuideDismiss, useMarkGuideSeen } from '../guidance/useGuideDismiss';
import { useScrollGuideIntoView } from '../guidance/useScrollGuideIntoView';
import { FOOTAGE_TYPES, footageTypeKey, footageTypePlane } from '../../data/footageTypes';
import effectsRegistry from '../../data/effects-registry.json';
import { BackgroundMode, backgroundVariations, useWizardStore } from '../../stores/wizardStore';

/* Цвета фона — данные (значение, уходящее в рендер), а не стиль интерфейса */
// ui-allow: коды белого и чёрного фона
const WHITE_BG = '#f6f5fd';
// ui-allow: коды белого и чёрного фона
const BLACK_BG = '#05010f';

/** Стили фото (Figma W13/W30) — те же, что «стиль» у эффектов-хука */
const PHOTO_STYLES = ['Ксерокс', 'Глитч', 'Неон', 'Старая камера'];

export { backgroundVariations };

/*
 * Этап «Фон» (концепт «Трек / Фон», wizard12 v3): три режима видны сразу и несут счётчики,
 * типы футажей — вкладками, а не скрытым степпером; выбор нумерует карточки; превью справа
 * листается пейджером внутри плеера и играет под отрывок; внизу — итог по режимам.
 */

// ui-allow: палитра иллюстраций гайдов
const GUIDE_TONES = [
  // ui-allow: палитра иллюстраций гайдов
  'from-[#8b6fe6] to-[#342553]',
  // ui-allow: иллюстрация гайда
  'from-[#42627b] to-[#172331]',
  // ui-allow: иллюстрация гайда
  'from-[#8a526d] to-[#2a1823]'
];

function GuideFrame({ index, selected, landscape = false, large = false }: { index: number; selected?: boolean; landscape?: boolean; large?: boolean }) {
  return (
    <span className={cn(
      'relative block overflow-hidden rounded-[7px] bg-gradient-to-b',
      landscape ? 'h-[25px] w-[40px]' : large ? 'h-[60px] w-[36px]' : 'h-[50px] w-[30px]',
      GUIDE_TONES[index % GUIDE_TONES.length],
      selected && 'ring-2 ring-inset ring-accent-light'
    )}>
      <span className="absolute inset-x-[5px] bottom-[6px] h-[2px] rounded-full bg-white/35" />
      {selected && <span className="absolute right-[4px] top-[4px] h-[5px] w-[5px] rounded-full bg-accent-light" />}
    </span>
  );
}

function BackgroundModeGuideVisual() {
  // Без стрелки (она никого не убеждала). Отступ между рядом иконок и контейнером
  // роликов равен отступу МЕЖДУ иконками — единая сетка, не два случайных числа.
  // Раскадровка одноразовая (guide-mode-frame/-delay-N, не луп): все три пила стартуют
  // выключенными, первые два включаются по очереди, ролики проявляются вместе с ними.
  const icons = [
    { src: '/assets/figma/icon-tag.svg', tag: true },
    { src: '/assets/figma/icon-photo.svg', tag: false },
    { src: '/assets/figma/icon-colorwheel.svg', tag: false }
  ];
  // Тайминги: у каждого пила ДВА слоя (дашед-«выключен» / сплошной-«включён»),
  // кросс-фейдятся — дашед реально исчезает, а не остаётся торчать под сплошным.
  // Иконки разнесены на 600ms (не соседние delay-N — то было слишком быстро и
  // читалось как «сразу оба фиолетовые»), ролики стартуют вместе со ВТОРОЙ иконкой.
  const iconDelays = [150, 750];
  return (
    <div className="mx-auto flex w-full max-w-[190px] flex-col items-center gap-[10px] overflow-hidden" aria-hidden="true">
      <span className="grid w-full grid-cols-3 gap-[10px]">
        {icons.map((icon, index) => (
          <span key={icon.src} className="relative flex h-[32px] items-center justify-center overflow-hidden rounded-[8px]">
            <span className="absolute inset-0 rounded-[8px] border border-dashed border-white/20 bg-white/[0.03]" style={index < 2 ? { animation: `guide-mode-off-fade 320ms cubic-bezier(.16,1,.3,1) ${iconDelays[index]}ms both` } : undefined} />
            {index < 2 && (
              <span /* ui-allow: иллюстрация гайда */ className="absolute inset-0 rounded-[8px] bg-[#6850b7] ring-1 ring-inset ring-white/30" style={{ animation: `guide-mode-on-fade 320ms cubic-bezier(.16,1,.3,1) ${iconDelays[index]}ms both` }} />
            )}
            <span className="relative z-[1]">
              {/* ui-allow: иллюстрация гайда */}
              {icon.tag ? <TagIcon color="rgba(255,255,255,.92)" size={13} /> : <SvgMaskIcon src={icon.src} style={{ width: 13, height: 13, color: 'rgba(255,255,255,.92)' }} />}
            </span>
          </span>
        ))}
      </span>
      <span className="relative h-[68px] w-full overflow-hidden rounded-[10px] border border-white/25 bg-black/15">
        <span className="absolute left-1/2 top-[19px] h-[60px] w-[74px] -translate-x-1/2">
          <span className="guide-mode-frame absolute left-0 top-[4px] scale-[1.15] opacity-45" style={{ animationDelay: '760ms' }}><GuideFrame index={2} /></span>
          <span className="guide-mode-frame absolute left-[18px] top-[2px] scale-[1.15] opacity-75" style={{ animationDelay: '880ms' }}><GuideFrame index={1} /></span>
          {/* Реюз once-реавила и infinite-пульса на одном transform дерётся за свойство —
              вложенный span даёт каждому своё: снаружи разовое появление, внутри вечное дыхание. */}
          <span className="guide-mode-frame absolute left-[36px] top-0 scale-[1.15]" style={{ animationDelay: '1000ms' }}><span className="guide-pulse"><GuideFrame index={0} selected /></span></span>
        </span>
      </span>
    </div>
  );
}

function BackgroundPoolGuideVisual() {
  return (
    <div className="mx-auto flex w-full max-w-[100px] flex-col items-center gap-[7px] overflow-visible" aria-hidden="true">
      {/* Галочка — по центру чипа, не в углу (правка ревью: сбоку читалась плохо). */}
      <span className="flex h-[33px] w-[88px] items-center justify-center gap-[5px] rounded-[9px] border border-white/25 bg-black/15 px-[7px] py-[6px]">
        <span className={cn('guide-pulse relative flex h-[19px] w-[28px] items-center justify-center rounded-[6px] bg-gradient-to-br ring-1 ring-inset ring-white/45', GUIDE_TONES[2])}>
          <span className="text-[9px] leading-none text-white">✓</span>
        </span>
        <span className="h-[19px] w-[28px] rounded-[6px] border border-dashed border-white/25 bg-white/[0.03]" />
      </span>
      {/* Карусель роликов, без стрелки: три пила крутятся по кругу — центр уезжает
          влево-и-мельчает, правый вырастает в центр, левый встаёт на место правого.
          Один кейфрейм на всех трёх (guide-carousel), фаза сдвинута на треть периода
          через отрицательный animation-delay — тот же приём, что у кросс-фейда иконок хука. */}
      <div className="relative flex h-[58px] w-full items-center justify-center" aria-hidden="true">
        {GUIDE_TONES.map((tone, index) => (
          <span
            key={index}
            className={cn('guide-carousel absolute left-1/2 top-1/2 flex h-[46px] w-[32px] items-center justify-center overflow-hidden rounded-[7px] bg-gradient-to-b shadow-[0_8px_16px_rgba(5,1,15,.4)] ring-1 ring-inset ring-white/50', tone)}
            style={{ animationDelay: `${index * -1.5}s` }}
          >
            <img src="/assets/figma/btn-play.svg" alt="" className="h-[11px] w-[11px]" />
          </span>
        ))}
      </div>
    </div>
  );
}

/** Типы склеек (Figma W22) — для строба и стилизации фото */
export const GLUE_TYPES = effectsRegistry.glue
  .filter((effect) => Boolean(effect.altId))
  .map((effect) => ({ id: effect.altId as string, label: effect.label }));

export { useDragScroll } from './useDragScroll';
import { useDragScroll } from './useDragScroll';

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

/* ── шаг «Фон» — разметка по макету wizard12 v3 ── */

/** Иконка склейки/стиля из продукта (круг 40) в кружке пилюли макета (28). */
export function ChipBadge({ label }: { label: string }) {
  return (
    <span className="w12-gicon" aria-hidden="true">
      <span className="w12-gicon-scale"><ChipIcon label={label} /></span>
    </span>
  );
}

/** Что человек навёл в списке склеек/стилей — это же играет примером в превью справа. */
const useBgHover = create<{ example: string | null; set: (id: string | null) => void }>((set) => ({ example: null, set: (example) => set({ example }) }));

export function ArrowRight() {
  return <Svg>{W12.arrow}</Svg>;
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (value: boolean) => void; label: string }) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} className="w12-switch" onClick={() => onChange(!checked)} />;
}

const modes: { value: BackgroundMode; label: string; icon: ReactNode }[] = [
  { value: 'footage', label: 'wizard.bg.modeFootage', icon: <span className="w12-mi w12-cap w12-heavy" aria-hidden="true" style={{ '--m': 'url(/assets/wizard/ic-tag.svg)', '--r': 1.24, transform: 'rotate(-22deg)' } as React.CSSProperties} /> },
  { value: 'photo', label: 'wizard.bg.modePhoto', icon: <span className="w12-mi w12-cap" aria-hidden="true" style={{ '--m': 'url(/assets/wizard/ic-photo.svg)', '--r': 1.1 } as React.CSSProperties} /> },
  { value: 'color', label: 'wizard.bg.modeColor', icon: <span className="w12-mi w12-cap w12-heavy" aria-hidden="true" style={{ '--m': 'url(/assets/wizard/ic-colorwheel.svg)' } as React.CSSProperties} /> }
];

/** Сколько выбрано в каждом режиме: свои видео идут в «Футажи». */
function modeCounts(bg: ReturnType<typeof useWizardStore.getState>['background']): Record<BackgroundMode, number> {
  return { footage: bg.footage.length + bg.sourceVideos.length, photo: bg.photo.length, color: bg.color ? 1 : 0 };
}

/* ── тип подборки футажей: выпадающий список — типов станет больше, чем влезает в ряд ── */
const OWN_TYPE = '__own__';
function TypeMenu({ label, value, options, onChange }: { label: string; value: string; options: { id: string; label: string }[]; onChange: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const wrapRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const listId = useId();
  const current = options.find((option) => option.id === value) ?? options[0];
  useEffect(() => {
    if (!open) return undefined;
    setActive(Math.max(0, options.findIndex((option) => option.id === value)));
    const onDown = (event: PointerEvent) => { if (!wrapRef.current?.contains(event.target as Node)) setOpen(false); };
    window.addEventListener('pointerdown', onDown);
    return () => window.removeEventListener('pointerdown', onDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  const pick = (id: string) => { onChange(id); setOpen(false); buttonRef.current?.focus(); };
  const onKey = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape' && open) { event.preventDefault(); setOpen(false); return; }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!open) { setOpen(true); return; }
      setActive((index) => (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length);
      return;
    }
    if ((event.key === 'Enter' || event.key === ' ') && open) { event.preventDefault(); pick(options[active].id); }
  };
  return (
    <div ref={wrapRef} className="w12-dd-wrap" onKeyDown={onKey}>
      <button
        ref={buttonRef}
        type="button"
        className="w12-dd"
        aria-label={`${label}: ${current?.label ?? ''}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="w12-l">{current?.label}</span><Svg>{W12.down}</Svg>
      </button>
      {open && (
        <div id={listId} role="listbox" aria-label={label} className="w12-dd-menu" aria-activedescendant={`${listId}-${active}`}>
          {options.map((option, index) => (
            <div
              key={option.id}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={option.id === value}
              data-active={index === active || undefined}
              className="w12-dd-opt"
              onPointerEnter={() => setActive(index)}
              onClick={() => pick(option.id)}
            >
              <span className="w12-l">{option.label}</span>
              {option.id === value && <Svg>{W12.check}</Svg>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── лента карточек: листается стрелками и драгом ── */
const railMovedRef: { current: () => boolean } = { current: () => false };
export function Rail({ children, resetKey }: { children: ReactNode; resetKey: string }) {
  const { t } = useTranslation();
  const scroll = useDragScroll();
  railMovedRef.current = scroll.moved;
  const [edges, setEdges] = useState({ left: true, right: true });
  const sync = () => {
    const el = scroll.ref.current;
    if (!el) return;
    setEdges({ left: el.scrollLeft < 8, right: el.scrollLeft + el.clientWidth > el.scrollWidth - 8 });
  };
  useEffect(() => {
    scroll.ref.current?.scrollTo({ left: 0 });
    sync();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey]);
  useEffect(() => {
    window.addEventListener('resize', sync);
    return () => window.removeEventListener('resize', sync);
  });
  const by = (dir: 1 | -1) => scroll.ref.current?.scrollBy({ left: dir * scroll.ref.current.clientWidth * 0.8, behavior: 'smooth' });
  return (
    <div className="w12-rail">
      <button type="button" className="w12-rail-btn w12-l" data-off={edges.left || undefined} aria-label={t('wizard.bg.railPrev')} onClick={() => by(-1)}><Svg>{W12.left}</Svg></button>
      <div ref={scroll.ref} className="w12-cards" onScroll={sync} {...scroll.handlers}>{children}</div>
      <button type="button" className="w12-rail-btn w12-r" data-off={edges.right || undefined} aria-label={t('wizard.bg.railNext')} onClick={() => by(1)}><Svg>{W12.right}</Svg></button>
    </div>
  );
}

export function MediaCard({ item, order, format, caption, onToggle }: { item: Pick<Vibe, 'id' | 'name' | 'previewUrl'>; order: number; format: string; caption: string; onToggle: () => void }) {
  // пустая ссылка — оригинала превью нет в хранилище (бэк пишет это в лог): сразу «недоступно»
  const [broken, setBroken] = useState(!item.previewUrl);
  const isVideo = isVideoUrl(item.previewUrl);
  return (
    <button
      type="button"
      className={cn('w12-mcard', format === '4:3' && 'w12-wide', format === '16:9' && 'w12-cine')}
      aria-pressed={order > 0}
      onClick={() => { if (!railMovedRef.current()) onToggle(); }}
    >
      <span className="w12-media">
        {/* играет только карточка в кадре ленты (заставка до тех пор) — см. PreviewVideo */}
        {!broken && (isVideo
          ? <PreviewVideo src={item.previewUrl} draggable={false} onError={() => setBroken(true)} />
          : <img src={item.previewUrl} alt="" draggable={false} onError={() => setBroken(true)} />)}
      </span>
      <span className="w12-badge w12-num">{order > 0 ? order : ''}</span>
      <span className="w12-cap">{caption}</span>
    </button>
  );
}

/* ── цвет: белый, чёрный или свой на шкале; код цвета всегда виден ── */
function ColorPicker() {
  const { t } = useTranslation();
  const chip = useChip();
  const background = useWizardStore((state) => state.background);
  const setBackground = useWizardStore((state) => state.setBackground);
  const setHover = useBgHover((state) => state.set);
  const value = background.color;
  const custom = Boolean(value && value !== WHITE_BG && value !== BLACK_BG);
  // позиция ползунка — из сохранённого цвета: раньше на каждом входе на шаг он вставал в 50%
  const [huePct, setHuePct] = useState(() => (custom && value ? nearestHuePercent(value) : 50));
  const hueRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // цвет пришёл извне (черновик, вариация) — переставить ползунок; свой же драг не трогаем,
    // иначе округление до процента дёргало бы ползунок под пальцем
    if (custom && value && hueAt(huePct).toLowerCase() !== value.toLowerCase()) setHuePct(nearestHuePercent(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [custom, value]);
  const pick = (clientX: number) => {
    const rect = hueRef.current?.getBoundingClientRect();
    if (!rect) return;
    const pct = Math.min(100, Math.max(0, ((clientX - rect.left) / rect.width) * 100));
    setHuePct(pct);
    setBackground({ color: hueAt(pct) });
  };
  return (
    <div className="w12-colors">
      <div className="w12-sw-row">
        <button type="button" className="w12-sw w12-white" aria-label={t('wizard.bg.whiteBg')} aria-pressed={value === WHITE_BG} onClick={() => setBackground({ color: value === WHITE_BG ? undefined : WHITE_BG })} />
        <button type="button" className="w12-sw w12-black" aria-label={t('wizard.bg.blackBg')} aria-pressed={value === BLACK_BG} onClick={() => setBackground({ color: value === BLACK_BG ? undefined : BLACK_BG })} />
        <div
          ref={hueRef}
          className="w12-hue"
          role="slider"
          tabIndex={0}
          aria-label={t('wizard.bg.colorBg')}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(huePct)}
          // скринридер читает цвет, а не «47 процентов»
          aria-valuetext={(custom && value ? value : hueAt(huePct)).toUpperCase()}
          style={{ background: HUE_GRADIENT }}
          onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); pick(event.clientX); }}
          onPointerMove={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) pick(event.clientX); }}
          onKeyDown={(event) => {
            const d = ({ ArrowLeft: -2, ArrowRight: 2 } as Record<string, number>)[event.key];
            if (!d) return;
            event.preventDefault();
            const pct = Math.min(100, Math.max(0, huePct + d));
            setHuePct(pct);
            setBackground({ color: hueAt(pct) });
          }}
        >
          <span className="w12-thumb" style={{ left: `${huePct}%`, background: hueAt(huePct) }} />
        </div>
      </div>
      <div className="w12-cur-color">
        {value ? <><i style={{ background: value }} />{t('wizard.bg.colorValue')} <b>{value.toUpperCase()}</b></> : t('wizard.bg.colorHint')}
      </div>
      <div className="w12-opt-row">
        <span className="w12-mi w12-cap" aria-hidden="true" style={{ '--m': 'url(/assets/wizard/ic-strobe.svg)', color: background.strobe ? 'var(--w12-text)' : 'var(--w12-text-3)', alignSelf: 'flex-start', marginTop: '.3em' } as React.CSSProperties} />
        <span className="w12-txt">{t('wizard.bg.strobe')}<span>{t('wizard.bg.strobeHint')}</span></span>
        <Toggle checked={background.strobe} onChange={(strobe) => setBackground({ strobe })} label={t('wizard.bg.strobe')} />
      </div>
      {/* склейки видны только при включённом стробе — выключенными они не висят */}
      <div className="w12-disclose" data-open={background.strobe || undefined}>
        <div>
          <div className="w12-sec" style={{ gap: 8 }}>
            <span className="w12-pool-note" style={{ marginTop: 4 }}>{t('wizard.bg.glueHint')}</span>
            <div className="w12-pills">
              {GLUE_TYPES.map((glue) => (
                <button
                  key={glue.id}
                  type="button"
                  className="w12-pill w12-with-ic"
                  aria-pressed={background.strobe && background.glue === glue.id}
                  tabIndex={background.strobe ? 0 : -1}
                  onClick={() => setBackground({ glue: glue.id })}
                  onPointerEnter={() => setHover(previewIdFor('effectGlue', glue.label) ?? null)}
                  onPointerLeave={() => setHover(null)}
                  onFocus={() => setHover(previewIdFor('effectGlue', glue.label) ?? null)}
                  onBlur={() => setHover(null)}
                >
                  <ChipBadge label={glue.label} /><span className="w12-l">{chip(glue.label)}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function useBackgroundLists() {
  const background = useWizardStore((state) => state.background);
  const lyrics = useWizardStore((state) => state.fragmentEnabled ? state.fragmentLyrics : state.lyrics);
  // Порядок задают и текст отрывка, и план подбора: вайбы ранжируются по смыслу текста
  const footagePlane = footageTypePlane(background.footageType);
  const vibesQuery = useQuery({
    queryKey: ['vibes', footagePlane, footagePlane === 'vibes' ? lyrics : ''],
    queryFn: async () => footagePlane === 'vibes'
      ? { vibes: (await api.rankBackgrounds(lyrics, 'video')).items }
      : api.vibes(footagePlane),
    staleTime: 60_000,
    enabled: background.mode === 'footage' && (footagePlane !== 'vibes' || Boolean(lyrics.trim()))
  });
  const photosQuery = useQuery({
    queryKey: ['photos', lyrics],
    queryFn: async () => ({ photos: (await api.rankBackgrounds(lyrics, 'photo')).items }),
    staleTime: 60_000,
    enabled: background.mode === 'photo' && Boolean(lyrics.trim())
  });
  return { background, footagePlane, vibesQuery, photosQuery };
}

/** Кадр фона для превью следующих шагов: первый выбранный футаж или фото, либо цвет. */
export function useBackdrop(): { url?: string; isVideo?: boolean; color?: string } {
  const { background, vibesQuery, photosQuery } = useBackgroundLists();
  const queryClient = useQueryClient();
  if (background.mode === 'color') return { color: background.color };
  const names = background.mode === 'photo' ? background.photo : background.footage;
  const known = background.mode === 'photo'
    ? photosQuery.data?.photos ?? []
    : [...(vibesQuery.data?.vibes ?? []), ...queryClient.getQueriesData<{ vibes?: Vibe[] }>({ queryKey: ['vibes'] }).flatMap(([, data]) => data?.vibes ?? [])];
  const item = names.map((name) => known.find((entry) => entry.name === name)).find(Boolean);
  return item ? { url: item.previewUrl, isVideo: isVideoUrl(item.previewUrl) } : {};
}

/** Шаг «Фон»: режимы со счётчиками, типы футажей видны сразу, лента выбора, свои исходники. */
export function StageBackground({ qaGuide }: { qaGuide?: string | null }) {
  const { t } = useTranslation();
  const chip = useChip();
  const setBackground = useWizardStore((state) => state.setBackground);
  const toggleVibe = useWizardStore((state) => state.toggleVibe);
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: 15_000 });
  const { background, footagePlane, vibesQuery, photosQuery } = useBackgroundLists();
  const tried = useTried(2);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  // «Свои» — отдельный тип в ленте футажей, пока свои видео есть
  const [showOwn, setShowOwn] = useState(false);
  const modeGuideTargetRef = useRef<HTMLDivElement>(null);
  const selectionGuideTargetRef = useRef<HTMLDivElement>(null);
  // режимы — настоящие табы: панель под ними (пул) подписана активным табом, стрелки ходят по табам
  const modesId = useId();
  const modeTabId = (mode: BackgroundMode) => `${modesId}-tab-${mode}`;
  const modePanelId = `${modesId}-panel`;
  const modeRefs = useRef<Partial<Record<BackgroundMode, HTMLButtonElement | null>>>({});
  const onModeKey = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const at = modes.findIndex((item) => item.value === background.mode);
    const step = ({ ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 } as Record<string, number>)[event.key];
    const index = event.key === 'Home' ? 0 : event.key === 'End' ? modes.length - 1 : step === undefined ? -1 : (at + step + modes.length) % modes.length;
    if (index < 0) return;
    event.preventDefault();
    const next = modes[index].value;
    setBackground({ mode: next });
    modeRefs.current[next]?.focus();
  };

  const isMedia = background.mode !== 'color';
  const listQuery = background.mode === 'photo' ? photosQuery : vibesQuery;
  const list = (background.mode === 'photo' ? photosQuery.data?.photos : vibesQuery.data?.vibes) ?? [];
  const loading = listQuery.isLoading;
  const selected = background.mode === 'photo' ? background.photo : background.footage;
  const counts = modeCounts(background);
  const hasBackground = backgroundVariations(background) > 0;
  const own = background.sourceVideos;
  const ownShown = background.mode === 'footage' && showOwn && own.length > 0;

  // selection-гайд объявлен первым: его dismissed нужен для idle-условия гайда выше по цепочке
  const [selectionGuideDismissed, setSelectionGuideDismissed] = useGuideDismiss('background-selection', !hasBackground && (!isMedia || !loading), false);
  const [modeGuideDismissed, setModeGuideDismissed] = useGuideDismiss('background-mode', !hasBackground && !selectionGuideDismissed, true);
  useEffect(() => {
    if (!qaGuide) return;
    setModeGuideDismissed(qaGuide === 'background-sources');
    setSelectionGuideDismissed(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qaGuide]);
  const showModeGuide = !modeGuideDismissed;
  const showSelectionGuide = modeGuideDismissed && !selectionGuideDismissed && (!isMedia || !loading);
  useMarkGuideSeen('background-selection', modeGuideDismissed && (!isMedia || !loading));
  useScrollGuideIntoView(showSelectionGuide, selectionGuideTargetRef);

  const format = background.mode === 'photo' ? '4:3' : background.footageType === 'cine16x9' ? '16:9' : '9:16';
  const canUpload = Boolean(meQuery.data?.capabilities?.customSources);

  return (
    <>
      <div className="w12-sec">
        <div className="w12-sec-head">
          <h2><span className="w12-sq w12-ttl-ic" /><span className="w12-l">{t('wizard.bg.headingShort')}</span></h2>
          {canUpload && (
            <div className="w12-side">
              <button type="button" className={cn('w12-ghost', own.length > 0 && 'w12-on')} onClick={() => setSourcesOpen(true)}>
                <span className="w12-mi w12-cap w12-heavy" aria-hidden="true" style={{ '--m': 'url(/assets/wizard/ic-upload.svg)' } as React.CSSProperties} />
                <span className="w12-l">{own.length > 0 ? t('wizard.bg.ownFootageCount', { count: own.length }) : t('wizard.bg.uploadFootage')}</span>
              </button>
            </div>
          )}
        </div>
        <div ref={modeGuideTargetRef} className="w12-modes" role="tablist" aria-label={t('wizard.bg.typeAria')} onKeyDown={onModeKey}>
          {modes.map((item) => (
            <button
              key={item.value}
              ref={(el) => { modeRefs.current[item.value] = el; }}
              id={modeTabId(item.value)}
              type="button"
              role="tab"
              className="w12-mode"
              aria-selected={background.mode === item.value}
              aria-controls={modePanelId}
              tabIndex={background.mode === item.value ? 0 : -1}
              onClick={() => setBackground({ mode: item.value })}
            >
              {item.icon}<span className="w12-l">{t(item.label)}</span>
              <span className={cn('w12-cnt w12-num', !counts[item.value] && 'w12-zero')}>{counts[item.value] || ''}</span>
            </button>
          ))}
        </div>
      </div>

      <div ref={selectionGuideTargetRef} id={modePanelId} role="tabpanel" aria-labelledby={modeTabId(background.mode)} className={cn('w12-pool', tried && !hasBackground && 'w12-invalid')}>
        <div className="w12-pool-head">
          {background.mode === 'footage' && (
            <>
              <TypeMenu
                label={t('wizard.bg.footageTypeAria')}
                value={ownShown ? OWN_TYPE : background.footageType}
                options={[
                  ...(own.length > 0 ? [{ id: OWN_TYPE, label: t('wizard.bg.ownType', { count: own.length }) }] : []),
                  ...FOOTAGE_TYPES.map((type) => ({ id: type.id, label: t(footageTypeKey(type.id)) }))
                ]}
                onChange={(id) => {
                  if (id === OWN_TYPE) { setShowOwn(true); return; }
                  setShowOwn(false);
                  setBackground({ footageType: id });
                }}
              />
              <span className="w12-pool-note">{ownShown ? t('wizard.bg.ownNote') : footagePlane === 'vibes' ? <><Svg>{W12.spark}</Svg><span className="w12-l">{t('wizard.bg.byLyrics')}</span></> : null}</span>
            </>
          )}
          {background.mode === 'photo' && (
            <>
              <span className="w12-pool-note"><Svg>{W12.spark}</Svg><span className="w12-l">{t('wizard.bg.photosByLyrics')}</span></span>
              <span className="w12-pool-note w12-num">{t('wizard.bg.selectedCount', { count: counts.photo })}</span>
            </>
          )}
          {background.mode === 'color' && (
            <>
              <span className="w12-pool-note">{t('wizard.bg.colorNote')}</span>
              {background.color && <button type="button" className="w12-link" onClick={() => setBackground({ color: undefined })}>{t('wizard.bg.colorOff')}</button>}
            </>
          )}
        </div>

        {!isMedia ? <ColorPicker /> : ownShown ? (
          <Rail resetKey="own">
            {own.map((plan, index) => (
              <button key={plan.id} type="button" className={cn('w12-mcard', plan.format === '16:9' && 'w12-cine')} aria-pressed="true" onClick={() => setSourcesOpen(true)}>
                <span className="w12-media w12-own"><Svg>{W12.upload}</Svg></span>
                <span className="w12-badge"><Svg>{W12.check}</Svg></span>
                <span className="w12-cap">{t('wizard.bg.ownCard', { n: index + 1, count: plan.sourceIds.length })}</span>
              </button>
            ))}
          </Rail>
        ) : loading ? (
          <div className="w12-rail w12-center"><span className="spinner" aria-hidden="true" /></div>
        ) : queryDown(listQuery) ? (
          /* библиотека фонов не пришла — без этого экран оставался пустым молча */
          <div className="w12-rail w12-center">
            <InlineError error={listQuery.error} offline={listQuery.fetchStatus === 'paused'} onRetry={() => listQuery.refetch()} retrying={listQuery.isFetching} />
          </div>
        ) : (
          <Rail resetKey={`${background.mode}:${background.footageType}`}>
            {list.map((item) => (
              <MediaCard
                key={item.id}
                item={item}
                format={format}
                caption={chip(item.name)}
                order={selected.indexOf(item.name) + 1}
                onToggle={() => toggleVibe(item.name, format)}
              />
            ))}
          </Rail>
        )}
      </div>

      <SourcesModal open={sourcesOpen} onClose={() => { setSourcesOpen(false); if (useWizardStore.getState().background.sourceVideos.length) { setBackground({ mode: 'footage' }); setShowOwn(true); } }} />

      <ActionGuideOverlay
        open={showModeGuide}
        targetRef={modeGuideTargetRef}
        title={t('wizard.bg.guideModeTitle')}
        text={t('wizard.bg.guideModeText')}
        dismissLabel={t('wizard.bg.guideNext')}
        progressLabel={t('wizard.guideProgress', { current: 1, total: 2 })}
        onDismiss={() => setModeGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<BackgroundModeGuideVisual />}
      />
      <ActionGuideOverlay
        open={showSelectionGuide}
        targetRef={selectionGuideTargetRef}
        title={background.mode === 'color' ? t('wizard.bg.guideColorTitle') : t('wizard.bg.guidePoolTitle')}
        text={background.mode === 'color' ? t('wizard.bg.guideColorText') : t('wizard.bg.guidePoolText')}
        dismissLabel={t('wizard.bg.guideDismiss')}
        progressLabel={t('wizard.guideProgress', { current: 2, total: 2 })}
        onDismiss={() => setSelectionGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={background.mode === 'color' ? (
          <div className="flex w-full items-center gap-[8px]" aria-hidden="true">
            {/* ui-allow: иллюстрация гайда */}
            <span className="h-[34px] w-[34px] shrink-0 rounded-r10 bg-[#f6f5fd]" />
            <span className="h-[34px] w-[34px] shrink-0 rounded-r10 bg-bg ring-1 ring-text-20" />
            <span className="h-[34px] flex-1 rounded-r10" style={{ background: HUE_GRADIENT }} />
          </div>
        ) : <BackgroundPoolGuideVisual />}
      />
    </>
  );
}

/** Правая колонка «Фона»: превью (пейджер внутри плеера, играет под отрывок) и итог. */
export function BackgroundWorkZone({ ready, loading, onBack, onNext }: { ready: boolean; canContinue?: boolean; loading?: boolean; onBack: () => void; onNext: () => void }) {
  const { t } = useTranslation();
  const chip = useChip();
  const setBackground = useWizardStore((state) => state.setBackground);
  const { background, vibesQuery, photosQuery } = useBackgroundLists();
  const example = useBgHover((state) => state.example);
  const setHover = useBgHover((state) => state.set);
  const [index, setIndex] = useState(0);
  const [broken, setBroken] = useState<Record<string, boolean>>({});
  const fragmentAudio = useFragmentAudio();
  const previewVideo = useRef<HTMLVideoElement | null>(null);
  const counts = modeCounts(background);

  const queryClient = useQueryClient();
  const list = (background.mode === 'photo' ? photosQuery.data?.photos : vibesQuery.data?.vibes) ?? [];
  // Выбранные футажи могут быть из другой подборки, чем открытая сейчас (выбрал вертикальные —
  // переключился на «Кино 16:9»): ищем их во всех уже загруженных подборках, а не только в текущей,
  // иначе превью писало «выбери карточки», хотя счётчик показывал выбранные.
  const known = background.mode === 'photo'
    ? list
    : [...list, ...queryClient.getQueriesData<{ vibes?: Vibe[] }>({ queryKey: ['vibes'] }).flatMap(([, data]) => data?.vibes ?? [])];
  const selectedNames = background.mode === 'photo' ? background.photo : background.footage;
  const selected = selectedNames.map((name) => known.find((item) => item.name === name)).filter(Boolean) as Vibe[];
  const total = background.mode === 'color' ? (background.color ? 1 : 0) : selected.length;
  const safeIndex = total ? Math.min(index, total - 1) : 0;
  const current = background.mode === 'color' ? null : selected[safeIndex] ?? null;
  useEffect(() => setIndex(Math.max(0, selectedNames.length - 1)), [background.mode, selectedNames.length]);
  const format = current && background.mode === 'footage'
    // формат — у самого клипа (записан при выборе, иначе по его подборке), а не у открытой сейчас подборки
    ? background.footageFormats?.[current.name] ?? (current.plane ? (current.plane === 'cine16x9' ? '16:9' : '9:16') : background.footageType === 'cine16x9' ? '16:9' : '9:16')
    : background.mode === 'photo' ? '4:3' : '9:16';
  const color = background.mode === 'color' ? background.color : undefined;
  const isVideo = current ? isVideoUrl(current.previewUrl) : false;
  const hasContent = Boolean(current || color);
  const meta = total
    ? background.mode === 'footage' ? t('wizard.bg.metaFootage', { count: total }) : background.mode === 'photo' ? t('wizard.bg.metaPhoto', { count: total }) : t('wizard.bg.modeColor')
    : '';
  // Трек пошёл — футаж с первого кадра вместе с ним: ролик и так крутится без звука по кругу,
  // и раньше по «играть» в кадре ничего не менялось. play() здесь же — если браузер не дал
  // автоплей, ролик стартует от этого клика. Стоп трека ролик не трогает: дальше он снова
  // просто крутится без звука, как до нажатия.
  const togglePreview = () => {
    const video = previewVideo.current;
    if (video && !fragmentAudio.playing && !fragmentAudio.loading) {
      video.currentTime = 0;
      video.play().catch(() => undefined);
    }
    fragmentAudio.toggle();
  };
  const audioActive = fragmentAudio.playing || fragmentAudio.loading;
  const tag = example
    ? t('wizard.bg.exampleTag')
    : current
      ? `${t(background.mode === 'photo' ? 'wizard.bg.modePhoto' : 'wizard.bg.tagFootage')} · ${chip(current.name)}`
      : color ? `${t('wizard.bg.modeColor')} · ${color.toUpperCase()}${background.strobe ? ` · ${t('wizard.bg.strobe').toLowerCase()}` : ''}` : null;

  return (
    <aside className="w12-col-aside">
      <div className="w12-card w12-pv-card">
        <div className="w12-aside-head"><h2>{t('wizard.bg.preview')}</h2><span className="w12-meta">{meta}</span></div>
        {/* широкий кадр (4:3, 16:9) в высокой колонке: вокруг него — размытая копия того же кадра,
            как у горизонтальных видео в вертикальных лентах, а не пустое поле сверху и снизу */}
        <div className={cn('w12-pv-stage', format !== '9:16' && 'w12-pv-ambient')}>
          {/* размытая подложка — заставка (картинка), а не второй экземпляр того же ролика:
              раньше выбранное превью качалось и декодировалось дважды */}
          {format !== '9:16' && current?.previewUrl && !broken[current.id] && !example && (isVideo
            ? catalogPosterOf(current.previewUrl) && <img key={`bg-${current.id}`} className="w12-ambient-bg" src={catalogPosterOf(current.previewUrl)!} alt="" aria-hidden="true" />
            : <img key={`bg-${current.id}`} className="w12-ambient-bg" src={current.previewUrl} alt="" aria-hidden="true" />)}
          <div className={cn('w12-player', format === '4:3' && 'w12-wide', format === '16:9' && 'w12-cine', fragmentAudio.playing && 'w12-playing')}>
            {current?.previewUrl && !broken[current.id] && (isVideo
              ? <PreviewVideo key={current.id} ref={previewVideo} className="w12-media-el" src={current.previewUrl} ignoreLowData onError={() => setBroken((b) => ({ ...b, [current.id]: true }))} />
              : <img key={current.id} className="w12-media-el" src={current.previewUrl} alt="" onError={() => setBroken((b) => ({ ...b, [current.id]: true }))} />)}
            {current && !color && !example && (!current.previewUrl || broken[current.id]) && (
              <div role="status" className="w12-media-el grid place-items-center p-4 text-center text-text-60">{t('wizard.preview.unavailable')}</div>
            )}
            {color && (
              <div
                className="w12-media-el"
                style={background.strobe && !example ? ({ '--strobe-color': color, animation: 'strobeFlicker 1s steps(1) infinite' } as CSSProperties) : { background: color }}
              />
            )}
            {/* наведённая склейка или стиль — честный пример из каталога эффектов */}
            {example && <div className="w12-media-el w12-example"><EffectPreview previewId={example} /></div>}
            {!hasContent && !example && <div className="w12-empty">{t(background.mode === 'color' ? 'wizard.bg.previewEmptyColor' : 'wizard.bg.previewEmpty')}</div>}
            {/* текста поверх кадра на «Фоне» нет: субтитры ещё не посчитаны, а строка песни
                отвлекала от выбора фона. Затемнение оставлено — под плашкой и кнопкой «играть» */}
            {hasContent && !example && <div className="w12-shade" />}
            {tag && <span className="w12-pv-tag">{tag}</span>}
            {hasContent && fragmentAudio.available && (
              <button
                type="button"
                className="w12-pv-play"
                onClick={togglePreview}
                aria-pressed={audioActive}
                aria-busy={fragmentAudio.loading || undefined}
                aria-label={audioActive ? t('wizard.bg.stopTrack') : t('wizard.bg.playTrack')}
              >
                {/* пока трек грузится — крутилка: «пауза» в тишине выглядела как сломанная кнопка */}
                {fragmentAudio.loading ? <span className="spinner" aria-hidden="true" /> : fragmentAudio.playing ? PAUSE : PLAY}
              </button>
            )}
            {total > 1 && (
              <div className="w12-pv-bar">
                <button type="button" className="w12-pv-nav" aria-label={t('wizard.bg.prevExample')} onClick={() => setIndex((safeIndex - 1 + total) % total)}><Svg>{W12.left}</Svg></button>
                <span className="w12-pv-count w12-num">{safeIndex + 1} / {total}</span>
                <button type="button" className="w12-pv-nav" aria-label={t('wizard.bg.nextExample')} onClick={() => setIndex((safeIndex + 1) % total)}><Svg>{W12.right}</Svg></button>
              </div>
            )}
          </div>
        </div>
        {background.mode === 'photo' && (
          <div className="w12-fx-box">
            <div className="w12-opt-row" style={{ border: 0, padding: 0 }}>
              <span style={{ color: 'var(--w12-text-3)', alignSelf: 'flex-start', fontSize: '.9em', lineHeight: 1.55 }} aria-hidden="true">✦</span>
              <span className="w12-txt">{t('wizard.bg.effects')}<span>{t('wizard.bg.effectsHint')}</span></span>
              <Toggle checked={background.photoEffects} onChange={(photoEffects) => setBackground({ photoEffects })} label={t('wizard.bg.effects')} />
            </div>
            <div className="w12-disclose" data-open={background.photoEffects || undefined}>
              <div>
                <div className="w12-pills">
                  {PHOTO_STYLES.map((style) => (
                    <button
                      key={style}
                      type="button"
                      className="w12-pill w12-with-ic"
                      aria-pressed={background.photoEffects && background.photoStyle === style}
                      tabIndex={background.photoEffects ? 0 : -1}
                      onClick={() => setBackground({ photoStyle: style })}
                      onPointerEnter={() => setHover(previewIdFor('effectStyle', style) ?? null)}
                      onPointerLeave={() => setHover(null)}
                      onFocus={() => setHover(previewIdFor('effectStyle', style) ?? null)}
                      onBlur={() => setHover(null)}
                    >
                      <ChipBadge label={style} /><span className="w12-l">{chip(style)}</span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* итог по режимам (клик ведёт в режим); «+» не нужен — его делают вкладки */}
      <PillsFooter
        pills={modes.map((item) => ({ key: item.value, label: t(item.label), icon: counts[item.value] || '0', zero: !counts[item.value] }))}
        activeKey={background.mode}
        emptyLabel=""
        onPill={(key) => setBackground({ mode: key as BackgroundMode })}
        ready={ready}
        loading={loading}
        onBack={onBack}
        onNext={onNext}
      />
    </aside>
  );
}
