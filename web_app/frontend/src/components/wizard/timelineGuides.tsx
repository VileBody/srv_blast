import { ReactNode, useEffect, useState } from 'react';
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

/* ── тур таймлайна v2: курсор показывает само действие ─────────────────────── */

/** Курсор-стрелка: белый с тёмной обводкой, видно на любой подложке. */
function Cursor({ className }: { className: string }) {
  return (
    <svg className={cn('gt-cur', className)} viewBox="0 0 14 16" aria-hidden="true">
      <path d="M1.5 1.5v11.2l3.1-2.7 2 4.4 2-.9-2-4.3 4.2-.2z" fill="#fff" stroke="#140e24" strokeWidth="1.1" strokeLinejoin="round" />
    </svg>
  );
}
const Box = ({ h = 76, children }: { h?: number; children: ReactNode }) => (
  <div className="relative w-full overflow-hidden" style={{ height: h }} aria-hidden="true">{children}</div>
);
const txt = 'absolute leading-none text-[10px]';

/** 0 (прототип). Варианты: открыть список, выбрать другой; «+» — новый вариант. */
export function TimelineVariantsGuideVisual() {
  return (
    <Box>
      <span className="absolute left-[150px] top-[4px] flex h-[22px] w-[40px] items-center justify-end rounded-r-[8px] bg-accent-20 pr-[9px] text-[13px] leading-none text-white"><span className="gt0-plus inline-block">+</span></span>
      <span className="absolute left-[4px] top-[4px] flex h-[22px] w-[160px] items-center gap-[6px] rounded-[8px] bg-[#1d1533] px-[8px] shadow-[inset_0_0_0_1px_rgba(246,245,253,.13)]">
        <span className="relative h-full flex-1">
          <span className="gt0-a absolute inset-0 flex items-center gap-[6px] text-[10px] leading-none text-white"><i className="h-[7px] w-[7px] rounded-full bg-[#8b6fe6]" /><span className="translate-y-px">Молния · Неон</span></span>
          <span className="gt0-b absolute inset-0 flex items-center gap-[6px] text-[10px] leading-none text-white"><i className="h-[7px] w-[7px] rounded-full bg-[#e38fb5]" /><span className="translate-y-px">Звезда · Ч/Б</span></span>
        </span>
        <span className="text-[9px] text-white/60">▾</span>
      </span>
      <span className="gt0-menu absolute left-[4px] top-[30px] flex w-[170px] flex-col gap-[2px] rounded-[8px] bg-[#1b1430] p-[3px] shadow-[0_8px_18px_rgba(0,0,0,.45)] ring-1 ring-white/10">
        <span className="flex h-[17px] items-center gap-[6px] rounded-[5px] bg-[#1d1533] px-[6px] text-[10px] leading-none text-white"><i className="h-[6px] w-[6px] rounded-full bg-[#8b6fe6]" /><span className="flex-1 translate-y-px">Молния · Неон</span><span className="text-[#8b6fe6]">✓</span></span>
        <span className="flex h-[17px] items-center gap-[6px] rounded-[5px] bg-white/[0.06] px-[6px] text-[10px] leading-none text-white/85"><i className="h-[6px] w-[6px] rounded-full bg-[#e38fb5]" /><span className="translate-y-px">Звезда · Ч/Б</span></span>
      </span>
      <Cursor className="gt0-cur" />
    </Box>
  );
}

