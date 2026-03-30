import type { Asset } from '../types';

interface Props {
  asset: Asset | null;
}

export function AssetInfo({ asset }: Props) {
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
    </div>
  );
}
