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

/** One selectable pool.
 *
 * The three ingest planes (video / photo / collection) are a backend concept;
 * an operator thinks in terms of what the material IS. The collection plane
 * therefore appears as one tab per kind rather than as a single "collections"
 * entry the operator would then have to filter by hand — with five pools a
 * cycling button gives no way to tell where you are without clicking through.
 */
type Pool = {
  id: string;
  label: string;
  mediaType: MediaType;
  /** Collection kind — also the S3 genre this pool is scoped to. */
  genre?: string;
};

const POOLS: Pool[] = [
  { id: 'video', label: '9:16', mediaType: 'video' },
  { id: 'photo', label: 'Фото', mediaType: 'photo' },
  { id: 'cine16x9', label: '16:9', mediaType: 'collection', genre: 'cine16x9' },
  { id: 'films', label: 'Фильмы', mediaType: 'collection', genre: 'films' },
  { id: 'people', label: 'Личности', mediaType: 'collection', genre: 'people' },
];

export function AssetBrowser() {
  // Asset pool the browser + ingest controls operate on. The browse list is
  // scoped to this pool — the pools never mix; an empty one shows nothing.
  const [poolId, setPoolId] = useState<string>(POOLS[0].id);
  const pool = POOLS.find((p) => p.id === poolId) ?? POOLS[0];
  const mediaType = pool.mediaType;
  // Bucket (vibe) browser: '' = all clips, otherwise browse exactly that bucket.
  const [bucket, setBucket] = useState<string>('');
  const [buckets, setBuckets] = useState<BucketOption[]>([]);
  // Collection pools: '' = every folder in this kind, otherwise one collection.
  const [folder, setFolder] = useState<string>('');
  const [folders, setFolders] = useState<string[]>([]);
  const { assets, current, index, total, loading, next, prev, remove, reload } = useAssets(
    pool.genre,
    folder || undefined,
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
      setBucket('');  // buckets are footage vibes; the other pools have none
    }
  }, [mediaType]);

  // Switching pools must clear the folder filter, or a films folder would stay
  // applied to the people pool and silently show nothing.
  useEffect(() => { setFolder(''); }, [poolId]);

  // The folder list is captured from the UNFILTERED load, so picking one folder
  // does not shrink the menu you picked it from.
  useEffect(() => {
    if (folder) return;
    const seen = Array.from(
      new Set(assets.map((a) => String(a.tag || '').trim()).filter(Boolean)),
    ).sort();
    setFolders(seen);
  }, [assets, folder]);

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
        <div className="pool-tabs" role="tablist" aria-label="Пул ассетов">
          {POOLS.map((p) => (
            <button
              key={p.id}
              role="tab"
              aria-selected={p.id === poolId}
              className={`pool-tab${p.id === poolId ? ' active' : ''}`}
              title={`Пул «${p.label}» — импорт, просмотр и активация идут в него`}
              onClick={() => setPoolId(p.id)}
            >
              {p.label}
            </button>
          ))}
        </div>
        {/* Collections are selected by folder, never by tags — there is nothing
            for the tagger to add, so the control is absent rather than inert. */}
        {mediaType !== 'collection' && (
          <TagUntaggedButton onDone={reload} mediaType={mediaType} />
        )}
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
        {mediaType === 'collection' && (
          <select
            className="toolbar-btn"
            title="Смотреть клипы одной коллекции"
            value={folder}
            onChange={(e) => setFolder(e.target.value)}
          >
            <option value="">📁 Все коллекции</option>
            {folders.map((f) => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>
        )}
        <span className="toolbar-spacer" />
        <span className="toolbar-counter">
          {bucket
            ? `Бакет: ${total} · совпало тегов: ${(current as { _overlap?: number } | null)?._overlap ?? '—'}`
            : mediaType === 'collection' && !folder
              ? `Всего: ${total} · коллекций: ${folders.length}`
              : `Всего: ${total}`}
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
          <AssetInfo
            asset={current}
            taxonomy={taxonomy}
            onSaved={reload}
            mediaType={mediaType}
          />
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
            presetGenre={pool.genre}
          />
        </div>
      )}
    </>
  );
}
