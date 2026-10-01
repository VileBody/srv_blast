import { useEffect, useId, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { cn } from '../../../lib/cn';
import { useModalCount } from '../Modal';
import { Button } from './Button';
import { GLYPH, Icon } from './Icon';

/*
 * Модалка (UI_RULES.md): карточка r25, шапка с заголовком 24 и закрытием, прокручиваемое
 * тело, низ — ActionBar. Считается в useModalCount, как старая Modal: подсказки визарда
 * при открытой модалке молчат и не ложатся поверх её кнопок.
 * Esc и клик по подложке закрывают; фокус уходит в окно и возвращается туда, откуда пришёл.
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  footer,
  size = 'md',
  children
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  /** обычно <ActionBar> */
  footer?: ReactNode;
  size?: 'md' | 'lg';
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const titleId = useId();
  const panel = useRef<HTMLElement>(null);
  const returnTo = useRef<Element | null>(null);

  useEffect(() => {
    if (!open) return undefined;
    useModalCount.getState().inc();
    returnTo.current = document.activeElement;
    panel.current?.focus({ preventScroll: true });
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => {
      useModalCount.getState().dec();
      window.removeEventListener('keydown', onKey);
      if (returnTo.current instanceof HTMLElement) returnTo.current.focus({ preventScroll: true });
    };
  }, [open, onClose]);

  if (!open) return null;
  return createPortal(
    <div className="m-overlay fixed inset-0 z-modal grid place-items-center bg-scrim p-[16px] backdrop-blur-[6px]" onMouseDown={onClose}>
      <section
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
        className={cn(
          'm-dialog flex max-h-[calc(100dvh-32px)] w-full flex-col overflow-hidden rounded-r25 border border-line-strong bg-card outline-none',
          size === 'md' ? 'max-w-[560px]' : 'max-w-[960px]'
        )}
      >
        <header className="flex items-start justify-between gap-[16px] px-[24px] pb-[12px] pt-[22px]">
          <div className="min-w-0">
            <h2 id={titleId} className="text-ui-24 font-[400] text-text">{title}</h2>
            {description && <p className="mt-[6px] text-ui-14 text-text-60">{description}</p>}
          </div>
          <Button variant="ghost" size="sm" iconOnly aria-label={t('common.close')} onClick={onClose}><Icon>{GLYPH.close}</Icon></Button>
        </header>
        <div className="subtle-scroll min-h-0 flex-1 overflow-y-auto px-[24px] pb-[20px]">{children}</div>
        {footer && <footer className="border-t border-line px-[24px] py-[14px]">{footer}</footer>}
      </section>
    </div>,
    document.body
  );
}
