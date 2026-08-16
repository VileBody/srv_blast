import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import { cn } from '../lib/cn';
import { FigIcon } from '../components/ui/FigIcon';

type ToastVariant = 'success' | 'error' | 'info' | 'warning';
type Toast = { id: string; title: string; text?: string; variant: ToastVariant; action?: { label: string; href: string } };

type ToastContextValue = {
  push: (toast: Omit<Toast, 'id'>) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

// success — фирменная галочка из Figma (та же L-форма + rotate-45, что в чек-листах),
// остальные варианты — глиф. Это «свежая» галочка, а не старый юникод-✓.
const variantGlyph: Record<Exclude<ToastVariant, 'success'>, string> = {
  error: '!',
  info: 'i',
  warning: '!'
};

function ToastMark({ variant }: { variant: ToastVariant }) {
  return (
    <span className="toast-mark" aria-hidden="true">
      {variant === 'success'
        ? <FigIcon name="pf-check.svg" h={13} className="-translate-y-[2px] rotate-45" />
        : variantGlyph[variant]}
    </span>
  );
}

/** Окно, в котором повтор того же тоста считается дублем, а не новым событием. */
const DEDUPE_MS = 1200;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  /*
   * Гасим дубли на уровне провайдера: под StrictMode эффекты вызываются дважды, и push
   * из useEffect (возврат из TikTok-OAuth, «Ролики готовы») успевает отработать до того,
   * как сработает его же state-гард — юзер видел два одинаковых уведомления.
   * Ref, а не state: сравнение должно быть синхронным внутри одного коммита.
   */
  const lastShown = useRef<Map<string, number>>(new Map());

  const dismiss = useCallback((id: string) => {
    setToasts((current) => current.filter((item) => item.id !== id));
  }, []);

  const push = useCallback((toast: Omit<Toast, 'id'>) => {
    const key = `${toast.variant}|${toast.title}|${toast.text ?? ''}`;
    const now = Date.now();
    const seenAt = lastShown.current.get(key);
    if (seenAt !== undefined && now - seenAt < DEDUPE_MS) return;
    lastShown.current.set(key, now);
    // не копим ключи бесконечно — чистим всё, что уже вышло из окна дедупа
    for (const [seenKey, at] of lastShown.current) {
      if (now - at >= DEDUPE_MS) lastShown.current.delete(seenKey);
    }

    const id = crypto.randomUUID();
    setToasts((current) => [...current, { ...toast, id }]);
    window.setTimeout(() => dismiss(id), 4000);
  }, [dismiss]);

  const value = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-stack" aria-live="polite" aria-atomic="false">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={cn('toast-item', `toast-item-${toast.variant}`, !toast.text && !toast.action && 'toast-item--single')}
            role={toast.variant === 'error' ? 'alert' : 'status'}
          >
            <ToastMark variant={toast.variant} />
            <div className="min-w-0 flex-1">
              <div className="text-[18px] font-[400] leading-[22px] text-text">{toast.title}</div>
              {toast.text && <div className="mt-[6px] text-[14px] font-[350] leading-[18px] text-text-60">{toast.text}</div>}
              {toast.action && (
                <a className="group mt-[12px] inline-flex items-center gap-[8px] text-[14px] text-accent-light" href={toast.action.href}>
                  {toast.action.label}
                  <span className="transition-transform group-hover:translate-x-[2px]" aria-hidden="true">→</span>
                </a>
              )}
            </div>
            <button className="toast-close" onClick={() => dismiss(toast.id)} aria-label="Закрыть">
              <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                <path d="M1 1L11 11M11 1L1 11" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error('useToast must be used inside ToastProvider');
  return context;
}
