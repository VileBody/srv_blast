import { useState, type ReactNode } from 'react';

/**
 * Картинка аватара с запасным вариантом. Ссылка может оказаться битой (протухшая
 * подпись TikTok CDN, удалённый файл) — тогда вместо пустой рамки рисуем `fallback`
 * (инициалы). Сбой запоминаем по самой ссылке: новая ссылка пробуется заново.
 */
export function AvatarImg({ src, fallback = null, className }: { src?: string | null; fallback?: ReactNode; className?: string }) {
  const [broken, setBroken] = useState<string | null>(null);
  if (!src || broken === src) return <>{fallback}</>;
  return <img src={src} alt="" className={className} onError={() => setBroken(src)} />;
}
