import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { cn } from '../../lib/cn';
import { FOCUSABLE, useModalCount } from '../ui/Modal';
import { Button, GLYPH, Icon } from '../ui/kit';

/*
 * Карточка шага воронки: та же геометрия, что у kit/Dialog (r25, заголовок 24, низ —
 * ActionBar), а прогресс шагов живёт слева в строке действий (контекст по правилу
 * в строке действий), а не надписью над заголовком.
 *
 * Отделена от оверлея: витрина /dev/funnel рисует все шаги рядом теми же компонентами.
 * `stepKey` перемонтирует тело при смене шага — содержимое всплывает (m-nudge из
 * motion.css), сама карточка остаётся на месте и не прыгает.
 */
export function FunnelSheet({
  title,
  description,
  progress,
  actions,
  aside,
  onClose,
  stepKey,
  titleId,
  className,
  children
}: {
  title: ReactNode;
  description?: ReactNode;
  progress?: { total: number; current: number };
  /** кнопки строки действий; главная — последней */
  actions?: ReactNode;
  /** контекст строки действий вместо прогресса (например, «Код B-4F2K») */
  aside?: ReactNode;
  onClose?: () => void;
  stepKey?: string;
  titleId?: string;
  className?: string;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const start = progress ? (
    <span className="flex items-center gap-[10px] tabular-nums">
      <span className="flex items-center gap-[4px]" aria-hidden="true">
        {Array.from({ length: progress.total }, (_, index) => (
          <span
            key={index}
            className={cn(
              'h-[4px] rounded-full transition-[width,background-color] duration-300 ease-[var(--m-ease)]',
              index === progress.current ? 'w-[18px] bg-accent-light' : index < progress.current ? 'w-[8px] bg-accent' : 'w-[8px] bg-field-hover'
            )}
          />
        ))}
      </span>
      {/* на телефоне хватает точек: подпись съела бы место у кнопок строки */}
      <span className="max-md:sr-only">{t('funnel.step', { n: progress.current + 1, total: progress.total })}</span>
    </span>
  ) : aside;
  return (
    <section
      className={cn(
        'flex max-h-[calc(100dvh-32px)] w-full max-w-[560px] flex-col overflow-hidden rounded-r25 border border-line-strong bg-card text-left',
        className
      )}
    >
      <header className="flex items-start justify-between gap-[16px] px-[24px] pb-[14px] pt-[24px]">
        <div key={stepKey} className="fn-step min-w-0">
          <h2 id={titleId} className="text-ui-24 font-[400] text-text [text-wrap:balance]">{title}</h2>
          {description && <p className="mt-[8px] max-w-[60ch] text-ui-14 text-text-60 [text-wrap:pretty]">{description}</p>}
        </div>
        {onClose && (
          <Button variant="ghost" size="sm" iconOnly aria-label={t('common.close')} onClick={onClose}>
            <Icon>{GLYPH.close}</Icon>
          </Button>
        )}
      </header>
      {children ? (
        <div key={stepKey} className="fn-step subtle-scroll min-h-0 flex-1 overflow-y-auto px-[24px] pb-[24px]">{children}</div>
      ) : (
        <div className="pb-[10px]" />
      )}
      {(actions || start) && (
        // Одна строка на любой ширине: прогресс слева, кнопки справа (ActionBar на
        // телефоне складывает их в столбик, а тут кнопка уезжала под прогресс).
        <footer className="flex items-center gap-[12px] border-t border-line px-[24px] py-[14px] max-md:px-[16px]">
          {start && <div className="flex shrink-0 items-center text-ui-14 text-text-60">{start}</div>}
          <div className="flex min-w-0 flex-1 items-center justify-end gap-[8px]">{actions}</div>
        </footer>
      )}
    </section>
  );
}

/**
 * Та же карточка поверх страницы: подложка, Esc, фокус, счётчик модалок (как kit/Dialog).
 *
 * Клавиатура слушается в фазе захвата и дальше окна не уходит: безлимит открывается и
 * поверх монтажного стола (генерация со стола → credits_exhausted), а у стола свои
 * горячие клавиши на window — Esc закрывал бы оба окна, Ctrl+Z и [ ] правили бы стол
 * под подложкой. Tab зациклен внутри окна.
 */
export function FunnelDialog({
  open,
  onClose,
  labelledBy,
  children
}: {
  open: boolean;
  onClose: () => void;
  labelledBy?: string;
  children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  // свежий onClose без переподписки: иначе каждый новый колбэк снова уводил бы фокус в окно
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    if (!open) return undefined;
    useModalCount.getState().inc();
    const returnTo = document.activeElement;
    panel.current?.focus({ preventScroll: true });
    const onKey = (event: KeyboardEvent) => {
      const root = panel.current;
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        closeRef.current();
        return;
      }
      if (event.key === 'Tab' && root) {
        event.stopPropagation();
        const items = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE));
        const active = document.activeElement;
        if (!items.length) {
          event.preventDefault();
          root.focus();
          return;
        }
        const first = items[0];
        const last = items[items.length - 1];
        if (event.shiftKey && (active === first || active === root || !root.contains(active))) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && (active === last || !root.contains(active))) {
          event.preventDefault();
          first.focus();
        }
        return;
      }
      // Клавиши мимо окна (фокус остался на странице) странице не достаются. Клавиши внутри
      // окна идут дальше — кнопкам окна нужны свои обработчики; стол их отсекает сам по
      // счётчику модалок.
      if (!root || !root.contains(event.target as Node)) event.stopPropagation();
    };
    window.addEventListener('keydown', onKey, true);
    return () => {
      useModalCount.getState().dec();
      window.removeEventListener('keydown', onKey, true);
      if (returnTo instanceof HTMLElement) returnTo.focus({ preventScroll: true });
    };
  }, [open]);

  if (!open) return null;
  return createPortal(
    <div className="m-overlay fixed inset-0 z-modal grid place-items-center bg-scrim p-[16px] backdrop-blur-[6px]" onMouseDown={onClose}>
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
        className="m-dialog flex w-full justify-center outline-none"
        onMouseDown={(event) => event.stopPropagation()}
      >
        {children}
      </div>
    </div>,
    document.body
  );
}
