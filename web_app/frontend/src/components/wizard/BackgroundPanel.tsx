import { CSSProperties, PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useChip } from '../../i18n/useChip';
import { useToast } from '../../contexts/ToastContext';
import { api } from '../../lib/api';
import { isVideoUrl } from '../../lib/media';
import { cn } from '../../lib/cn';
import { HUE_GRADIENT, hueAt } from '../../lib/color';
import type { Vibe } from '../../lib/types';
import { SvgMaskIcon } from '../layout/SvgMaskIcon';
import { InlineError, queryDown } from '../ui/ErrorState';
import { ChipIcon } from './HookPanel';
import { PillsFooter } from './WizardFrame';
import { PreviewPlayer } from '../ui/PreviewPlayer';
import { useFragmentAudio } from './useFragmentAudio';
import { SourcesModal } from './SourcesEditor';
import { ActionGuideOverlay, type ActionGuideVariant } from '../guidance/ActionGuideOverlay';
import { useGuideDismiss } from '../guidance/useGuideDismiss';
import { useScrollGuideIntoView } from '../guidance/useScrollGuideIntoView';
import { footageTypeKey, footageTypePlane, stepFootageType } from '../../data/footageTypes';
import effectsRegistry from '../../data/effects-registry.json';
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

export type BackgroundGuideGraphic = 'map' | 'pairs' | 'stack' | 'studio';

