import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { isVideoUrl } from '../../lib/media';

export function CatalogMedia({ url, className = '' }: { url?: string; className?: string }) {
  const { t } = useTranslation();
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [url]);
  if (!url || broken) return <div role="status" className={`flex items-center justify-center p-4 text-center text-text-60 ${className}`}>{t('wizard.preview.unavailable')}</div>;
  return isVideoUrl(url)
    ? <video key={url} src={url} className={`object-contain ${className}`} autoPlay muted loop playsInline controls preload="metadata" onError={() => setBroken(true)} />
    : <img src={url} alt="" className={`object-contain ${className}`} onError={() => setBroken(true)} />;
}

export function SubtitleCatalogPreview({ name, className = '' }: { name: string; className?: string }) {
  const { t } = useTranslation();
  const query = useQuery({ queryKey: ['subtitle-styles'], queryFn: api.subtitleStyles });
  return <div className={`flex flex-col ${className}`}>
    <CatalogMedia url={query.data?.styles.find(item => item.name === name)?.previewUrl} className="min-h-0 w-full flex-1" />
    <p className="p-2 text-center text-xs text-text-60">{t('wizard.preview.example')}</p>
  </div>;
}
