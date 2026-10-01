import type { ReactNode } from 'react';
import { create } from 'zustand';
import { cn } from '../../lib/cn';

/*
 * Мини-визуалы подсказок раскадровки «Пула» и монтажного стола. Тот же язык, что у
 * остальных гайдов: мягкое появление по очереди (guide-mode-reveal + задержки), одна
 * бегущая анимация на элемент, только transform/opacity. Цвет кадров — палитра стола
 * (#553ba8): гайд рисуется порталом в body, CSS-переменные .fxt туда не доходят.
 */

const FRAME = '#553ba8';
const delay = (i: number) => `guide-mode-delay-${Math.min(7, i + 1)}`;

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
            <span className="guide-sb-a absolute inset-0 flex items-center justify-center"><span>2 / 8</span></span>
            <span className="guide-sb-b absolute inset-0 flex items-center justify-center"><span>3 / 8</span></span>
          </span>
          <span className="guide-sb-press text-white">›</span>
        </span>
        <span className="flex h-[22px] items-center gap-[5px] rounded-[7px] bg-[#5f42b9] px-[8px] text-[11px] leading-none text-white">
          <svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M8 11V8a4 4 0 0 1 8 0v3M6 11h12v9H6z" /></svg>
          <span>Готово</span>
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

/** Пул 5/5 и стол 2/2: дорожки ролика — кадры, хук на дропе, стиль, слова; бежит плейхед. */
export function TimelineEntryGuideVisual() {
  const lane = (children: ReactNode, i: number) => <span className={cn('guide-mode-reveal relative flex h-[14px] w-full', delay(i))}>{children}</span>;
  return (
    <div className="relative flex w-full flex-col gap-[5px]" aria-hidden="true">
      {lane(<span className="flex w-full gap-[3px]">{[1.2, 1, 1.4, 1, 1.1].map((w, i) => <i key={i} className="rounded-[4px]" style={{ flexGrow: w, flexBasis: 0, background: FRAME }} />)}</span>, 0)}
      {lane(<i className="absolute inset-y-0 left-[44%] w-[16%] rounded-[4px] bg-[#c6b6ff]" />, 1)}
      {lane(<i className="absolute inset-y-0 left-0 w-[44%] rounded-[4px] bg-[#d3a068]" />, 2)}
      {lane(<span className="flex w-full gap-[3px]">{[0.8, 1, 0.6, 1.2, 0.7, 1].map((w, i) => <i key={i} className="rounded-[4px] bg-white/20" style={{ flexGrow: w, flexBasis: 0 }} />)}</span>, 3)}
      <span className="pointer-events-none absolute inset-y-[-3px] left-0 w-full guide-tl-sweep">
        <i className="absolute inset-y-0 left-0 w-[2px] rounded-full bg-white shadow-[0_0_0_1px_rgba(5,1,15,.35)]" />
      </span>
    </div>
  );
}

/**
 * Открыт ли полноэкранный монтажный стол. Общий флаг, а не стейт «Пула»: пока стол
 * открыт, подсказки визарда под ним молчат (ActionGuideOverlay смотрит на него) — живут
 * только те, что указывают внутрь стола.
 */
export const useFxTimelineOpen = create<{ open: boolean; setOpen: (open: boolean) => void }>((set) => ({
  open: false,
  setOpen: (open) => set({ open })
}));
