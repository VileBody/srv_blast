import { useEffect, useState } from 'react';
import { create } from 'zustand';
import { cn } from '../../lib/cn';

/*
 * Мини-визуалы подсказок таймлайна FX и раскадровки «Пула». Тот же язык, что у
 * остальных гайдов: мягкое появление по очереди (guide-mode-reveal + задержки), одна
 * бегущая анимация на элемент, только transform/opacity. Цвета — палитра таймлайна
 * (кадры #553ba8, хук #c6b6ff, стиль #d3a068): гайд рисуется порталом в body, CSS-
 * переменные .fxt туда не доходят.
 */

const FRAME = '#553ba8';
const HOOK = '#c6b6ff';
const STYLE = '#d3a068';
const delay = (i: number) => `guide-mode-delay-${Math.min(7, i + 1)}`;

/** Пробегающий playhead: обёртка во всю ширину едет на свою ширину. */
function Playhead() {
  return (
    <span className="pointer-events-none absolute inset-y-[-3px] left-0 w-full guide-tl-sweep">
      <i className="absolute inset-y-0 left-0 w-[2px] rounded-full bg-white shadow-[0_0_0_1px_rgba(5,1,15,.35)]" />
    </span>
  );
}

function Frames({ widths, height = 22, cur }: { widths: number[]; height?: number; cur?: number }) {
  return (
    <span className="flex w-full gap-[3px]" style={{ height }}>
      {widths.map((w, i) => (
        <span key={i} className={cn('guide-mode-reveal rounded-[5px]', delay(i))} style={{ flexGrow: w, flexBasis: 0, background: i === cur ? '#6f55d6' : FRAME }} />
      ))}
    </span>
  );
}

/** Шаг FX 4/4: кнопка «Таймлайн» — кадры по темпу, хук на дропе, стиль на кадрах. */
export function TimelineButtonGuideVisual() {
  return (
    <div className="relative flex w-full flex-col gap-[5px]" aria-hidden="true">
      <Frames widths={[2, 1.3, 1.3, 1, 1, 1.6]} />
      <span className="relative h-[12px] w-full">
        <i className="guide-mode-reveal guide-mode-delay-5 absolute inset-y-0 rounded-[4px]" style={{ left: '30%', width: '26%', background: HOOK }} />
        <i className="guide-mode-reveal guide-mode-delay-6 absolute inset-y-0 rounded-[4px]" style={{ left: '58%', width: '42%', background: STYLE }} />
      </span>
      <Playhead />
    </div>
  );
}

/** Таймлайн 1/3: библиотека → эффект перетаскивается на дорожку. */
export function TimelineLibraryGuideVisual() {
  const rows = [HOOK, 'rgba(246,245,253,.75)', STYLE];
  return (
    <div className="relative h-[60px] w-full" aria-hidden="true">
      {rows.map((color, i) => (
        <span key={i} className={cn('guide-mode-reveal absolute left-0 flex h-[16px] w-[104px] items-center gap-[6px] rounded-[5px] bg-white/[0.06] px-[4px]', delay(i))} style={{ top: 2 + i * 20 }}>
          <i className="h-[9px] w-[9px] shrink-0 rounded-[3px]" style={{ background: color, opacity: .85 }} />
          <i className="h-[3px] flex-1 rounded-full bg-white/25" />
        </span>
      ))}
      <span className="guide-mode-reveal guide-mode-delay-4 absolute left-[120px] right-0 top-[4px] h-[22px]">
        <Frames widths={[1.4, 1, 1, 1.2]} />
      </span>
      <span className="guide-mode-reveal guide-mode-delay-5 absolute left-[120px] right-0 top-[36px] h-[20px] rounded-[5px] bg-white/[0.06]" />
      <i className="guide-tl-land absolute left-[172px] top-[38px] h-[16px] w-[64px] rounded-[4px]" style={{ background: STYLE }} />
      <span
        className="guide-tl-drag absolute left-[22px] top-[42px] h-[16px] w-[64px] rounded-[4px] shadow-[0_6px_14px_rgba(5,1,15,.45)]"
        style={{ background: STYLE, ['--guide-drag-x' as string]: '150px', ['--guide-drag-y' as string]: '-4px' }}
      />
    </div>
  );
}

