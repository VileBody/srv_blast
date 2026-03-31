import { useCallback, useEffect } from 'react';
import { useAssets } from '../hooks/useAssets';
import { useTaxonomy } from '../hooks/useTaxonomy';
import { VideoPreview } from './VideoPreview';
import { NavigationBar } from './NavigationBar';
import { AssetInfo } from './AssetInfo';
export function AssetBrowser() {
  const { current, index, total, loading, next, prev, remove, reload } = useAssets();
  const taxonomy = useTaxonomy();

  // Keyboard navigation
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') next();
      if (e.key === 'ArrowLeft') prev();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [next, prev]);

  const handleDelete = useCallback(async () => {
    if (!current) return;
    if (!window.confirm(`Удалить "${current.file_name}"?`)) return;
    await remove();
  }, [current, remove]);

  if (loading) return <div className="loading-screen">Загрузка ассетов...</div>;

  return (
    <div className="asset-browser">
      <div className="main-column">
        <VideoPreview fileName={current?.file_name ?? null} />
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
  );
}
