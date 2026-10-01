import { useTranslation } from 'react-i18next';
import { cn } from '../../../lib/cn';
import { GLYPH, Icon } from './Icon';

/*
 * Пейджер «‹ 1 / 2 ›» (UI_RULES.md → «Пейджер»). Тихий: 32 px, заливка field. Раньше это был
 * белый градиент — самое яркое пятно экрана, ярче главной кнопки. Для листания медиа —
 * `tone="overlay"`: полупрозрачный, живёт внутри плеера.
 */
export function Pager({
  index,
  total,
  onPrev,
  onNext,
  loop = true,
  tone = 'field',
  prevLabel,
  nextLabel,
  className
}: {
  /** с нуля */
  index: number;
  total: number;
  onPrev: () => void;
  onNext: () => void;
  /** по кругу: стрелки не гаснут на краях */
  loop?: boolean;
  tone?: 'field' | 'overlay';
  prevLabel?: string;
  nextLabel?: string;
  className?: string;
}) {
  const { t } = useTranslation();
  const single = total < 2;
  const arrow = 'grid aspect-square h-full place-items-center rounded-full text-text-60 transition-colors hover:text-text disabled:opacity-30 disabled:hover:text-text-60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-light';
  return (
    <span
      className={cn(
        'inline-flex h-ctl-sm items-center gap-[2px] rounded-full p-[3px] text-ui-14 text-text-80',
        tone === 'field' ? 'bg-field' : 'bg-scrim backdrop-blur-[8px]',
        className
      )}
    >
      <button type="button" className={arrow} onClick={onPrev} disabled={single || (!loop && index === 0)} aria-label={prevLabel ?? t('common.prev')}>
        <Icon>{GLYPH.left}</Icon>
      </button>
      <span className="px-[4px] tabular-nums">{Math.min(index + 1, Math.max(total, 1))} / {Math.max(total, 1)}</span>
      <button type="button" className={arrow} onClick={onNext} disabled={single || (!loop && index >= total - 1)} aria-label={nextLabel ?? t('common.next')}>
        <Icon>{GLYPH.right}</Icon>
      </button>
    </span>
  );
}