const GUIDE_TONES = [
  'from-[#8b6fe6] to-[#342553]',
  'from-[#42627b] to-[#172331]',
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

function BackgroundModeGuideVisual({ variant }: { variant: BackgroundGuideGraphic }) {
  if (variant === 'studio') {
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
                <span className="absolute inset-0 rounded-[8px] bg-[#6850b7] ring-1 ring-inset ring-white/30" style={{ animation: `guide-mode-on-fade 320ms cubic-bezier(.16,1,.3,1) ${iconDelays[index]}ms both` }} />
              )}
              <span className="relative z-[1]">
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

  if (variant === 'pairs') {
    return (
      <div className="grid w-full min-w-0 grid-cols-[104px_18px_minmax(0,1fr)] items-center gap-[8px] overflow-hidden" aria-hidden="true">
        <span className="flex min-w-0 flex-col gap-[5px]">
          {['Футажи', 'Фото'].map((label, index) => (
            <span key={label} className={cn('guide-mode-reveal flex h-[26px] min-w-0 items-center gap-[6px] rounded-[7px] bg-[#6850b7] px-[7px] text-[9px] text-white', index === 0 ? 'guide-mode-delay-1' : 'guide-mode-delay-2')}>
              {index === 0 ? (
                <TagIcon color="rgba(255,255,255,0.92)" size={13} />
              ) : (
                <SvgMaskIcon src="/assets/figma/icon-photo.svg" style={{ width: 13, height: 12, color: 'rgba(255,255,255,0.92)' }} />
              )}
              <span className="action-guide-optical-text truncate">{label}</span>
            </span>
          ))}
        </span>
        <span className="guide-mode-reveal guide-mode-delay-3 flex items-center justify-center">
          <img src="/assets/figma/pd-arrow-right.svg" alt="" className="h-[10px] w-[18px]" />
        </span>
        <span className="guide-mode-reveal guide-mode-delay-4 relative h-[57px] w-full min-w-0 overflow-hidden rounded-[9px] border border-white/70 bg-black/15">
          <span className="guide-mode-frame guide-mode-delay-5 absolute left-[7px] top-[4px] opacity-50"><GuideFrame index={2} large /></span>
          <span className="guide-mode-frame guide-mode-delay-6 absolute left-[22px] top-[3px] opacity-75"><GuideFrame index={1} large /></span>
          <span className="guide-mode-frame guide-mode-delay-7 absolute left-[37px] top-[2px]"><GuideFrame index={0} selected large /></span>
        </span>
      </div>
    );
  }

  if (variant === 'stack') {
    return (
      <div className="grid w-full min-w-0 grid-cols-[82px_18px_82px] items-center justify-center gap-[8px] overflow-hidden" aria-hidden="true">
        <span className="flex h-[64px] flex-col gap-[4px] rounded-[9px] border border-white/30 bg-black/15 p-[6px]">
          {['Футажи', 'Фото', 'Цвет'].map((label, index) => (
            <span key={label} className={cn('flex h-[14px] min-w-0 items-center gap-[5px] rounded-[5px] px-[5px] text-[7px] text-white', index < 2 ? 'bg-[#6850b7] ring-1 ring-inset ring-white/25' : 'border border-dashed border-white/20 bg-white/[0.03] text-white/45')}>
              {index === 0 ? <TagIcon color="currentColor" size={9} /> : <SvgMaskIcon src={index === 1 ? '/assets/figma/icon-photo.svg' : '/assets/figma/icon-colorwheel.svg'} style={{ width: 9, height: 9, color: 'currentColor' }} />}
              <span className="action-guide-optical-text truncate">{label}</span>
            </span>
          ))}
        </span>
        <img src="/assets/figma/pd-arrow-right.svg" alt="" className="h-[10px] w-[18px] opacity-85" />
        <span className="relative h-[64px] overflow-hidden rounded-[9px] border border-white/30 bg-black/15">
          <span className="absolute left-[8px] top-[8px] opacity-45"><GuideFrame index={2} /></span>
          <span className="absolute left-[24px] top-[6px] opacity-75"><GuideFrame index={1} /></span>
          <span className="absolute left-[40px] top-[4px]"><GuideFrame index={0} selected /></span>
        </span>
      </div>
    );
  }

  return (
    <div className="grid w-full min-w-0 grid-cols-[86px_18px_86px] items-center justify-center gap-[8px] overflow-hidden" aria-hidden="true">
      <span className="relative h-[64px] overflow-hidden rounded-[9px] border border-white/30 bg-black/15">
        <span className="absolute left-[9px] top-[8px] flex h-[22px] w-[50px] items-center gap-[5px] rounded-[6px] bg-[#6850b7] px-[6px] text-[7px] text-white ring-1 ring-inset ring-white/25"><TagIcon color="currentColor" size={9} /><span className="action-guide-optical-text">Футажи</span></span>
        <span className="absolute left-[25px] top-[25px] flex h-[22px] w-[50px] items-center gap-[5px] rounded-[6px] bg-[#6850b7] px-[6px] text-[7px] text-white shadow-[0_5px_12px_rgba(5,1,15,.32)] ring-1 ring-inset ring-white/25"><SvgMaskIcon src="/assets/figma/icon-photo.svg" style={{ width: 9, height: 9, color: 'currentColor' }} /><span className="action-guide-optical-text">Фото</span></span>
        <span className="absolute bottom-[5px] left-[10px] h-[12px] w-[12px] rounded-[4px] border border-dashed border-white/20" />
      </span>
      <img src="/assets/figma/pd-arrow-right.svg" alt="" className="h-[10px] w-[18px] opacity-85" />
      <span className="grid h-[64px] grid-cols-[1fr_1fr] gap-[5px] overflow-hidden rounded-[9px] border border-white/30 bg-black/15 p-[6px]">
        <span className={cn('col-span-2 h-[20px] rounded-[6px] bg-gradient-to-r ring-1 ring-inset ring-white/35', GUIDE_TONES[1])} />
        <span className={cn('h-[30px] rounded-[6px] bg-gradient-to-b ring-1 ring-inset ring-white/35', GUIDE_TONES[0])} />
        <span className={cn('h-[30px] rounded-[6px] bg-gradient-to-b ring-1 ring-inset ring-white/35', GUIDE_TONES[2])} />
      </span>
    </div>
  );
}

