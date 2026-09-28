import { type ReactNode, type RefObject, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '../../lib/cn';

type TargetBox = {
  top: number;
  right: number;
  bottom: number;
  left: number;
  width: number;
  height: number;
  scale: number;
};

type ActionGuideOverlayProps = {
  open: boolean;
  targetRef: RefObject<HTMLElement>;
  title: string;
  text: string;
  visual: ReactNode;
  dismissLabel: string;
  progressLabel: string;
  onDismiss: () => void;
  variant?: ActionGuideVariant;
  shell?: ActionGuideShell;
};

export type ActionGuideVariant = 'minimal' | 'balanced' | 'visual';
export type ActionGuideShell = 'standard' | 'creative' | 'track' | 'track-reverse' | 'track-wide' | 'track-top';

const TARGET_GAP = 8;
const VIEWPORT_GAP = 12;
const CARD_WIDTH = 230;
const VISUAL_CARD_WIDTH = 250;
const CREATIVE_CARD_WIDTH = 330;
const TRACK_CARD_WIDTH = 450;
const TRACK_WIDE_CARD_WIDTH = 450;
const CARD_ESTIMATED_HEIGHT = 240;

/**
 * Fixed tutorial layer. It only reads the target geometry and never inserts
 * anything into the product layout, so opening a guide cannot move the UI.
 */
export function ActionGuideOverlay({
  open,
  targetRef,
  title,
  text,
  visual,
  dismissLabel,
  progressLabel,
  onDismiss,
  variant = 'balanced',
  shell = 'standard'
}: ActionGuideOverlayProps) {
  const [target, setTarget] = useState<TargetBox | null>(null);
  const [cardHeight, setCardHeight] = useState(CARD_ESTIMATED_HEIGHT);
  const cardRef = useRef<HTMLElement>(null);
  const dimRef = useRef<HTMLDivElement>(null);
  // Скроллим к цели только один раз за время жизни этого гайда (первое
  // открытие). Реактивация после 45с простоя больше НЕ скроллит: цель и так
  // почти всегда в зоне видимости, а принудительный скролл посреди того, как
  // юзер читает что-то другое на странице, выглядит как будто у него из-под
  // курсора выдёргивают экран.
  const hasScrolledRef = useRef(false);

  useLayoutEffect(() => {
    if (!open || !cardRef.current) return;

    const card = cardRef.current;
    const updateCardHeight = () => setCardHeight(card.getBoundingClientRect().height);
    updateCardHeight();

    const observer = new ResizeObserver(updateCardHeight);
    observer.observe(card);
    return () => observer.disconnect();
  }, [open, title, text, visual]);

  useEffect(() => {
    if (!open) {
      setTarget(null);
      return;
    }

    const element = targetRef.current;
    if (!element) return;

    const scrollFrame = hasScrolledRef.current ? 0 : window.requestAnimationFrame(() => {
      // Мгновенное позиционирование исключает конфликт двух анимаций: нативный
      // smooth-scroll и покадровое слежение обводки раньше двигали цель с разной
      // частотой, из-за чего рамка заметно дрожала во всех гайдах.
      element.scrollIntoView({ behavior: 'auto', block: 'center', inline: 'nearest' });
    });
    hasScrolledRef.current = true;
    // Вырез красится НАПРЯМУЮ в DOM через ref, в обход React state — иначе каждый
    // кадр скролла идёт через setState → commit → paint, и вырез на 1-2 кадра
    // отстаёт от реального контента (виден дребезг). Это ровно тот паттерн, который
    // прямым текстом запрещён в taste-skill: «rAF-цикл, трогающий React state» —
    // используем state только для карточки (ей point-perfect трекинг не нужен), а
    // вырез красим императивно на КАЖДОМ кадре измерения.
    const paintDim = (t: TargetBox) => {
      const el = dimRef.current;
      if (!el) return;
      const pixelScale = t.scale * (window.devicePixelRatio || 1);
      const snap = (value: number) => Math.round(value * pixelScale) / pixelScale;
      const hTop = snap((t.top - TARGET_GAP) / t.scale);
      const hLeft = snap((t.left - TARGET_GAP) / t.scale);
      const hWidth = snap((t.width + TARGET_GAP * 2) / t.scale);
      const hHeight = snap((t.height + TARGET_GAP * 2) / t.scale);
      el.style.width = `${hWidth}px`;
      el.style.height = `${hHeight}px`;
      el.style.transform = `translate3d(${hLeft}px, ${hTop}px, 0)`;
    };
    // rAF-троттлинг, не debounce: cancelAnimationFrame+переназначение на каждый 'scroll'
    // превращало это в debounce — при непрерывном скролле (события чаще кадра) кадр ни разу
    // не успевал выполниться, рамка замирала и лишь «догоняла» скачком, когда скролл стихал.
    // Флаг ticking гарантирует ровно один measure на кадр.
    let ticking = false;
    const measure = () => {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(() => {
        ticking = false;
        const rect = element.getBoundingClientRect();
        let visibleTop = rect.top;
        let visibleRight = rect.right;
        let visibleBottom = rect.bottom;
        let visibleLeft = rect.left;
        let ancestor = element.parentElement;
        while (ancestor) {
          const styles = window.getComputedStyle(ancestor);
          const clipsX = styles.overflowX === 'hidden' || styles.overflowX === 'clip' || styles.overflowX === 'auto' || styles.overflowX === 'scroll';
          const clipsY = styles.overflowY === 'hidden' || styles.overflowY === 'clip' || styles.overflowY === 'auto' || styles.overflowY === 'scroll';
          if (clipsX || clipsY) {
            const ancestorRect = ancestor.getBoundingClientRect();
            if (clipsX) {
              visibleLeft = Math.max(visibleLeft, ancestorRect.left + TARGET_GAP);
              visibleRight = Math.min(visibleRight, ancestorRect.right - TARGET_GAP);
            }
            if (clipsY) {
              visibleTop = Math.max(visibleTop, ancestorRect.top + TARGET_GAP);
              visibleBottom = Math.min(visibleBottom, ancestorRect.bottom - TARGET_GAP);
            }
          }
          ancestor = ancestor.parentElement;
        }
        const parsedScale = Number.parseFloat(window.getComputedStyle(document.documentElement).zoom);
        const scale = Number.isFinite(parsedScale) && parsedScale > 0 ? parsedScale : 1;
        const deviceScale = window.devicePixelRatio || 1;
        const snapVisual = (value: number) => Math.round(value * deviceScale) / deviceScale;
        const top = snapVisual(visibleTop);
        const right = snapVisual(visibleRight);
        const bottom = snapVisual(visibleBottom);
        const left = snapVisual(visibleLeft);
        const nextTarget = {
          top,
          right,
          bottom,
          left,
          width: Math.max(0, right - left),
          height: Math.max(0, bottom - top),
          scale
        };
        paintDim(nextTarget);
        setTarget((previous) => previous
          && previous.top === nextTarget.top
          && previous.right === nextTarget.right
          && previous.bottom === nextTarget.bottom
          && previous.left === nextTarget.left
          && previous.scale === nextTarget.scale
          ? previous
          : nextTarget);
      });
    };

    measure();
    // Host surfaces can enter with a transform animation. ResizeObserver does
    // not fire when an ancestor transform ends, so take one settled reading too.
    const settleTimer = window.setTimeout(measure, 450);
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    window.addEventListener('resize', measure);
    document.addEventListener('scroll', measure, true);

    return () => {
      window.cancelAnimationFrame(scrollFrame);
      window.clearTimeout(settleTimer);
      observer.disconnect();
      window.removeEventListener('resize', measure);
      document.removeEventListener('scroll', measure, true);
    };
  }, [open, targetRef]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onDismiss();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onDismiss, open]);

  if (!open || !target) return null;

  // getBoundingClientRect returns visual pixels after the app-level CSS zoom,
  // while fixed style values are zoomed once more. Position in visual pixels,
  // then convert the final values back to CSS pixels exactly once.
  const isCreative = shell === 'creative';
  const isTrack = shell === 'track';
  const isTrackReverse = shell === 'track-reverse';
  const isTrackWide = shell === 'track-wide';
  const isTrackTop = shell === 'track-top';
  const isTrackShell = isTrack || isTrackReverse || isTrackWide || isTrackTop;
  const width = Math.min(isTrackTop ? 330 : isTrackWide ? TRACK_WIDE_CARD_WIDTH : isTrackShell ? TRACK_CARD_WIDTH : isCreative ? CREATIVE_CARD_WIDTH : variant === 'visual' ? VISUAL_CARD_WIDTH : CARD_WIDTH, window.innerWidth - VIEWPORT_GAP * 2);
  const left = Math.min(
    Math.max(VIEWPORT_GAP, target.left + target.width / 2 - width / 2),
    window.innerWidth - width - VIEWPORT_GAP
  );
  const roomAbove = target.top;
  const roomBelow = window.innerHeight - target.bottom;
  const roomLeft = target.left;
  const placement = roomAbove >= cardHeight + 20
    ? 'above'
    : roomBelow >= cardHeight + 20
      ? 'below'
      : roomLeft >= width + 20
        ? 'left'
        : 'inside';
  const verticalCenter = Math.min(
    Math.max(VIEWPORT_GAP, target.top + target.height / 2 - cardHeight / 2),
    window.innerHeight - cardHeight - VIEWPORT_GAP
  );
  const cardStyle = placement === 'above'
    ? { bottom: (window.innerHeight - target.top + 20) / target.scale, left: left / target.scale, width: width / target.scale }
    : placement === 'below'
      ? { top: (target.bottom + 20) / target.scale, left: left / target.scale, width: width / target.scale }
      : placement === 'left'
        ? { top: verticalCenter / target.scale, left: (target.left - width - 20) / target.scale, width: width / target.scale }
        : { top: (target.top + 20) / target.scale, left: left / target.scale, width: width / target.scale };

  const isMinimal = variant === 'minimal';
  const isVisual = variant === 'visual';
  const pixelScale = target.scale * (window.devicePixelRatio || 1);
  const snapToPixel = (value: number) => Math.round(value * pixelScale) / pixelScale;
  const dimTop = snapToPixel((target.top - TARGET_GAP) / target.scale);
  const dimLeft = snapToPixel((target.left - TARGET_GAP) / target.scale);
  const dimWidth = snapToPixel((target.width + TARGET_GAP * 2) / target.scale);
  const dimHeight = snapToPixel((target.height + TARGET_GAP * 2) / target.scale);
  const content = (
    <>
      <h3 className={cn(
        'text-text',
        isMinimal ? 'text-[15px] font-[400] leading-[18px]' : 'text-[17px] font-[400] leading-[20px] tracking-[-0.01em]'
      )}>{title}</h3>

      <p className={cn(
        'font-[350] text-text-80',
        isMinimal ? 'mt-[4px] text-[12px] leading-[16px]' : 'mt-[6px] text-[14px] leading-[19px]'
      )}>{text}</p>
    </>
  );

  const visualBlock = (
    <div className={cn(
      'flex items-center overflow-hidden',
      isMinimal
        ? 'mt-[8px] min-h-[34px] border-t border-[rgba(246,245,253,0.14)] pt-[8px]'
        : isVisual
          ? 'mb-[22px] min-h-[78px] rounded-r12 bg-[rgba(246,245,253,0.07)] px-[14px]'
          : 'mt-[10px] min-h-[46px] rounded-r12 bg-[rgba(246,245,253,0.07)] px-[11px]'
    )}>
      {visual}
    </div>
  );

  return createPortal(
    <div className="pointer-events-none fixed inset-0 z-guidance" aria-live="polite">
      {/* Дим на весь экран, КРОМЕ целевой зоны — trick через огромный spread
          box-shadow: тень «заливает» всё вокруг элемента, а сам элемент
          (ровно по размеру цели) остаётся прозрачным. Без рамки/обводки —
          только контраст света и тени, без явного контура вокруг зоны. */}
      <div
        ref={dimRef}
        aria-hidden="true"
        className="absolute left-0 top-0 rounded-r20 shadow-[0_0_0_9999px_rgba(5,1,15,0.72)] will-change-transform"
        style={{
          width: dimWidth,
          height: dimHeight,
          transform: `translate3d(${dimLeft}px, ${dimTop}px, 0)`
        }}
      />

      <section
        ref={cardRef}
        role="dialog"
        aria-modal="false"
        aria-label={title}
        className={cn(
          'action-guide-enter pointer-events-auto fixed overflow-hidden border bg-[#181126] shadow-[0_18px_48px_rgba(8,3,20,0.48)]',
          isTrackShell
            ? 'rounded-[18px] border-white/20 bg-[#171024] p-[12px]'
            : isCreative
            ? 'rounded-[18px] border-white/20 bg-[#171024] p-[12px]'
            : isMinimal
            ? 'rounded-r12 border-[rgba(246,245,253,0.18)] p-[10px]'
            : isVisual
              ? 'rounded-r15 border-[rgba(139,111,230,0.42)] p-[16px]'
              : 'rounded-r15 border-[rgba(139,111,230,0.42)] p-[14px]'
        )}
        style={cardStyle}
      >
        {isTrackTop ? (
          <div className="flex flex-col gap-[14px]">
            <div className="flex min-h-[76px] items-center overflow-hidden rounded-[14px] border border-white/15 bg-white/[0.07] p-[10px] shadow-[inset_0_1px_0_rgba(255,255,255,.06)]">
              {visual}
            </div>
            <div className="px-[2px]">
              <div className="flex items-center justify-start gap-[8px]">
                <h3 className="min-w-0 text-[18px] font-[400] leading-[21px] tracking-[-0.015em] text-text">{title}</h3>
                <span className="-translate-y-px shrink-0 rounded-[7px] bg-white/[0.07] px-[7px] py-[4px] text-[10px] font-[350] text-text-60"><span className="inline-block translate-y-px">{progressLabel}</span></span>
              </div>
              <p className="mt-[9px] text-[15px] font-[350] leading-[20px] text-text-80">{text}</p>
            </div>
            <button
              type="button"
              onClick={onDismiss}
              className="flex h-[38px] w-full min-w-[88px] items-center justify-center whitespace-nowrap rounded-[9px] bg-[#7458c7] px-[14px] text-[15px] font-[400] text-white transition hover:bg-[#8266d8] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-light active:scale-[0.98]"
            >
              <span className="action-guide-optical-text action-guide-creative-button-text">{dismissLabel}</span>
            </button>
          </div>
        ) : isTrack || isTrackReverse ? (
          <div className={cn('grid min-h-[166px] gap-[14px]', isTrack ? 'grid-cols-[minmax(0,1fr)_170px]' : 'grid-cols-[170px_minmax(0,1fr)]')}>
            <div className={cn('flex min-w-0 flex-col justify-center gap-[10px] p-[2px]', isTrackReverse && 'order-2')}>
              <span className="action-guide-creative-progress w-fit rounded-[7px] bg-white/[0.07] px-[7px] py-[4px] text-[10px] font-[350] text-text-60">{progressLabel}</span>
              <div>
                <h3 className="text-[18px] font-[400] leading-[21px] tracking-[-0.015em] text-text">{title}</h3>
                <p className="mt-[7px] text-[15px] font-[350] leading-[20px] text-text-80">{text}</p>
              </div>
              <button
                type="button"
                onClick={onDismiss}
                className="flex h-[38px] w-fit min-w-[88px] items-center justify-center whitespace-nowrap rounded-[9px] bg-[#7458c7] px-[14px] text-[15px] font-[400] text-white transition hover:bg-[#8266d8] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-light active:scale-[0.98]"
              >
                <span className="action-guide-optical-text action-guide-creative-button-text">{dismissLabel}</span>
              </button>
            </div>
            <div className={cn('flex min-w-0 items-center overflow-visible rounded-[14px] border border-white/15 bg-white/[0.07] p-[10px] shadow-[inset_0_1px_0_rgba(255,255,255,.06)]', isTrackReverse && 'order-1')}>
              {visual}
            </div>
          </div>
        ) : isTrackWide ? (
          <div className="grid min-h-[150px] grid-cols-[minmax(0,1fr)_92px] gap-x-[14px] gap-y-[10px]">
            <div className="min-w-0 px-[2px] pt-[2px]">
              <div className="flex items-center gap-[10px]">
                <span className="action-guide-creative-progress shrink-0 rounded-[7px] bg-white/[0.07] px-[7px] py-[4px] text-[10px] font-[350] text-text-60">{progressLabel}</span>
                <h3 className="min-w-0 text-[18px] font-[400] leading-[21px] tracking-[-0.015em] text-text">{title}</h3>
              </div>
              <p className="mt-[7px] text-[15px] font-[350] leading-[20px] text-text-80">{text}</p>
            </div>
            <button
              type="button"
              onClick={onDismiss}
              className="flex h-[38px] min-w-[88px] items-center justify-center self-start whitespace-nowrap rounded-[9px] bg-[#7458c7] px-[14px] text-[15px] font-[400] text-white transition hover:bg-[#8266d8] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-light active:scale-[0.98]"
            >
              <span className="action-guide-optical-text action-guide-creative-button-text">{dismissLabel}</span>
            </button>
            <div className="col-span-2 flex min-h-[68px] items-center overflow-hidden rounded-[14px] border border-white/15 bg-white/[0.07] p-[10px] shadow-[inset_0_1px_0_rgba(255,255,255,.06)]">
              {visual}
            </div>
          </div>
        ) : isCreative ? (
          <div className="grid min-h-[142px] grid-cols-[minmax(0,1fr)_116px] gap-[12px]">
            <div className="flex min-w-0 flex-col justify-center gap-[10px] p-[2px]">
              <span className="action-guide-creative-progress w-fit rounded-[7px] bg-white/[0.07] px-[7px] py-[4px] text-[10px] font-[350] text-text-60">{progressLabel}</span>
              <div>
                <h3 className="text-[18px] font-[400] leading-[21px] tracking-[-0.015em] text-text">{title}</h3>
                <p className="mt-[6px] text-[15px] font-[350] leading-[20px] text-text-80">{text}</p>
              </div>
              <button
                type="button"
                onClick={onDismiss}
                className="flex h-[38px] w-fit min-w-[88px] items-center justify-center whitespace-nowrap rounded-[9px] bg-[#7458c7] px-[14px] text-[15px] font-[400] text-white transition hover:bg-[#8266d8] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-light active:scale-[0.98]"
              >
                <span className="action-guide-optical-text action-guide-creative-button-text">{dismissLabel}</span>
              </button>
            </div>
            <div className="flex min-w-0 items-center overflow-hidden rounded-[14px] border border-white/15 bg-white/[0.07] p-[10px] shadow-[inset_0_1px_0_rgba(255,255,255,.06)]">
              {visual}
            </div>
          </div>
        ) : (
          <>
            {isVisual && visualBlock}
            {content}
            {!isVisual && visualBlock}

            <div className={cn('flex items-center justify-between gap-[12px]', isVisual ? 'mt-[14px]' : 'mt-[10px]')}>
              <span className="text-[11px] font-[350] text-text-60">{progressLabel}</span>
              <button
                type="button"
                onClick={onDismiss}
                className={cn(
                  'flex items-center justify-center whitespace-nowrap font-[400] transition hover:brightness-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-light active:scale-[0.98]',
                  isMinimal
                    ? 'h-[28px] rounded-r9 bg-[#7458c7] px-[10px] text-[11px] text-white hover:bg-[#8266d8]'
                    : 'h-[32px] min-w-[76px] rounded-r10 bg-[#7458c7] px-[14px] text-[12px] text-white'
                )}
              >
                <span className="action-guide-optical-text">{dismissLabel}</span>
              </button>
            </div>
          </>
        )}

        <span
          aria-hidden="true"
          className={placement === 'above'
            ? 'absolute bottom-[-7px] left-1/2 h-[14px] w-[14px] -translate-x-1/2 rotate-45 border-b border-r border-[rgba(139,111,230,0.42)] bg-[#181126]'
            : placement === 'left'
              ? 'absolute right-[-7px] top-1/2 h-[14px] w-[14px] -translate-y-1/2 rotate-45 border-r border-t border-[rgba(139,111,230,0.42)] bg-[#181126]'
              : 'absolute left-1/2 top-[-7px] h-[14px] w-[14px] -translate-x-1/2 rotate-45 border-l border-t border-[rgba(139,111,230,0.42)] bg-[#181126]'}
        />
      </section>
    </div>,
    document.body
  );
}
