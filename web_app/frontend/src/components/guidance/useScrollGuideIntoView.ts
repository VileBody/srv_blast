import { useEffect, useRef, type RefObject } from 'react';

/**
 * Скроллит к цели гайда, когда он впервые показался (show=true) — и ровно
 * один раз за время жизни компонента. Повторный show (например, из-за
 * idle-реактивации подсказки после 45с простоя) больше НЕ дёргает скролл —
 * юзера не утаскивает туда, куда он не просил, пока он смотрит что-то другое
 * на странице.
 */
export function useScrollGuideIntoView(show: boolean, targetRef: RefObject<HTMLElement>,
  block: ScrollLogicalPosition = 'center') {
  const hasScrolledRef = useRef(false);
  useEffect(() => {
    if (!show || hasScrolledRef.current) return;
    // Флаг — когда скролл реально случился: в StrictMode эффект идёт дважды, и
    // флаг до таймера отменял скролл насовсем (cleanup гасил единственный таймер).
    // цель может ещё рендериться (данные грузятся) — пробуем несколько раз
    let timer = 0;
    let attempts = 0;
    const tryScroll = () => {
      if (!targetRef.current) {
        if (++attempts < 20) timer = window.setTimeout(tryScroll, 100);
        return;
      }
      hasScrolledRef.current = true;
      targetRef.current.scrollIntoView({
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        // 'start' — для целей выше экрана: иначе их верх (заголовок) уезжает за край
        block,
        inline: 'nearest'
      });
    };
    timer = window.setTimeout(tryScroll, 80);
    return () => window.clearTimeout(timer);
  }, [show, targetRef, block]);
}