/** 1. Библиотека: ▶ — пример в превью справа; перетащить строку на дорожку — эффект встал. */
export function TimelineLibraryGuideVisual() {
  return (
    <Box>
      <span className="absolute left-[4px] top-[4px] flex h-[24px] w-[146px] items-center gap-[7px] rounded-[7px] bg-white/[0.06] pl-[6px]">
        <i className="h-[14px] w-[14px] rounded-full" style={{ background: 'rgba(198,182,255,.2)' }} />
        <span className="translate-y-px text-[10px] leading-none text-white">Молния</span>
        <span className="absolute left-[110px] top-[4px] flex h-[16px] w-[16px] items-center justify-center rounded-full bg-white/10 text-[8px] text-white">▶</span>
        <span className="absolute left-[130px] top-[4px] flex h-[16px] w-[14px] items-center justify-center text-[11px] text-white/70">+</span>
      </span>
      <span className="absolute left-[244px] top-[4px] h-[56px] w-[32px] overflow-hidden rounded-[6px] bg-[#0b0718] ring-1 ring-white/10">
        <i className="gt1-flash absolute inset-0" style={{ background: 'linear-gradient(170deg, #c6b6ff, #5f42b9 60%, #140e24)' }} />
        <span className="absolute inset-x-0 bottom-[3px] text-center text-[7px] leading-none text-white/70">превью</span>
      </span>
      <span className="absolute left-[4px] top-[48px] h-[22px] w-[232px] rounded-[6px] bg-[#0b0718]" />
      <span className={cn(txt, 'left-[10px] top-[56px] text-white/40')}>Хук</span>
      <span className="gt1-land absolute left-[102px] top-[50px] flex h-[18px] w-[52px] items-center justify-center rounded-[5px] text-[9px] leading-none" style={{ background: HOOK, color: '#170c38' }}><span className="translate-y-px">Молния</span></span>
      <span className="gt1-ghost absolute left-[20px] top-[6px] flex h-[18px] w-[52px] items-center justify-center rounded-[5px] text-[9px] leading-none shadow-[0_6px_14px_rgba(5,1,15,.5)]" style={{ background: HOOK, color: '#170c38' }}><span className="translate-y-px">Молния</span></span>
      <Cursor className="gt1-cur" />
    </Box>
  );
}

/** 2. Склейки: кружок между кадрами → выбрать переход; потянуть кружок → склейка сдвинулась. */
export function TimelineCutsGuideVisual() {
  return (
    <Box>
      <span className="gt2-pop absolute left-[68px] top-[2px] flex gap-[3px] rounded-[7px] bg-[#1b1430] p-[3px] shadow-[0_8px_18px_rgba(0,0,0,.45)] ring-1 ring-white/10">
        {['Щелчок', 'Минимакс', 'Вспышка'].map((l, i) => (
          <span key={l} className="relative flex h-[18px] items-center rounded-[5px] bg-white/[0.06] px-[5px] text-[9px] leading-none text-white/80">
            {i === 1 && <i className="gt2-pick absolute inset-0 rounded-[5px] bg-accent-light/50 ring-1 ring-accent-light" />}
            <span className="relative translate-y-px">{l}</span>
          </span>
        ))}
      </span>
      <span className="absolute inset-x-0 top-[38px] h-[24px] overflow-hidden">
        <span className="absolute left-0 top-0 h-full w-[90px] rounded-[5px]" style={{ background: FRAME }} />
        <span className="gt2-grow absolute left-[94px] top-0 h-full w-[88px] rounded-[5px]" style={{ background: FRAME }} />
        <span className="gt2-move absolute left-[186px] top-0 h-full w-[120px] rounded-[5px]" style={{ background: FRAME }} />
      </span>
      <span className="absolute left-[84px] top-[42px] flex h-[16px] w-[16px] items-center justify-center rounded-full bg-[#0b0718] text-[10px] text-white/80 ring-2 ring-[#0b0718]">
        <span className="absolute">+</span>
        <span className="gt2-icon absolute flex h-full w-full items-center justify-center rounded-full bg-accent text-[8px] text-white">◇</span>
      </span>
      <span className="gt2-move absolute left-[176px] top-[42px] flex h-[16px] w-[16px] items-center justify-center rounded-full bg-[#0b0718] text-[10px] text-white/80 ring-2 ring-[#0b0718]">+</span>
      <span className={cn(txt, 'left-[4px] top-[66px] text-white/40')}>кадр 1</span>
      <span className={cn(txt, 'left-[98px] top-[66px] text-white/40')}>кадр 2</span>
      <Cursor className="gt2-cur" />
    </Box>
  );
}

