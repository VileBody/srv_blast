import { useEffect, useState } from 'react';
import { fetchVideoUrl } from '../api';

interface Props {
  fileName: string | null;
}

export function VideoPreview({ fileName }: Props) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!fileName) { setUrl(null); return; }
    setError(null);
    fetchVideoUrl(fileName)
      .then(setUrl)
      .catch((e) => {
        setError(e.message);
        setUrl(null);
      });
  }, [fileName]);

  if (!fileName) return <div className="video-preview empty">Нет ассетов</div>;
  if (error) return <div className="video-preview error">Ошибка загрузки: {error}</div>;
  if (!url) return <div className="video-preview loading">Загрузка видео...</div>;

  return (
    <div className="video-preview">
      <video key={url} controls autoPlay muted loop style={{ width: '100%', maxHeight: '70vh', borderRadius: 8 }}>
        <source src={url} type="video/mp4" />
      </video>
    </div>
  );
}
