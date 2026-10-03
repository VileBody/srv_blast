import { useEffect, useState } from 'react';

/*
 * Экономия трафика. Браузер сообщает о медленной сети (`effectiveType` 2g/3g) или о включённом
 * «экономить трафик» (`saveData`) — тогда декоративное видео (стена роликов на входе, примеры
 * эффектов в библиотеке) не стартует само: показываем кадр, видео играет по наведению/тапу.
 * Где браузер этого не сообщает (Safari, Firefox) — всё как обычно.
 */
interface NetInfo extends EventTarget { saveData?: boolean; effectiveType?: string }
const connection = (): NetInfo | undefined => (typeof navigator === 'undefined' ? undefined : (navigator as Navigator & { connection?: NetInfo }).connection);

export function isLowData(): boolean {
  const c = connection();
  return Boolean(c && (c.saveData || ['slow-2g', '2g', '3g'].includes(c.effectiveType ?? '')));
}

export function useLowData(): boolean {
  const [low, setLow] = useState(isLowData);
  useEffect(() => {
    const c = connection();
    if (!c) return undefined;
    const sync = () => setLow(isLowData());
    c.addEventListener('change', sync);
    return () => c.removeEventListener('change', sync);
  }, []);
  return low;
}
