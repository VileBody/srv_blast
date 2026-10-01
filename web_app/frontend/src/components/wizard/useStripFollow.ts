import { useEffect, useLayoutEffect, useRef, useState } from 'react';

/**
 * Лента кадров ролика (`.psb-strip`): у кадра есть минимальная ширина, и на узком экране
 * (15 кадров в ~300px) лента прокручивается вбок, а не сжимает клипы в щели. Хук держит
 * текущий кадр в центре ленты и сообщает, по каким краям есть ещё кадры (для фейдов).
 * Прокручивается только сама лента — страницу не дёргаем (не scrollIntoView).
 */
export function useStripFollow(current: number, count: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ left: false, right: false });

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const sync = () => setEdges({ left: el.scrollLeft > 2, right: el.scrollLeft + el.clientWidth < el.scrollWidth - 2 });
    sync();
    el.addEventListener('scroll', sync, { passive: true });
    const ro = new ResizeObserver(sync);
    ro.observe(el);
    return () => { el.removeEventListener('scroll', sync); ro.disconnect(); };
  }, [count]);

  useEffect(() => {
    const el = ref.current;
    const seg = el?.children[current] as HTMLElement | undefined;
    if (!el || !seg || el.scrollWidth <= el.clientWidth + 2) return;
    const target = seg.offsetLeft - (el.clientWidth - seg.offsetWidth) / 2;
    el.scrollTo({ left: Math.max(0, target), behavior: 'smooth' });
  }, [current, count]);

  return { ref, fadeLeft: edges.left, fadeRight: edges.right };
}
