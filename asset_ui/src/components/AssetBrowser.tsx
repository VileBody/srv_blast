import { useCallback, useEffect, useState } from 'react';
import type { MediaType, BucketOption } from '../api';
import { fetchBuckets } from '../api';
import { useAssets } from '../hooks/useAssets';
import { useTaxonomy } from '../hooks/useTaxonomy';
import { VideoPreview } from './VideoPreview';
import { NavigationBar } from './NavigationBar';
import { AssetInfo } from './AssetInfo';
import { BulkExport } from './BulkExport';
import { BulkImport } from './BulkImport';
import { TagUntaggedButton } from './TagUntaggedButton';
import { ActivateBaseButton } from './ActivateBaseButton';

type Panel = 'export' | 'import' | null;

export function AssetBrowser() {
  // Asset pool the browser + ingest controls operate on. The browse list is
  // scoped to this pool — photos and footage never mix; an empty photo pool
  // shows nothing.
  const [mediaType, setMediaType] = useState<MediaType>('video');
  // Bucket (vibe) browser: '' = all clips, otherwise browse exactly that bucket.
  const [bucket, setBucket] = useState<string>('');
  const [buckets, setBuckets] = useState<BucketOption[]>([]);
  const { current, index, total, loading, next, prev, remove, reload } = useAssets(
    undefined,
    undefined,
    mediaType,
    bucket || undefined,
  );
  const taxonomy = useTaxonomy();
  const [panel, setPanel] = useState<Panel>(null);

  // Bucket list for the dropdown (video pool only — buckets are footage vibes).
  useEffect(() => {
    if (mediaType === 'video') {
      fetchBuckets().then(setBuckets).catch((e) => console.error('fetchBuckets', e));
    } else {
      setBucket('');  // buckets are footage vibes; the photo pool has none
    }
  }, [mediaType]);

  // Keyboard navigation — disabled while a bulk panel is open so typing in
  // inputs doesn't move through the asset list.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (panel) return;
      if (e.key === 'ArrowRight') next();
      if (e.key === 'ArrowLeft') prev();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [next, prev, panel]);

  const handleDelete = useCallback(async () => {
    if (!current) return;
    if (!window.confirm(`Удалить "${current.file_name}"?`)) return;
    await remove();
  }, [current, remove]);

  if (loading) return <div className="loading-screen">Загрузка ассетов...</div>;

  return (
    <>
      <div className="toolbar">
        <button className="toolbar-btn" onClick={() => setPanel('export')}>
          ⬇ Экспорт
        </button>
        <button className="toolbar-btn" onClick={() => setPanel('import')}>
          ⬆ Импорт
        </button>
        <button
          className="toolbar-btn"
          title="Пул ассетов для импорта/разметки/активации"
          onClick={() => setMediaType((m) => (m === 'video' ? 'photo' : 'video'))}
        >
          {mediaType === 'photo' ? '🖼 Пул: фото' : '🎞 Пул: видео'}
        </button>
        <TagUntaggedButton onDone={reload} mediaType={mediaType} />
        <ActivateBaseButton onDone={reload} mediaType={mediaType} />
        {mediaType === 'video' && (
          <select
            className="toolbar-btn"
            title="Смотреть клипы одного вайба (бакета)"
            value={bucket}
            onChange={(e) => setBucket(e.target.value)}
          >
            <option value="">🎬 Все клипы</option>
            {buckets.map((b) => (
              <option key={b.id} value={b.id}>
                {b.theme_label} · {b.label} ({b.mood})
              </option>
            ))}
          </select>
        )}
        <span className="toolbar-spacer" />
        <span className="toolbar-counter">
          {bucket ? `Бакет: ${total}` : `Всего: ${total}`}
        </span>
      </div>
      <div className="asset-browser">
        <div className="main-column">
          <VideoPreview asset={current} mediaType={mediaType} />
          <NavigationBar
            index={index}
            total={total}
            onPrev={prev}
            onNext={next}
            onDelete={handleDelete}
          />
        </div>
        <div className="side-column">
          <AssetInfo asset={current} taxonomy={taxonomy} onSaved={reload} />
        </div>
      </div>

      {panel === 'export' && (
        <div className="bulk-overlay" onClick={(e) => { if (e.target === e.currentTarget) setPanel(null); }}>
          <BulkExport onClose={() => setPanel(null)} />
        </div>
      )}
      {panel === 'import' && (
        <div className="bulk-overlay">
          <BulkImport
            onClose={() => setPanel(null)}
            onUploaded={reload}
            mediaType={mediaType}
          />
        </div>
      )}
    </>
  );
}
