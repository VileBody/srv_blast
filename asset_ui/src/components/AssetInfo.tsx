import type { Asset, Taxonomy } from '../types';
import { ThemeTagPills } from './ThemeTagPills';

interface Props {
  asset: Asset | null;
  taxonomy: Taxonomy | null;
  onSaved: () => void;
}

export function AssetInfo({ asset, taxonomy, onSaved }: Props) {
  if (!asset) return null;
  return (
    <div className="asset-info">
      <h3>{asset.file_name}</h3>
      <div className="info-grid">
        <span className="label">Жанр:</span><span>{asset.genre}</span>
        <span className="label">Тег:</span><span>{asset.tag}</span>
        <span className="label">Размер:</span><span>{asset.src_w}×{asset.src_h}</span>
        <span className="label">Длительность:</span><span>{asset.duration_sec.toFixed(1)}с</span>
        {asset.dominant_color && (
          <><span className="label">Цвет:</span><span>{asset.dominant_color}</span></>
        )}
      </div>
      <ThemeTagPills
        tags={asset.theme_tags ?? []}
        tagStatuses={asset.tag_statuses ?? {}}
        taxonomy={taxonomy}
        onSaved={onSaved}
      />
    </div>
  );
}
