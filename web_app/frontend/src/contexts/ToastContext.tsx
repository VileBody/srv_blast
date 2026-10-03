import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type MouseEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
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
/** Обычный тост читается за 4 с; с кнопкой нужно время дотянуться — 10 с (и пауза под курсором). */
const TOAST_MS = 4000;
const ACTION_TOAST_MS = 10_000;

type Timer = { handle: number; endsAt: number; remaining: number };

/*
 * Провайдер живёт внутри BrowserRouter: кнопка тоста ведёт клиентским переходом. Раньше это
 * был голый <a href> — полная перезагрузка сайта (сброс кеша и состояния визарда).
 */
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timers = useRef<Map<string, Timer>>(new Map());
  /*
   * Гасим дубли на уровне провайдера: под StrictMode эффекты вызываются дважды, и push
   * из useEffect (возврат из TikTok-OAuth, «Ролики готовы») успевает отработать до того,
   * как сработает его же state-гард — юзер видел два одинаковых уведомления.
   * Ref, а не state: сравнение должно быть синхронным внутри одного коммита.
   */
  const lastShown = useRef<Map<string, number>>(new Map());

  const dismiss = useCallback((id: string) => {
    const timer = timers.current.get(id);
    if (timer) window.clearTimeout(timer.handle);
    timers.current.delete(id);
    setToasts((current) => current.filter((item) => item.id !== id));
  }, []);

  const schedule = useCallback((id: string, ms: number) => {
    const handle = window.setTimeout(() => dismiss(id), ms);
    timers.current.set(id, { handle, endsAt: Date.now() + ms, remaining: ms });
  }, [dismiss]);

  // Под курсором/фокусом тост не исчезает: таймер встаёт и потом дотикивает остаток
  const pause = useCallback((id: string) => {
    const timer = timers.current.get(id);
    if (!timer || timer.handle === 0) return;
    window.clearTimeout(timer.handle);
    timers.current.set(id, { handle: 0, endsAt: 0, remaining: Math.max(1000, timer.endsAt - Date.now()) });
  }, []);

  const resume = useCallback((id: string) => {
    const timer = timers.current.get(id);
    if (timer && timer.handle === 0) schedule(id, timer.remaining);
  }, [schedule]);

  useEffect(() => () => timers.current.forEach((timer) => window.clearTimeout(timer.handle)), []);

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
    schedule(id, toast.action ? ACTION_TOAST_MS : TOAST_MS);
  }, [schedule]);

  const follow = useCallback((event: MouseEvent<HTMLAnchorElement>, toast: Toast) => {
    // новая вкладка (ctrl/cmd/средняя кнопка) — браузеру; обычный клик — переход без перезагрузки
    if (!toast.action || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    dismiss(toast.id);
    navigate(toast.action.href);
  }, [dismiss, navigate]);

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
            onMouseEnter={() => pause(toast.id)}
            onMouseLeave={() => resume(toast.id)}
            onFocus={() => pause(toast.id)}
            onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) resume(toast.id); }}
          >
            <ToastMark variant={toast.variant} />
            <div className="min-w-0 flex-1">
              <div className="text-[18px] font-[400] leading-[22px] text-text">{toast.title}</div>
              {toast.text && <div className="mt-[6px] text-[14px] font-[350] leading-[18px] text-text-60">{toast.text}</div>}
              {toast.action && (
                <a className="group mt-[12px] inline-flex items-center gap-[8px] text-[14px] text-accent-light" href={toast.action.href} onClick={(event) => follow(event, toast)}>
                  {toast.action.label}
                  <span className="transition-transform group-hover:translate-x-[2px]" aria-hidden="true">→</span>
                </a>
              )}
            </div>
            <button className="toast-close" onClick={() => dismiss(toast.id)} aria-label={t('common.close')}>
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
