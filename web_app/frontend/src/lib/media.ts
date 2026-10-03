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
