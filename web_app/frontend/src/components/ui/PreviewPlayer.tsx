import { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '../../lib/cn';

/**
 * Рамка превью с управлением ВНУТРИ кадра: плей по центру и стрелки по бокам.
 *
 * Один компонент на три места (фон в визарде, пул, превью батча) — раньше эта разметка
 * жила копиями, и навигация в каждом месте выглядела по-своему. Что именно играет и куда
 * листают стрелки, решает вызывающий: в визарде это отрывок трека поверх футажа, на батче —
 * готовый ролик.
 *
 * Счётчик здесь НЕ рисуем: он живёт компактной пилюлей в шапке карточки (общий приём для
 * всех превью), а внутри кадра остаются только действия.
 */
export function PreviewPlayer({
  children,
  playing,
  onTogglePlay,
  playLabel,
  pauseLabel,
  onPrev,
  onNext,
  showSteps = true,
  className
}: {
  children: ReactNode;
  /** Плей не рисуем вовсе, если играть нечего (нет трека/ролика) */
  playing?: boolean;
  onTogglePlay?: () => void;
  playLabel?: string;
  pauseLabel?: string;
  onPrev?: () => void;
  onNext?: () => void;
  showSteps?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const arrow = 'absolute top-1/2 z-[4] flex h-[44px] w-[44px] -translate-y-1/2 items-center justify-center '
    + 'rounded-full bg-[rgba(5,1,15,0.55)] text-text-80 backdrop-blur-[8px] transition hover:text-text';
  return (
    <div className={cn('relative overflow-hidden', className)}>
      {children}
      {showSteps && onPrev && onNext && (
        <>
          <button type="button" className={cn(arrow, 'left-space-4')} aria-label={t('common.prev')} onClick={onPrev}>
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true">
              <path d="M14.5 6 8.5 12l6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
          <button type="button" className={cn(arrow, 'right-space-4')} aria-label={t('common.next')} onClick={onNext}>
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true">
              <path d="M9.5 6l6 6-6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </>
      )}
      {onTogglePlay && (
        <button
          type="button"
          onClick={onTogglePlay}
          aria-pressed={Boolean(playing)}
          aria-label={playing ? pauseLabel ?? t('common.pause') : playLabel ?? t('common.play')}
          title={playing ? pauseLabel ?? t('common.pause') : playLabel ?? t('common.play')}
          className="absolute left-1/2 top-1/2 z-[4] flex h-[64px] w-[64px] -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-[rgba(5,1,15,0.6)] text-text backdrop-blur-[8px] transition hover:brightness-125"
        >
          {playing
            ? <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true"><path d="M6 4h3v12H6zM11 4h3v12h-3z" fill="currentColor" /></svg>
            : <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true"><path d="M6 3.5v13l11-6.5L6 3.5Z" fill="currentColor" /></svg>}
        </button>
      )}
    </div>
  );
}
