import { useEffect, useState } from 'react';
import { fetchVideoUrl } from '../api';
import type { MediaType } from '../api';
import type { Asset } from '../types';

interface Props {
  asset: Asset | null;
  mediaType?: MediaType;
}

export function VideoPreview({ asset, mediaType = 'video' }: Props) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileName = asset?.file_name ?? null;
  const isPhoto = mediaType === 'photo';

  useEffect(() => {
    if (!fileName) { setUrl(null); return; }
    setError(null);
    setUrl(null);
    fetchVideoUrl(fileName, asset?.s3_key, mediaType)
      .then(setUrl)
      .catch((e) => {
        setError(e.message);
        setUrl(null);
      });
  }, [asset?.s3_key, fileName, mediaType]);

  if (!fileName) return <div className="video-preview empty">Нет ассетов</div>;
  if (error) return <div className="video-preview error">Ошибка загрузки: {error}</div>;
  if (!url) return <div className="video-preview loading">{isPhoto ? 'Загрузка фото...' : 'Загрузка видео...'}</div>;

  return (
    <div className="video-preview">
      {isPhoto ? (
        <img
          key={url}
          src={url}
          alt={fileName}
          style={{ width: '100%', maxHeight: '70vh', borderRadius: 8, objectFit: 'contain' }}
        />
      ) : (
        <video key={url} controls autoPlay muted loop style={{ width: '100%', maxHeight: '70vh', borderRadius: 8 }}>
          <source src={url} type="video/mp4" />
        </video>
      )}
    </div>
  );
}
