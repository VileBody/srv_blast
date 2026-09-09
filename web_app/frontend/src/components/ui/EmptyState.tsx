import type { ReactNode } from 'react';

export function EmptyState({ icon, title, text, cta, onAction }: { icon: ReactNode; title: string; text: string; cta?: string; onAction?: () => void }) {
  return (
    <div className="flex h-full min-h-[260px] flex-col items-center justify-center px-[28px] text-center">
      <div className="flex h-[60px] w-[60px] items-center justify-center rounded-r15 bg-grad-soft-20 text-[28px] text-accent-light" aria-hidden="true">{icon}</div>
      <h3 className="mt-[20px] text-[24px] font-[400] leading-[29px] text-text">{title}</h3>
      <p className="mt-[10px] max-w-[420px] text-[16px] font-[350] leading-[20px] text-text-60">{text}</p>
      {cta && (
        <button type="button" onClick={onAction} className="mt-[24px] flex h-[60px] items-center justify-center rounded-r15 border border-accent-light bg-grad-soft-20 px-[28px] text-[20px] font-[350] leading-none text-text-80 transition hover:text-text">
          {cta}
        </button>
      )}
    </div>
  );
}
