import { useEffect } from 'react';

export function Modal({
  open,
  title,
  children,
  onClose
}: {
  open: boolean;
  title?: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      <div className="modal-overlay" onMouseDown={onClose} />
      <section className="modal-panel subtle-scroll" role="dialog" aria-modal="true" aria-label={title} onMouseDown={(event) => event.stopPropagation()}>
        <div className="mb-[28px] flex items-center justify-between gap-[20px]">
          {title ? <h2 className="text-[28px] font-[400] leading-[34px] text-text">{title}</h2> : <span />}
          <button type="button" onClick={onClose} className="flex h-[40px] w-[40px] shrink-0 items-center justify-center rounded-r10 bg-grad-soft-20 text-text-60 transition hover:text-text" aria-label="Закрыть модальное окно">
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
