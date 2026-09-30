import { useLayoutEffect, useState } from 'react';

/**
 * Вписать содержимое в высоту рамки одним масштабом (CSS zoom), не трогая пропорции.
 *
 * Страницы по макету бывают выше десктопного холста 1600×900 (карточки Figma
 * сложены под более высокий кадр), а скролла на десктопе быть не должно. Переверстать
 * карточки — значит поменять пропорции макета, поэтому ужимаем блок целиком. Ширину не
 * компенсируем: у блока под zoom проценты считаются от родителя, и 100% уже на всю колонку.
 *
 * `outer` — рамка (её высота = доступное место), `inner` — содержимое естественной высоты.
 * Ниже `minWidth` (планшет/телефон) масштаб не применяется: там страница скроллится.
 * Рефы — колбэки: у страницы бывают ранние return'ы (загрузка/ошибка), и блоки
 * появляются в DOM не на первом рендере.
 */
export function useZoomToFit<O extends HTMLElement = HTMLDivElement, I extends HTMLElement = HTMLDivElement>(minWidth = 1024) {
  const [outer, outerRef] = useState<O | null>(null);
  const [inner, innerRef] = useState<I | null>(null);
  useLayoutEffect(() => {
    if (!outer || !inner) return undefined;
    const media = window.matchMedia(`(min-width: ${minWidth}px)`);
    const apply = () => {
      if (!media.matches) {
        inner.style.zoom = '';
        return;
      }
      // Мерим естественную высоту со СНЯТЫМ масштабом: замер уже ужатого блока зависит от
      // текущего zoom, и каждый проход ResizeObserver ужимал страницу ещё сильнее.
      inner.style.zoom = '';
      const natural = inner.offsetHeight;
      const fit = Math.min(1, outer.clientHeight / Math.max(1, natural));
      inner.style.zoom = fit < 0.999 ? String(Math.round(fit * 1000) / 1000) : '';
    };
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(outer);
    ro.observe(inner);
    media.addEventListener('change', apply);
    return () => {
      ro.disconnect();
      media.removeEventListener('change', apply);
    };
  }, [outer, inner, minWidth]);
  return { outerRef, innerRef };
}
