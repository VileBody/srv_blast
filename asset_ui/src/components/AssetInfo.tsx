import type { Asset, Taxonomy } from '../types';
import type { MediaType } from '../api';
import { ThemeTagPills } from './ThemeTagPills';

interface Props {
  asset: Asset | null;
  taxonomy: Taxonomy | null;
  onSaved: () => void;
  mediaType?: MediaType;
}

export function AssetInfo({ asset, taxonomy, onSaved, mediaType = 'video' }: Props) {
  if (!asset) return null;
  // The same two S3 path levels mean different things per plane: genre/tag for
  // the tagged pools, kind/collection for the folder-scoped one. Showing
  // "Жанр: films" would invite reading a collection as a vibe.
  const isCollection = mediaType === 'collection';
  // 0×0 / 0.0s means the static index has no entry for this file yet. The picker
  // reads duration to decide whether a clip can cover an interval, so such a
  // clip is invisible to selection however full the browser looks — say so here
  // rather than let a zero read as a real measurement.
  const unindexed = !asset.duration_sec || !asset.src_w || !asset.src_h;
  return (
    <div className="asset-info">
      <h3>{asset.file_name}</h3>
      <div className="info-grid">
        <span className="label">{isCollection ? 'Тип:' : 'Жанр:'}</span><span>{asset.genre}</span>
        <span className="label">{isCollection ? 'Коллекция:' : 'Тег:'}</span><span>{asset.tag}</span>
        <span className="label">Размер:</span><span>{asset.src_w}×{asset.src_h}</span>
        <span className="label">Длительность:</span><span>{asset.duration_sec.toFixed(1)}с</span>
        {asset.dominant_color && (
          <><span className="label">Цвет:</span><span>{asset.dominant_color}</span></>
        )}
      </div>
      {unindexed && (
        <p className="asset-warning">
          Нет данных индекса — клип не участвует в подборе. Нажми «Активировать базу».
        </p>
      )}
      <ThemeTagPills
        tags={asset.theme_tags ?? []}
        tagStatuses={asset.tag_statuses ?? {}}
        taxonomy={taxonomy}
        onSaved={onSaved}
      />
    </div>
  );
}
