import { RefObject, useEffect, useState } from 'react';

/** Signed S3 URLs have a query string after the extension. */
export function isVideoUrl(url: string): boolean {
  return /\.(mp4|webm|mov|m4v)(?:[?#]|$)/i.test(url);
}

/**
 * Кадр клипа для миниатюры: у клипов через прослойку сайта (`/api/wizard/media/clip/…/clip.mp4`)
 * есть JPEG-кадр ~10–20 КБ. Миниатюра — картинка, а не <video>, качавший начало клипа ради
 * одного кадра. Для остальных адресов (мок, свои загрузки) кадра нет — null.
 */
export function posterOf(url: string | null | undefined, at: number): string | null {
  if (!url || !/^\/api\/wizard\/media\/clip\/[^/]+\/clip\.mp4$/.test(url)) return null;
  return url.replace(/\/clip\.mp4$/, `/poster.jpg?t=${Math.max(0, at).toFixed(1)}`);
}

/**
 * Заставка превью каталога (`/api/wizard/media/preview/…/clip.mp4`): JPEG того же размера, что
 * копия ролика. Видна, пока карточка не в кадре, и вместо размытой второй копии ролика под
 * широким кадром. У адресов мока и явных https каталога заставки нет — null.
 */
export function catalogPosterOf(url: string | null | undefined): string | null {
  if (!url || !/^\/api\/wizard\/media\/preview\/[^/]+\/clip\.mp4$/.test(url)) return null;
  return url.replace(/\/clip\.mp4$/, '/poster.jpg');
}

/**
 * Элемент сейчас на экране. Без root считается по окну, но с обрезкой прокручиваемыми предками
 * (горизонтальная лента карточек, меню) — карточка за краем ленты «не видна».
 * once — раз показался, считаем видимым навсегда (шрифт уже скачан, гасить незачем).
 */
export function useInView(ref: RefObject<Element>, opts: { root?: RefObject<Element>; rootMargin?: string; once?: boolean } = {}): boolean {
  const { root, rootMargin, once } = opts;
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    // старый браузер без IntersectionObserver — считаем видимым, как было до ленивой загрузки
    if (typeof IntersectionObserver === 'undefined') { setInView(true); return undefined; }
    const io = new IntersectionObserver((entries) => {
      const hit = entries.some((entry) => entry.isIntersecting);
      setInView((prev) => (once && prev ? prev : hit));
      if (hit && once) io.disconnect();
    }, { root: root?.current ?? null, rootMargin });
    io.observe(el);
    return () => io.disconnect();
  }, [ref, root, rootMargin, once]);
  return inView;
}