function BackgroundPoolGuideVisual({ variant }: { variant: BackgroundGuideGraphic }) {
  if (variant === 'studio') {
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

  if (variant === 'pairs') {
    return (
      <div className="grid w-full min-w-0 grid-cols-3 gap-[9px] overflow-hidden px-[2px]" aria-hidden="true">
        {GUIDE_TONES.map((tone, index) => (
          <span key={index} className="flex min-w-0 flex-col items-center">
            <span className={cn('h-[18px] w-full rounded-[6px] bg-gradient-to-r ring-1 ring-inset ring-white/35', tone)} />
            <img src="/assets/figma/pd-arrow-right.svg" alt="" className="my-[3px] h-[8px] w-[12px] rotate-90 opacity-80" />
            <span className={cn('relative flex h-[47px] w-[31px] items-center justify-center overflow-hidden rounded-[7px] bg-gradient-to-b ring-1 ring-inset ring-white/55', tone)}>
              <img src="/assets/figma/btn-play.svg" alt="" className="h-[14px] w-[14px]" />
              <span className="action-guide-optical-text absolute bottom-[3px] text-[7px] text-white/80">0{index + 1}</span>
            </span>
          </span>
        ))}
      </div>
    );
  }

  if (variant === 'stack') {
    return (
      <div className="grid min-h-[82px] w-full min-w-0 grid-cols-[78px_20px_90px] items-center justify-center gap-[8px]" aria-hidden="true">
        <span className="flex h-[72px] items-center justify-center gap-[4px] rounded-[9px] border border-white/30 bg-black/15 px-[7px]">
          {[0, 1, 2, 3].map((index) => index < 3 ? (
            <span key={index} className={cn('relative h-[52px] w-[13px] shrink-0 rounded-[5px] bg-gradient-to-b ring-1 ring-inset ring-white/45', GUIDE_TONES[index])}>
              <span className="action-guide-optical-text absolute left-1/2 top-[2px] -translate-x-1/2 text-[6px] text-white">✓</span>
              <span className="absolute inset-x-[3px] bottom-[5px] h-px rounded-full bg-white/35" />
            </span>
          ) : (
            <span key={index} className="relative h-[52px] w-[13px] shrink-0 rounded-[5px] border border-dashed border-white/30 bg-white/[0.03]">
              <span className="absolute inset-x-[3px] bottom-[5px] h-px rounded-full bg-white/10" />
            </span>
          ))}
        </span>
        <img src="/assets/figma/pd-arrow-right.svg" alt="" className="h-[10px] w-[20px] opacity-85" />
        <span className="relative h-[82px] min-w-0 overflow-visible">
          {GUIDE_TONES.map((tone, index) => (
            <span key={index} className={cn('absolute top-[10px] flex h-[58px] w-[36px] items-center justify-center overflow-hidden rounded-[8px] bg-gradient-to-b shadow-[0_7px_12px_rgba(5,1,15,.38)] ring-1 ring-inset ring-white/50', tone)} style={{ left: 4 + index * 25, transform: `rotate(${(index - 1) * 4}deg)` }}>
              <img src="/assets/figma/btn-play.svg" alt="" className="h-[14px] w-[14px]" />
              <span className="action-guide-optical-text absolute bottom-[4px] text-[7px] text-white/80">0{index + 1}</span>
            </span>
          ))}
        </span>
      </div>
    );
  }

  return (
    <div className="flex w-full min-w-0 flex-col gap-[7px] overflow-hidden px-[2px]" aria-hidden="true">
      <span className="grid grid-cols-[repeat(3,1fr)] gap-[7px]">
        {GUIDE_TONES.map((tone, index) => <span key={index} className={cn('h-[22px] rounded-[6px] bg-gradient-to-r ring-1 ring-inset ring-white/35', tone)} />)}
      </span>
      <span className="relative h-[10px]">
        <span className="absolute inset-x-[14%] top-0 h-px bg-white/25" />
        {GUIDE_TONES.map((_, index) => <img key={index} src="/assets/figma/pd-arrow-right.svg" alt="" className="absolute top-[-1px] h-[8px] w-[11px] rotate-90 opacity-70" style={{ left: `${14 + index * 36}%` }} />)}
      </span>
      <span className="grid grid-cols-3 gap-[7px] rounded-[9px] border border-white/35 bg-black/15 p-[5px]">
        {GUIDE_TONES.map((tone, index) => (
          <span key={index} className={cn('relative flex h-[39px] min-w-0 items-center justify-center overflow-hidden rounded-[6px] bg-gradient-to-b', tone)}>
            <img src="/assets/figma/btn-play.svg" alt="" className="h-[13px] w-[13px]" />
            <span className="action-guide-optical-text absolute bottom-[2px] text-[7px] text-white/80">ролик {index + 1}</span>
          </span>
        ))}
      </span>
    </div>
  );
}

/** Типы склеек (Figma W22) — для строба и стилизации фото */
export const GLUE_TYPES = effectsRegistry.glue
  .filter((effect) => Boolean(effect.altId))
  .map((effect) => ({ id: effect.altId as string, label: effect.label }));

/** Горизонтальный скролл: драг 1:1, колесо — плавно */
export function useDragScroll() {
  const ref = useRef<HTMLDivElement>(null);
  const drag = useRef({ active: false, moved: false, startX: 0, startScroll: 0 });

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    // тач листает лентой нативно (touch-action: pan-x) — JS-drag только для мыши
    if (!ref.current || e.pointerType === 'touch') return;
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
    /* Стрелки держатся текста на постоянном отступе: фиксированная ширина ряда разносила
       их по краям и у коротких подписей («16:9») зазор становился огромным. */
    /* телефон: подпись становится заголовком строки («Вертикальные 9:16»), стрелки — две
       пилюли справа; на десктопе порядок фигмы «‹ подпись ›» через md:order-* */
    <span className="inline-flex items-center gap-[15px] max-md:w-full max-md:gap-[8px]">
      <span
        className="whitespace-nowrap text-center text-[24px] font-[350] leading-normal text-transparent md:order-2 max-md:mr-auto max-md:text-[15px]"
        style={{ backgroundImage: 'var(--grad-main)', WebkitBackgroundClip: 'text', backgroundClip: 'text' }}
      >
        {label}
      </span>
      <span className="md:order-1 max-md:flex max-md:h-[28px] max-md:w-[28px] max-md:items-center max-md:justify-center max-md:rounded-[8px] max-md:bg-grad-soft-20">{arrow(-1)}</span>
      <span className="md:order-3 max-md:flex max-md:h-[28px] max-md:w-[28px] max-md:items-center max-md:justify-center max-md:rounded-[8px] max-md:bg-grad-soft-20">{arrow(1)}</span>
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

function MediaCard({ item, selected, wide, format, onToggle }: { item: Vibe; selected: boolean; wide?: boolean; format: string; onToggle: () => void }) {
  const chip = useChip();
  const [broken, setBroken] = useState(false);
  const isVideo = isVideoUrl(item.previewUrl);
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
  const sizing: CSSProperties = wide || format === '16:9'
    ? { height: '100%', aspectRatio: format === '16:9' ? '16 / 9' : '4 / 3', flexShrink: 0 }
    : { aspectRatio: '142 / 253' };
  return (
    <button type="button" onClick={onToggle} aria-pressed={selected} className={cn('media-card', wide || format === '16:9' ? 'media-card--fit' : 'h-full')} style={sizing}>
      <span className="absolute left-2 top-2 z-10 rounded bg-black/60 px-2 py-1 text-xs text-white">{format}</span>
      {!broken && (isVideo
        ? <video src={item.previewUrl} muted loop playsInline autoPlay onError={() => setBroken(true)} />
        : <img src={item.previewUrl} alt="" onError={() => setBroken(true)} />)}
      {broken && <span className="media-card-fallback">{chip(item.name)}</span>}
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

export function StageBackground({ guideGraphic = 'studio', guideVariant = 'visual', qaGuide }: { guideGraphic?: BackgroundGuideGraphic; guideVariant?: ActionGuideVariant; qaGuide?: string | null }) {
  const { t } = useTranslation();
  const chip = useChip();
  const { push } = useToast();
  const background = useWizardStore((state) => state.background);
  const setBackground = useWizardStore((state) => state.setBackground);
  const toggleVibe = useWizardStore((state) => state.toggleVibe);
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: 15_000 });
  const lyrics = useWizardStore((state) => state.fragmentEnabled ? state.fragmentLyrics : state.lyrics);
  // Lyrics and media plane both determine the semantic order.
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
  const cardsScroll = useDragScroll();
  const gluesScroll = useDragScroll();
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const modeGuideTargetRef = useRef<HTMLDivElement>(null);
  const selectionGuideTargetRef = useRef<HTMLDivElement>(null);

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

  const isMedia = background.mode !== 'color';
  const listQuery = background.mode === 'photo' ? photosQuery : vibesQuery;
  const list = background.mode === 'photo' ? photosQuery.data?.photos : vibesQuery.data?.vibes;
  const loading = listQuery.isLoading;
  const selected = background.mode === 'photo' ? background.photo : background.footage;
  const hasBackground = backgroundVariations(background) > 0;
  const forceModeGuide = qaGuide === 'background-mode';
  const forceSelectionGuide = qaGuide === 'background-sources';
  // selection-хук объявлен первым: его dismissed-значение нужно для idle-условия
  // ГАЙДА ВЫШЕ по цепочке (mode) — «эта подсказка ещё актуальна, если дальше по
  // цепочке ещё не ушли», иначе после простоя может вернуться уже пройденный шаг.
  const [selectionGuideDismissed, setSelectionGuideDismissed] = useGuideDismiss('background-selection', !hasBackground && (!isMedia || !loading));
  const [modeGuideDismissed, setModeGuideDismissed] = useGuideDismiss('background-mode', !hasBackground && !selectionGuideDismissed);

  useEffect(() => {
    setModeGuideDismissed(qaGuide === 'background-sources');
    setSelectionGuideDismissed(false);
  }, [qaGuide]);

  const showModeGuide = (forceModeGuide || !hasBackground) && !modeGuideDismissed;
  const showSelectionGuide = (forceSelectionGuide || !hasBackground) && modeGuideDismissed && !selectionGuideDismissed && (!isMedia || !loading);

  useScrollGuideIntoView(showSelectionGuide, selectionGuideTargetRef);

  const format = background.mode === 'photo' ? '4:3' : background.footageType === 'cine16x9' && background.mode === 'footage' ? '16:9' : '9:16';
  const pickVibe = (name: string) => {
    toggleVibe(name, format);
  };
  const heading = loading && isMedia ? t('wizard.bg.headingShort') : t('wizard.bg.heading');
  const panelTitle = {
    footage: t('wizard.bg.typeFootage'),
    photo: t('wizard.bg.typePhoto'),
    color: t('wizard.bg.typeColor')
  }[background.mode];

  return (
    <div className="flex h-full flex-col">
      {/* Figma W12: слева заголовок, справа «Загрузить футажи» (иконка 20 + текст 24) */}
      <div className="flex items-center justify-between gap-space-4 max-md:gap-[10px]">
        <h2 className="wizard-h flex min-w-0 items-center gap-space-3 whitespace-nowrap max-md:truncate">
          <BgSquaresIcon color="var(--accent-light)" />
          {heading}
        </h2>
        {background.mode === 'footage' && meQuery.data?.capabilities?.customSources && (
          <button
            type="button"
            onClick={() => setSourcesOpen(true)}
            className={cn('wizard-body flex h-[44px] shrink-0 items-center gap-[10px] whitespace-nowrap rounded-r15 px-[16px] transition hover:text-text max-md:h-[32px] max-md:gap-[6px] max-md:rounded-r10 max-md:bg-grad-soft-20 max-md:px-[10px] max-md:!text-[13px]', background.sourceVideos.length > 0 && 'border border-accent-light bg-grad-soft-20 !text-text')}
          >
            <SvgMaskIcon src="/assets/figma/bg-upload.svg" style={{ width: 20, height: 20, color: WHITE80 }} />
            {background.sourceVideos.length > 0
              ? t('wizard.bg.ownFootageCount', { count: background.sourceVideos.length })
              : <><span className="max-md:hidden">{t('wizard.bg.uploadFootage')}</span><span className="hidden max-md:inline">{t('wizard.bg.uploadShort')}</span></>}
          </button>
        )}
      </div>

      <div ref={modeGuideTargetRef} className="mt-[20px] max-md:mt-[14px]">
        <ModeSwitch />
      </div>

      <ActionGuideOverlay
        open={showModeGuide}
        targetRef={modeGuideTargetRef}
        title={t('wizard.bg.guideModeTitle')}
        text={t('wizard.bg.guideModeText')}
        dismissLabel={t('wizard.bg.guideNext')}
        progressLabel={t('wizard.guideProgress', { current: 1, total: 2 })}
        onDismiss={() => setModeGuideDismissed(true)}
        variant={guideVariant}
        shell="track-top"
        visual={<BackgroundModeGuideVisual variant={guideGraphic} />}
      />

      <SourcesModal
        open={sourcesOpen}
        onClose={() => setSourcesOpen(false)}
      />

      <div className="relative mt-[40px] flex min-h-[382px] w-full flex-1 flex-col overflow-hidden rounded-r15 bg-grad-soft-10 pb-[40px] pt-[40px] max-md:mt-[14px] max-md:min-h-0 max-md:flex-none max-md:pb-[14px] max-md:pt-[14px]">
        <div className="flex items-center justify-between px-[40px] max-md:flex-wrap max-md:gap-[10px] max-md:px-[16px]">
          <span className={cn('wizard-body', background.mode === 'footage' && 'max-md:hidden')}>{panelTitle}</span>
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
            <div ref={selectionGuideTargetRef} className="relative mt-[12px] min-h-[253px] flex-1 max-md:mt-[8px] max-md:h-[200px] max-md:min-h-0 max-md:flex-none">
              <span className="scroll-fade-l" />
              <span className="scroll-fade-r" />
              <div
                ref={cardsScroll.ref}
                className="media-row cursor-grab select-none items-center gap-[20px] px-[40px] active:cursor-grabbing max-md:gap-[10px] max-md:px-[14px]"
                {...cardsScroll.handlers}
              >
                {list?.map((item) => (
                  <MediaCard
                    key={item.id}
                    item={item}
                    wide={background.mode === 'photo'}
                    format={format}
                    selected={selected.includes(item.name)}
                    onToggle={() => { if (!cardsScroll.moved()) pickVibe(item.name); }}
                  />
                ))}
              </div>
            </div>
          )
        ) : (
          <div ref={selectionGuideTargetRef} className="mt-[28px] flex flex-col gap-[40px]">
            <div className="px-[40px]">
              <ColorRow value={background.color} onPick={(hex) => setBackground({ color: hex })} />
            </div>
            <div className="flex min-h-[30px] items-center justify-between gap-space-4 px-[40px]">
              <span className="wizard-body flex items-center gap-space-3 leading-none">
                <SvgMaskIcon src="/assets/figma/icon-strobe.svg" className="-translate-y-px" style={{ width: 20, height: 20, color: background.strobe ? ACCENT : WHITE80 }} />
                <span className="translate-y-px">{t('wizard.bg.strobe')}</span>
                <span className="ml-space-2 flex items-center">
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
                className="media-row cursor-grab select-none items-center gap-[20px] px-[40px] active:cursor-grabbing max-md:gap-[10px] max-md:px-[14px]"
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
                    <ChipIcon label={glue.label} />
                    {chip(glue.label)}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      <ActionGuideOverlay
        open={showSelectionGuide}
        targetRef={selectionGuideTargetRef}
        title={background.mode === 'color' ? t('wizard.bg.guideColorTitle') : t('wizard.bg.guidePoolTitle')}
        text={background.mode === 'color' ? t('wizard.bg.guideColorText') : t('wizard.bg.guidePoolText')}
        dismissLabel={t('wizard.bg.guideDismiss')}
        progressLabel={t('wizard.guideProgress', { current: 2, total: 2 })}
        onDismiss={() => setSelectionGuideDismissed(true)}
        variant={guideVariant}
        shell="track-top"
        visual={background.mode === 'color' ? (
          <div className="flex w-full items-center gap-[8px]" aria-hidden="true">
            <span className="h-[34px] w-[34px] shrink-0 rounded-r9 bg-[#f6f5fd]" />
            <span className="h-[34px] w-[34px] shrink-0 rounded-r9 bg-[#05010f] ring-1 ring-text-20" />
            <span className="h-[34px] flex-1 rounded-r9" style={{ background: HUE_GRADIENT }} />
          </div>
        ) : <BackgroundPoolGuideVisual variant={guideGraphic} />}
      />
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
  const lyrics = useWizardStore((state) => state.fragmentEnabled ? state.fragmentLyrics : state.lyrics);
  // Lyrics and media plane both determine the semantic order.
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
  const currentFormat = current && background.mode === 'footage'
    ? background.footageFormats?.[current.name] ?? (background.footageType === 'cine16x9' ? '16:9' : '9:16')
    : background.mode === 'photo' ? '4:3' : '9:16';
  const activeColor = background.mode === 'color' ? background.color : undefined;
  const variations = backgroundVariations(background);
  const pills = backgroundPills(background);
  const footerPills = pills.length > 0
    ? pills
    : [{ key: background.mode, mode: background.mode, label: modes.find((item) => item.value === background.mode)?.label ?? 'wizard.bg.modeFootage', count: 0 }];

  const emptyText = { footage: t('wizard.bg.emptyFootage'), photo: t('wizard.bg.emptyPhoto'), color: t('wizard.bg.emptyColor') }[background.mode];
  const step = (delta: number) => {
    if (!selected.length) return;
    setIndex((safeIndex + delta + selected.length) % selected.length);
  };

  useEffect(() => setIndex(0), [background.mode, selectedNames.join('|')]);
  const isVideo = current ? isVideoUrl(current.previewUrl) : false;

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
        <div className="mb-space-5 flex shrink-0 flex-nowrap items-center justify-between gap-space-3 max-md:mb-[12px]">
          <h2 className="wizard-h whitespace-nowrap">{t('wizard.workZone')}</h2>
          {/* Компактная пилюля «‹ N/M ›» — тот же элемент, что на превью батча. Листать
              можно и ей, и стрелками внутри плеера: она вспомогательная. */}
          {isMedia && selected.length > 0 ? (
            <div className="flex h-[30px] shrink-0 items-center gap-[10px] rounded-[15px] px-[12px]" style={{ background: 'var(--grad-whitey)' }}>
              <button type="button" aria-label={t('wizard.bg.prevExample')} onClick={() => step(-1)} disabled={selected.length < 2} className="flex items-center transition-opacity hover:opacity-60 disabled:opacity-30">
                <SvgMaskIcon src="/assets/figma/home-arrow.svg" style={{ width: 7, height: 11, color: 'var(--accent)', transform: 'rotate(180deg)' }} />
              </button>
              <span className="text-[16px] font-[350] leading-none text-accent">{safeIndex + 1}/{selected.length}</span>
              <button type="button" aria-label={t('wizard.bg.nextExample')} onClick={() => step(1)} disabled={selected.length < 2} className="flex items-center transition-opacity hover:opacity-60 disabled:opacity-30">
                <SvgMaskIcon src="/assets/figma/home-arrow.svg" style={{ width: 7, height: 11, color: 'var(--accent)' }} />
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
        ) : currentFormat === '16:9' && background.mode === 'footage' ? (
          <div className="flex min-h-0 flex-1 items-center justify-center">
            <PreviewPlayer
              key={current?.id ?? 'empty-wide'}
              className="w-full rounded-r15 bg-grad-soft-10"
              {...playerProps}
              showSteps={playerProps.showSteps && Boolean(current)}
              onTogglePlay={current ? playerProps.onTogglePlay : undefined}
            >
              <div className="relative w-full" style={{ aspectRatio: '16 / 9' }}>
                {current ? renderMedia(current) : <div className="flex h-full items-center justify-center"><p className="wizard-body">{emptyText}</p></div>}
              </div>
              <span className="dash-panel-plain pointer-events-none absolute inset-0 z-[3]" aria-hidden="true" />
            </PreviewPlayer>
          </div>
        ) : background.mode === 'footage' ? (
          <div className="flex min-h-0 flex-1 justify-center">
            <PreviewPlayer
              className="h-full w-auto max-w-full rounded-r15 bg-grad-soft-10 max-md:h-auto max-md:w-full"
              style={{ aspectRatio: '9 / 16' }}
              {...playerProps}
              showSteps={playerProps.showSteps && Boolean(current)}
              onTogglePlay={current ? playerProps.onTogglePlay : undefined}
            >
              <div className="absolute inset-0">
                {current ? renderMedia(current) : (
                  <div className="flex h-full items-center justify-center p-space-5">
                    <p className="wizard-body max-w-[223px] text-center">{emptyText}</p>
                  </div>
                )}
              </div>
              <span className="dash-panel-plain pointer-events-none absolute inset-0 z-[3]" aria-hidden="true" />
            </PreviewPlayer>
          </div>
        ) : (
          <PreviewPlayer className="min-h-0 flex-1 rounded-r15" {...playerProps} showSteps={false} onTogglePlay={undefined}>
            <div className="absolute inset-0" style={fillStyle} />
            <span className="dash-panel-plain pointer-events-none absolute inset-0 z-[3]" aria-hidden="true" />
          </PreviewPlayer>
        )}
      </div>

      <PillsFooter
        pills={footerPills.map((pill) => ({
          key: pill.key,
          label: pill.label.startsWith('wizard.') ? t(pill.label) : chip(pill.label),
          // Figma W22: счётчик выбранных стейтов. Было «Хn» — читалось как код, а не как
          // «столько выбрано»; оставили голое число и подписали его в title.
          icon: <span className="text-[20px] font-[350] text-text-80" title={t('wizard.bg.pillCount', { count: pill.count })}>{pill.count}</span>
        }))}
        activeKey={background.mode === 'footage' ? 'footage' : background.mode}
        emptyLabel={t('wizard.bg.addNew')}
        onPill={(key) => {
          if (key === 'uploads') { setBackground({ mode: 'footage' }); return; }
          setBackground({ mode: key as BackgroundMode });
        }}
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
