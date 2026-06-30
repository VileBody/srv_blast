import { useRef, useState } from 'react';
import { importAssets, type ImportResult, type MediaType } from '../api';

interface Props {
  onClose: () => void;
  onUploaded: () => void;
  mediaType?: MediaType;
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

const ACCEPTED_EXT_VIDEO = ['.mp4', '.mov', '.webm', '.m4v', '.zip'];
const ACCEPTED_EXT_PHOTO = ['.jpg', '.jpeg', '.png', '.webp', '.zip'];

function isAccepted(name: string, exts: string[]): boolean {
  const lower = name.toLowerCase();
  return exts.some((ext) => lower.endsWith(ext));
}

export function BulkImport({ onClose, onUploaded, mediaType = 'video' }: Props) {
  const ACCEPTED_EXT = mediaType === 'photo' ? ACCEPTED_EXT_PHOTO : ACCEPTED_EXT_VIDEO;
  const [genre, setGenre] = useState('');
  const [tag, setTag] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const addFiles = (incoming: FileList | File[]) => {
    const arr = Array.from(incoming).filter((f) => isAccepted(f.name, ACCEPTED_EXT));
    if (arr.length === 0) {
      setErr('Допустимые форматы: ' + ACCEPTED_EXT.join(', '));
      return;
    }
    // Deduplicate by name+size
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      const merged = [...prev];
      for (const f of arr) {
        const key = `${f.name}:${f.size}`;
        if (!seen.has(key)) {
          merged.push(f);
          seen.add(key);
        }
      }
      return merged;
    });
    setErr(null);
  };

  const removeFile = (idx: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  };

  const clearFiles = () => setFiles([]);

  const onInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) addFiles(e.target.files);
    // Reset input so same file can be picked again if removed
    if (inputRef.current) inputRef.current.value = '';
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (busy) return;
    if (e.dataTransfer?.files) addFiles(e.dataTransfer.files);
  };

  const submit = async () => {
    setErr(null);
    setResult(null);
    if (!genre.trim() || !tag.trim()) {
      setErr('Укажи жанр и тег — в них сложатся файлы');
      return;
    }
    if (files.length === 0) {
      setErr('Выбери файлы');
      return;
    }
    setBusy(true);
    setProgress(0);
    try {
      const res = await importAssets(files, genre.trim(), tag.trim(), setProgress, mediaType);
      setResult(res);
      if (res.uploaded > 0) {
        onUploaded();
      }
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const totalBytes = files.reduce((s, f) => s + f.size, 0);

  return (
    <div className="bulk-panel">
      <div className="bulk-panel-header">
        <h3>Импорт ассетов</h3>
        <button className="btn-close" onClick={onClose} disabled={busy} aria-label="close">×</button>
      </div>
      <div className="bulk-panel-body">
        <div className="form-row-2col">
          <div className="form-row">
            <label>Жанр*</label>
            <input
              type="text"
              value={genre}
              onChange={(e) => setGenre(e.target.value)}
              placeholder="hiphop"
              disabled={busy}
            />
          </div>
          <div className="form-row">
            <label>Тег*</label>
            <input
              type="text"
              value={tag}
              onChange={(e) => setTag(e.target.value)}
              placeholder="street"
              disabled={busy}
            />
          </div>
        </div>

        <div
          className={`bulk-dropzone${dragOver ? ' drag-over' : ''}${busy ? ' disabled' : ''}`}
          onDragOver={(e) => { e.preventDefault(); if (!busy) setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          onClick={() => { if (!busy) inputRef.current?.click(); }}
          role="button"
          tabIndex={0}
        >
          <div className="bulk-dropzone-main">
            {files.length === 0
              ? 'Перетащи сюда файлы или кликни, чтобы выбрать'
              : `Добавить ещё файлов`}
          </div>
          <div className="bulk-dropzone-hint">.mp4, .mov, .webm, .m4v, .zip</div>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPTED_EXT.join(',') + (mediaType === 'photo' ? ',image/*' : ',video/*') + ',application/zip'}
            onChange={onInputChange}
            disabled={busy}
            style={{ display: 'none' }}
          />
        </div>

        {files.length > 0 && (
          <div className="bulk-file-list">
            <div className="bulk-file-list-header">
              <span>{files.length} файл(ов) · {fmtSize(totalBytes)}</span>
              {!busy && <button className="btn-link" onClick={clearFiles}>очистить</button>}
            </div>
            <ul>
              {files.map((f, i) => (
                <li key={`${f.name}:${f.size}:${i}`} className="bulk-file-item">
                  <span className="bulk-file-name">{f.name}</span>
                  <span className="bulk-file-size">{fmtSize(f.size)}</span>
                  {!busy && (
                    <button className="btn-link-del" onClick={() => removeFile(i)} aria-label="remove">×</button>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {busy && (
          <div className="import-progress">
            <div className="import-progress-bar" style={{ width: `${progress}%` }} />
            <div className="import-progress-label">
              Загрузка {progress}% {progress === 100 ? '— сервер обрабатывает...' : ''}
            </div>
          </div>
        )}

        <div className="bulk-actions">
          <button className="btn-primary" onClick={submit} disabled={busy}>
            {busy ? 'Загрузка...' : `Загрузить${files.length ? ` (${files.length})` : ''}`}
          </button>
        </div>

        {err && <div className="bulk-msg bulk-msg-err">{err}</div>}

        {result && (
          <div className="bulk-result">
            <div>
              Загружено: <b>{result.uploaded}</b> в <code>{result.target_prefix}</code>
            </div>
            {result.errors.length > 0 && (
              <div className="bulk-errors">
                <div>Ошибки ({result.errors.length}):</div>
                <ul>
                  {result.errors.slice(0, 30).map((e, i) => (
                    <li key={i}><code>{e.file}</code>: {e.error}</li>
                  ))}
                  {result.errors.length > 30 && <li>... и ещё {result.errors.length - 30}</li>}
                </ul>
              </div>
            )}
          </div>
        )}

        <div className="bulk-hint">
          ZIP-архивы распаковываются на сервере, видео из них загружаются по отдельности.<br />
          Путь в S3: <code>{`{prefix}/{genre}/{tag}/{file_name}`}</code>
        </div>
      </div>
    </div>
  );
}
