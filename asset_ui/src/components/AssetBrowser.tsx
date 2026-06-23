import { useCallback, useEffect, useState } from 'react';
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
  const { current, index, total, loading, next, prev, remove, reload } = useAssets();
  const taxonomy = useTaxonomy();
  const [panel, setPanel] = useState<Panel>(null);

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
        <TagUntaggedButton onDone={reload} />
        <ActivateBaseButton onDone={reload} />
        <span className="toolbar-spacer" />
        <span className="toolbar-counter">Всего: {total}</span>
      </div>
      <div className="asset-browser">
        <div className="main-column">
          <VideoPreview asset={current} />
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
          />
        </div>
      )}
    </>
  );
}