/** 3. Хук: всегда на дропе, сдвинуть нельзя; у слоу-шаттера тянется правый край. */
export function TimelineHookGuideVisual() {
  return (
    <Box>
      <span className="absolute left-[102px] top-[2px] rounded-[5px] bg-[#c6b6ff] px-[5px] py-[2px] text-[8px] leading-none text-[#170c38]">дроп</span>
      <span className="absolute left-[120px] top-[16px] h-[56px] w-[2px] bg-[#c6b6ff]/80" />
      <span className="absolute left-0 right-0 top-[28px] h-[22px] rounded-[6px] bg-[#0b0718]" />
      <span className="gt3-grow absolute left-[121px] top-[30px] h-[18px] w-[36px] rounded-[5px]" style={{ background: HOOK }} />
      <span className="absolute left-[125px] top-[35px] flex items-center gap-[4px] text-[8px] leading-none text-[#170c38]"><svg viewBox="0 0 12 12" width="8" height="8" fill="none" aria-hidden="true"><path d="M3.5 5.5V4a2.5 2.5 0 0 1 5 0v1.5M2.5 5.5h7v5h-7z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></svg><span className="translate-y-px">Слоу-шаттер</span></span>
      <span className={cn(txt, 'left-[8px] top-[36px] text-white/40')}>до дропа</span>
      <Cursor className="gt3-cur" />
    </Box>
  );
}

/** 4. Стили: перетащить из библиотеки на кадр; потянуть край — на соседние кадры. */
export function TimelineStylesGuideVisual() {
  return (
    <Box>
      <span className="absolute left-[8px] top-[4px] flex h-[18px] w-[58px] items-center justify-center rounded-[5px] bg-white/[0.08] text-[9px] leading-none text-white"><span className="translate-y-px">Неон</span></span>
      <span className="gt4-ghost absolute left-[8px] top-[4px] flex h-[18px] w-[58px] items-center justify-center rounded-[5px] text-[9px] leading-none shadow-[0_6px_14px_rgba(5,1,15,.5)]" style={{ background: STYLE, color: '#2b1906' }}><span className="translate-y-px">Неон</span></span>
      <span className="absolute inset-x-0 top-[28px] flex h-[12px] gap-[3px]">
        {[0, 1, 2].map((i) => <i key={i} className="flex-1 rounded-[3px]" style={{ background: FRAME, opacity: .7 }} />)}
      </span>
      <span className="absolute inset-x-0 top-[44px] h-[22px] rounded-[6px] bg-[#0b0718]" />
      <span className="gt4-land absolute left-[96px] top-[46px] flex h-[18px] w-[84px] items-center rounded-[5px] pl-[6px] text-[9px] leading-none" style={{ background: STYLE, color: '#2b1906' }}><span className="translate-y-px">Неон</span></span>
      <span className={cn(txt, 'left-[8px] top-[51px] text-white/40')}>Стиль 1</span>
      <Cursor className="gt4-cur" />
    </Box>
  );
}

/** 5. Темп: реже · авто · чаще — одна сетка битов, разное число склеек. */
export function TimelinePaceGuideVisual() {
  const phases: [string, number][] = [['Реже', 3], ['Авто', 5], ['Чаще', 9]];
  const cls = ['guide-hook-icon-a', 'guide-hook-icon-b', 'guide-hook-icon-c'];
  return (
    <Box h={58}>
      <span className="absolute left-1/2 top-[2px] flex -translate-x-1/2 gap-[2px] rounded-[8px] bg-[#0b0718] p-[2px] ring-1 ring-white/10">
        {phases.map(([l], i) => (
          <span key={l} className="relative flex h-[20px] w-[58px] items-center justify-center rounded-[6px] text-[10px] leading-none text-white/60">
            <i className={cn(cls[i], 'absolute inset-0 rounded-[6px] bg-accent')} />
            <span className="relative translate-y-px text-white">{l}</span>
          </span>
        ))}
      </span>
      {phases.map(([l, n], i) => (
        <span key={l} className={cn(cls[i], 'absolute inset-x-0 top-[34px] flex h-[18px] gap-[3px]')}>
          {Array.from({ length: n }, (_, k) => <i key={k} className="flex-1 rounded-[4px]" style={{ background: FRAME }} />)}
        </span>
      ))}
    </Box>
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