/** Таймлайн 2/3: склейки по битам — стык с переходом пульсирует, граница кадра двигается. */
export function TimelineCutsGuideVisual() {
  return (
    <div className="relative flex w-full items-center" aria-hidden="true">
      <Frames widths={[1.5, 1, 1.2, 1, 1.3]} height={30} cur={1} />
      {/* Статичное центрирование и бегущие анимации — на разных слоях: анимация transform
          иначе затёрла бы translate(-50%). */}
      <span className="absolute top-1/2 h-[20px] w-[20px] -translate-x-1/2 -translate-y-1/2" style={{ left: '41.5%' }}>
        <span className="guide-mode-reveal guide-mode-delay-6 block h-full w-full">
          <span className="guide-pulse flex h-full w-full items-center justify-center rounded-full bg-[#2a2140] text-white shadow-[0_0_0_1.5px_#8b6fe6]">
            <svg viewBox="0 0 12 12" width="10" height="10" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"><path d="M2 4h7l-2-2M10 8H3l2 2" /></svg>
          </span>
        </span>
      </span>
      <span className="absolute top-1/2 h-[34px] w-[4px] -translate-y-1/2" style={{ left: '61.7%' }}>
        <span className="guide-mode-reveal guide-mode-delay-7 block h-full w-full">
          <i className="guide-fit-drag block h-full w-full rounded-full bg-white/90" />
        </span>
      </span>
    </div>
  );
}

/** Таймлайн 3/3: реже / авто / чаще — одна сетка битов, разная частота склеек. */
export function TimelinePaceGuideVisual() {
  const rows: [string, number[]][] = [
    ['Реже', [1, 1, 1]],
    ['Авто', [1.2, 1, 1, 1.1, 1]],
    ['Чаще', [1, 1, 1, 1, 1, 1, 1, 1, 1]]
  ];
  return (
    <div className="flex w-full flex-col gap-[5px]" aria-hidden="true">
      {rows.map(([label, widths], i) => (
        <span key={label} className={cn('guide-mode-reveal flex items-center gap-[8px] rounded-[6px] px-[4px] py-[2px]', delay(i * 2), i === 1 && 'bg-accent-20 shadow-[inset_0_0_0_1px_var(--accent-light)]')}>
          <span className="w-[32px] shrink-0 text-[10px] leading-none text-white/75"><span className="inline-block translate-y-px">{label}</span></span>
          <span className="flex h-[12px] flex-1 gap-[2px]">
            {widths.map((w, k) => <i key={k} className="rounded-[3px]" style={{ flexGrow: w, flexBasis: 0, background: FRAME }} />)}
          </span>
        </span>
      ))}
    </div>
  );
}

/** Пул 3/4: кадр видео сменяется, стрелки по бокам «нажимаются». */
export function StoryboardGuideVisual() {
  const arrow = (dir: 'l' | 'r') => (
    <span className={cn('guide-mode-reveal flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full bg-accent-20 text-white shadow-[inset_0_0_0_1px_var(--accent-light)]', dir === 'l' ? 'guide-mode-delay-1' : 'guide-mode-delay-3')}>
      <span className={cn('flex', dir === 'r' && 'guide-sb-press')}>
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d={dir === 'l' ? 'M14.5 6 8.5 12l6 6' : 'M9.5 6l6 6-6 6'} /></svg>
      </span>
    </span>
  );
  return (
    <div className="flex w-full items-center justify-center gap-[14px]" aria-hidden="true">
      {arrow('l')}
      <span className="guide-mode-reveal guide-mode-delay-2 relative h-[84px] w-[47px] overflow-hidden rounded-[8px] bg-black">
        <i className="guide-sb-a absolute inset-0" style={{ background: 'linear-gradient(160deg, #3b2f6e, #120b24 70%)' }} />
        <i className="guide-sb-b absolute inset-0" style={{ background: 'linear-gradient(200deg, #6a3f58, #1a0d1c 70%)' }} />
        <span className="absolute inset-x-[4px] bottom-[4px] flex h-[7px] gap-[2px]">
          {[1.2, 1, 1.4].map((w, i) => <i key={i} className="rounded-[2px] bg-white/30" style={{ flexGrow: w, flexBasis: 0 }} />)}
        </span>
      </span>
      {arrow('r')}
    </div>
  );
}

