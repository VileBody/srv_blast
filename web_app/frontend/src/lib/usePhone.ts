import { useEffect, useState } from 'react';

const QUERY = '(max-width: 767px)';

/** true на телефоне (<768px) — там, где мобильная раскладка меняет не классы, а состав UI */
export function usePhone(): boolean {
  const [phone, setPhone] = useState(() => typeof window !== 'undefined' && window.matchMedia(QUERY).matches);
  useEffect(() => {
    const mq = window.matchMedia(QUERY);
    const sync = () => setPhone(mq.matches);
    mq.addEventListener('change', sync);
    return () => mq.removeEventListener('change', sync);
  }, []);
  return phone;
}
