import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { catalogPosterOf, isVideoUrl, useInView } from '../../lib/media';
import { useLowData } from '../../lib/network';

/**
 * Ролик-превью каталога, который играет только на экране.
 *
 * Лента — до ~25 карточек; раньше все стартовали разом (`autoPlay`) и качались целиком, даже
 * за краем ленты. Теперь: `preload="none"` + заставка, играет только видимая карточка, ушла
 * из кадра — пауза. В режиме экономии трафика (lib/network) — только заставка, ролик по
 * наведению или тапу.
 */
export const PreviewVideo = forwardRef<HTMLVideoElement, {
  src: string;
  className?: string;
  onError?: () => void;
  draggable?: boolean;
  /** главный плеер превью: выбранное человеком играет и при экономии трафика */
  ignoreLowData?: boolean;
}>(function PreviewVideo({ src, className, onError, draggable, ignoreLowData = false }, forwarded) {
  const ref = useRef<HTMLVideoElement>(null);
  // сам элемент — наружу: плеер «Фона» перезапускает ролик вместе с треком
  useImperativeHandle(forwarded, () => ref.current as HTMLVideoElement, []);
  const lowData = useLowData() && !ignoreLowData;
  const visible = useInView(ref);
  const [asked, setAsked] = useState(false);
  useEffect(() => { if (!visible) setAsked(false); }, [visible]);
  const play = visible && (!lowData || asked);
  useEffect(() => {
    const video = ref.current;
    if (!video) return;
    // play() отклоняется, если браузер запретил автоплей — тогда остаётся заставка, это не ошибка
    if (play) video.play().catch(() => undefined);
    else video.pause();
  }, [play, src]);
  // наведение/тап слушаем на родителе: у карточек ленты сам <video> без pointer-events
  useEffect(() => {
    const host = ref.current?.parentElement;
    if (!host || !lowData) return undefined;
    const enter = (event: PointerEvent) => { if (event.pointerType === 'mouse') setAsked(true); };
    const leave = (event: PointerEvent) => { if (event.pointerType === 'mouse') setAsked(false); };
    // на тач-экране «наведения» нет: тап запускает, уход карточки из кадра гасит
    const down = (event: PointerEvent) => { if (event.pointerType !== 'mouse') setAsked(true); };
    host.addEventListener('pointerenter', enter);
    host.addEventListener('pointerleave', leave);
    host.addEventListener('pointerdown', down);
    return () => {
      host.removeEventListener('pointerenter', enter);
      host.removeEventListener('pointerleave', leave);
      host.removeEventListener('pointerdown', down);
    };
  }, [lowData]);
  return (
    <video
      ref={ref}
      src={src}
      poster={catalogPosterOf(src) ?? undefined}
      className={className}
      muted
      loop
      playsInline
      preload="none"
      draggable={draggable}
      onError={onError}
    />
  );
});

export function CatalogMedia({ url, className = '' }: { url?: string; className?: string }) {
  const { t } = useTranslation();
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [url]);
  if (!url || broken) return <div role="status" className={`flex items-center justify-center p-4 text-center text-text-60 ${className}`}>{t('wizard.preview.unavailable')}</div>;
  return isVideoUrl(url)
    ? <PreviewVideo src={url} className={`object-contain ${className}`} onError={() => setBroken(true)} />
    : <img src={url} alt="" className={`object-contain ${className}`} onError={() => setBroken(true)} />;
}

export function SubtitleCatalogPreview({ name, className = '' }: { name: string; className?: string }) {
  const query = useQuery({ queryKey: ['subtitle-styles'], queryFn: api.subtitleStyles });
  return <CatalogMedia url={query.data?.styles.find(item => item.name === name)?.previewUrl} className={`h-full w-full ${className}`} />;
}
