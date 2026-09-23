import { useEffect, useRef, type RefObject } from 'react';

/**
 * Скроллит к цели гайда, когда он впервые показался (show=true) — и ровно
 * один раз за время жизни компонента. Повторный show (например, из-за
 * idle-реактивации подсказки после 45с простоя) больше НЕ дёргает скролл —
 * юзера не утаскивает туда, куда он не просил, пока он смотрит что-то другое
 * на странице.
 */
export function useScrollGuideIntoView(show: boolean, targetRef: RefObject<HTMLElement>) {
  const hasScrolledRef = useRef(false);
  useEffect(() => {
    if (!show || hasScrolledRef.current) return;
    hasScrolledRef.current = true;
    const timer = window.setTimeout(() => {
      targetRef.current?.scrollIntoView({
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        block: 'center',
        inline: 'nearest'
      });
    }, 80);
    return () => window.clearTimeout(timer);
  }, [show, targetRef]);
}