/** Пул 4/4: у кадра на экране перебираются варианты, «Готово» закрепляет. */
export function StoryboardReplaceGuideVisual() {
  return (
    <div className="flex w-full flex-col gap-[7px]" aria-hidden="true">
      <span className="guide-mode-reveal guide-mode-delay-1 flex items-center justify-between gap-[8px]">
        <span className="flex h-[22px] items-center gap-[6px] rounded-[7px] bg-white/[0.08] px-[7px] text-[11px] leading-none text-white">
          <span className="text-white/60">‹</span>
          <span className="relative h-[12px] w-[30px] overflow-hidden">
            <span className="guide-sb-a absolute inset-0 flex items-center justify-center"><span className="translate-y-px">2 / 8</span></span>
            <span className="guide-sb-b absolute inset-0 flex items-center justify-center"><span className="translate-y-px">3 / 8</span></span>
          </span>
          <span className="guide-sb-press text-white">›</span>
        </span>
        <span className="flex h-[22px] items-center gap-[5px] rounded-[7px] bg-[#5f42b9] px-[8px] text-[11px] leading-none text-white">
          <svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M8 11V8a4 4 0 0 1 8 0v3M6 11h12v9H6z" /></svg>
          <span className="translate-y-px">Готово</span>
        </span>
      </span>
      <span className="flex h-[26px] gap-[3px]">
        {[1.3, 1, 1.2, 1, 1.4].map((w, i) => (
          <span key={i} className={cn('guide-mode-reveal relative overflow-hidden rounded-[5px]', delay(i + 1), i !== 2 && 'opacity-60')} style={{ flexGrow: w, flexBasis: 0, background: FRAME }}>
            {i === 2 && <>
              <i className="guide-sb-a absolute inset-0" style={{ background: 'linear-gradient(160deg, #3b2f6e, #120b24)' }} />
              <i className="guide-sb-b absolute inset-0" style={{ background: 'linear-gradient(200deg, #6a3f58, #1a0d1c)' }} />
              <i className="absolute inset-0 rounded-[5px] shadow-[inset_0_0_0_2px_#fff]" />
            </>}
          </span>
        ))}
      </span>
    </div>
  );
}

/**
 * Кнопка «Таймлайн» есть только от md и шире (на телефоне широкого режима нет), поэтому
 * и шаг подсказки про неё — только там: иначе «Шаг 4 из 4» указывал бы в пустоту.
 */
export function useTimelineGuideAvailable(): boolean {
  const query = '(min-width: 768px)';
  const [ok, setOk] = useState(() => typeof window !== 'undefined' && window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setOk(mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return ok;
}

/**
 * Открыт ли полноэкранный таймлайн. Общий, а не локальный стейт рабочей зоны: пока он
 * открыт, подсказки шага FX (и левой панели, и рабочей зоны) прячутся — иначе они
 * висят под таймлайном одновременно с его собственными.
 */
export const useFxTimelineOpen = create<{ open: boolean; setOpen: (open: boolean) => void }>((set) => ({
  open: false,
  setOpen: (open) => set({ open })
}));
