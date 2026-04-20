import { useState } from 'react';
import { exportZipUrl, fetchExportManifest } from '../api';

interface Props {
  onClose: () => void;
}

export function BulkExport({ onClose }: Props) {
  const [genre, setGenre] = useState('');
  const [tag, setTag] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const downloadManifest = async () => {
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      const manifest = await fetchExportManifest(genre.trim() || undefined, tag.trim() || undefined);
      const blob = new Blob([JSON.stringify(manifest, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const ts = new Date().toISOString().slice(0, 19).replace(/[:-]/g, '').replace('T', '_');
      a.download = `assets_manifest_${ts}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setMsg(`OK: манифест сохранён (${manifest.total} ассетов)`);
    } catch (e) {
      setErr(`Ошибка: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const downloadZip = () => {
    setErr(null);
    setMsg('Запуск скачивания архива... Сервер может стримить его несколько минут.');
    const url = exportZipUrl(genre.trim() || undefined, tag.trim() || undefined);
    const a = document.createElement('a');
    a.href = url;
    // Server sets Content-Disposition, browser will initiate download without navigating.
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  return (
    <div className="bulk-panel">
      <div className="bulk-panel-header">
        <h3>Экспорт ассетов</h3>
        <button className="btn-close" onClick={onClose} disabled={busy} aria-label="close">×</button>
      </div>
      <div className="bulk-panel-body">
        <div className="form-row">
          <label>Жанр (фильтр, необязательно)</label>
          <input
            type="text"
            value={genre}
            onChange={(e) => setGenre(e.target.value)}
            placeholder="напр. hiphop"
            disabled={busy}
          />
        </div>
        <div className="form-row">
          <label>Тег (фильтр, необязательно)</label>
          <input
            type="text"
            value={tag}
            onChange={(e) => setTag(e.target.value)}
            placeholder="напр. street"
            disabled={busy}
          />
        </div>
        <div className="bulk-actions">
          <button className="btn-primary" onClick={downloadManifest} disabled={busy}>
            {busy ? '...' : '⬇ JSON-манифест'}
          </button>
          <button className="btn-primary" onClick={downloadZip} disabled={busy}>
            ⬇ ZIP-архив
          </button>
        </div>
        {msg && <div className="bulk-msg">{msg}</div>}
        {err && <div className="bulk-msg bulk-msg-err">{err}</div>}
        <div className="bulk-hint">
          <b>JSON-манифест</b>: метаданные + presigned-ссылки (живут 1 час).
          Можно скормить в wget/curl для массового скачивания.<br />
          <b>ZIP-архив</b>: сервер стримит все ассеты одним файлом
          <code>{' {genre}/{tag}/{file}'}</code>. Может занять время.<br />
          Без фильтров выгружаются все видимые ассеты (кроме удалённых).
        </div>
      </div>
    </div>
  );
}
