import { useEffect, useRef, type PointerEvent as ReactPointerEvent } from 'react';
import { cssZoom } from '../../lib/zoom';

/** Горизонтальный скролл: драг 1:1, колесо — плавно */
export function useDragScroll() {
  const ref = useRef<HTMLDivElement>(null);
  const drag = useRef({ active: false, moved: false, startX: 0, startScroll: 0 });

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    // тач листает лентой нативно (touch-action: pan-x) — JS-drag только для мыши
    if (!ref.current || e.pointerType === 'touch') return;
    drag.current = { active: true, moved: false, startX: e.clientX, startScroll: ref.current.scrollLeft };
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag.current.active || !ref.current) return;
    const dx = e.clientX - drag.current.startX;
    if (Math.abs(dx) > 5) {
      // лента поехала — держим курсор за ней, даже когда он вышел за край (иначе драг рвался)
      if (!drag.current.moved) ref.current.setPointerCapture(e.pointerId);
      drag.current.moved = true;
      // сдвиг мыши — визуальные пиксели, scrollLeft — пиксели ленты (визард под zoom)
      ref.current.scrollLeft = drag.current.startScroll - dx / cssZoom(ref.current);
    }
  };
  const end = () => {
    setTimeout(() => { drag.current.active = false; drag.current.moved = false; }, 0);
  };
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      if (element.scrollWidth <= element.clientWidth + 1) return;
      const delta = Math.abs(event.deltaY) >= Math.abs(event.deltaX) ? event.deltaY : event.deltaX;
      if (!delta) return;
      event.preventDefault();
      element.scrollLeft += delta;
    };
    element.addEventListener('wheel', onWheel, { passive: false });
    return () => element.removeEventListener('wheel', onWheel);
  });

  return {
    ref,
    moved: () => drag.current.moved,
    handlers: { onPointerDown, onPointerMove, onPointerUp: end, onPointerLeave: end }
  };
}
