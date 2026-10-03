import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { create } from 'zustand';

/**
 * Сколько модалок открыто прямо сейчас. Подсказки визарда (ActionGuideOverlay) при
 * открытой модалке молчат: иначе, например, «Выбери тип фона» ложилась поверх
 * вопроса «Использовать те же вводные?» и перекрывала его кнопки.
 */
export const useModalCount = create<{ count: number; inc: () => void; dec: () => void }>((set) => ({
  count: 0,
  inc: () => set((s) => ({ count: s.count + 1 })),
  dec: () => set((s) => ({ count: Math.max(0, s.count - 1) }))
}));

export const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Modal({
  open,
  title,
  children,
  onClose,
  dismissable = true
}: {
  open: boolean;
  title?: string;
  children: React.ReactNode;
  onClose: () => void;
  /** false — идёт необратимое действие (удаление аккаунта): Esc, подложка и крестик не закрывают */
  dismissable?: boolean;
}) {
  const { t } = useTranslation();
  const panel = useRef<HTMLElement>(null);
  // свежие значения для keydown без переподписки (иначе на каждом рендере сбрасывался бы фокус)
  const latest = useRef({ onClose, dismissable });
  latest.current = { onClose, dismissable };

  useEffect(() => {
    if (!open) return undefined;
    useModalCount.getState().inc();
    return () => useModalCount.getState().dec();
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    /*
     * Фокус в окно и обратно: без этого клавиатура и скринридер оставались на странице под
     * подложкой. Tab зациклен внутри окна — простая ловушка без сторонних библиотек.
     */
    const returnTo = document.activeElement;
    const root = panel.current;
    const preferred = root?.querySelector<HTMLElement>('[data-autofocus]');
    (preferred ?? root)?.focus({ preventScroll: true });
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (latest.current.dismissable) latest.current.onClose();
        return;
      }
      if (event.key !== 'Tab' || !root) return;
      const items = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!items.length) {
        event.preventDefault();
        root.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || active === root || !root.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !root.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      if (returnTo instanceof HTMLElement) returnTo.focus({ preventScroll: true });
    };
  }, [open]);

  if (!open) return null;
  const close = () => { if (dismissable) onClose(); };

  return (
    <>
      <div className="modal-overlay" onMouseDown={close} />
      <section ref={panel} tabIndex={-1} className="modal-panel subtle-scroll outline-none" role="dialog" aria-modal="true" aria-label={title} aria-busy={!dismissable || undefined} onMouseDown={(event) => event.stopPropagation()}>
        <div className="mb-[28px] flex items-center justify-between gap-[20px]">
          {title ? <h2 className="text-[28px] font-[400] leading-[34px] text-text">{title}</h2> : <span />}
          <button type="button" onClick={close} disabled={!dismissable} className="flex h-[40px] w-[40px] shrink-0 items-center justify-center rounded-r10 bg-grad-soft-20 text-text-60 transition hover:text-text disabled:cursor-not-allowed disabled:opacity-40" aria-label={t('common.closeDialog')}>
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
              <path d="M1 1L11 11M11 1L1 11" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        {children}
      </section>
    </>
  );
}
