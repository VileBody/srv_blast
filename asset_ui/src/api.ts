import type { PaginatedAssets, Taxonomy, ThemeAssignment, TagOverrides } from './types';

const BASE = 'api';

export async function fetchAssets(
  page = 1,
  perPage = 50,
  genre?: string,
  tag?: string,
): Promise<PaginatedAssets> {
  const params = new URLSearchParams({ page: String(page), per_page: String(perPage) });
  if (genre) params.set('genre', genre);
  if (tag) params.set('tag', tag);
  const res = await fetch(`${BASE}/assets?${params}`);
  if (!res.ok) throw new Error(`fetchAssets: ${res.status}`);
  return res.json();
}

export async function fetchTaxonomy(): Promise<Taxonomy> {
  const res = await fetch(`${BASE}/assets/taxonomy`);
  if (!res.ok) throw new Error(`fetchTaxonomy: ${res.status}`);
  const data = await res.json();
  return data.themes;
}

export async function updateTags(
  fileName: string,
  assignments: ThemeAssignment[],
  s3Key?: string,
): Promise<void> {
  const params = new URLSearchParams();
  if (s3Key) params.set('s3_key', s3Key);
  const suffix = params.toString() ? `?${params.toString()}` : '';
  const res = await fetch(`${BASE}/assets/${encodeURIComponent(fileName)}/tags${suffix}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ theme_assignments: assignments }),
  });
  if (!res.ok) throw new Error(`updateTags: ${res.status}`);
}

export async function fetchVideoUrl(fileName: string, s3Key?: string): Promise<string> {
  const params = new URLSearchParams();
  if (s3Key) params.set('s3_key', s3Key);
  const suffix = params.toString() ? `?${params.toString()}` : '';
  const res = await fetch(`${BASE}/assets/${encodeURIComponent(fileName)}/video-url${suffix}`);
  if (!res.ok) throw new Error(`fetchVideoUrl: ${res.status}`);
  const data = await res.json();
  return data.url;
}

export async function deleteAsset(fileName: string, s3Key?: string): Promise<void> {
  const params = new URLSearchParams();
  if (s3Key) params.set('s3_key', s3Key);
  const suffix = params.toString() ? `?${params.toString()}` : '';
  const res = await fetch(`${BASE}/assets/${encodeURIComponent(fileName)}${suffix}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(`deleteAsset: ${res.status}`);
}

// --- Tag-level overrides ---

export async function fetchTagOverrides(): Promise<TagOverrides> {
  const res = await fetch(`${BASE}/tag-overrides`);
  if (!res.ok) throw new Error(`fetchTagOverrides: ${res.status}`);
  return res.json();
}

export async function blacklistTag(tag: string): Promise<void> {
  const res = await fetch(`${BASE}/tag-overrides/blacklist`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tag }),
  });
  if (!res.ok) throw new Error(`blacklistTag: ${res.status}`);
}

export async function unblacklistTag(tag: string): Promise<void> {
  const res = await fetch(`${BASE}/tag-overrides/blacklist/${encodeURIComponent(tag)}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(`unblacklistTag: ${res.status}`);
}

export async function assignTag(tag: string, theme: string, group: string): Promise<void> {
  const res = await fetch(`${BASE}/tag-overrides/assign`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tag, theme, group }),
  });
  if (!res.ok) throw new Error(`assignTag: ${res.status}`);
}

export async function unassignTag(tag: string, theme: string, group: string): Promise<void> {
  const res = await fetch(`${BASE}/tag-overrides/assign`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tag, theme, group }),
  });
  if (!res.ok) throw new Error(`unassignTag: ${res.status}`);
}

// --- Bulk export / import ---

export interface ExportManifestItem {
  file_name: string;
  s3_key?: string;
  genre: string;
  tag: string;
  duration_sec: number;
  src_w?: number;
  src_h?: number;
  download_url?: string;
}

export interface ExportManifest {
  generated_at: string;
  total: number;
  filters: { genre?: string | null; tag?: string | null };
  expires_in_sec: number | null;
  items: ExportManifestItem[];
}

export interface ImportResult {
  uploaded: number;
  uploaded_files: { file_name: string; s3_key: string }[];
  errors: { file: string; error: string }[];
  target_prefix: string;
}

function buildExportParams(genre?: string, tag?: string, format: 'manifest' | 'zip' = 'manifest'): URLSearchParams {
  const params = new URLSearchParams({ format });
  if (genre) params.set('genre', genre);
  if (tag) params.set('tag', tag);
  return params;
}

export async function fetchExportManifest(genre?: string, tag?: string): Promise<ExportManifest> {
  const params = buildExportParams(genre, tag, 'manifest');
  const res = await fetch(`${BASE}/assets/export?${params}`);
  if (!res.ok) throw new Error(`fetchExportManifest: ${res.status}`);
  return res.json();
}

export function exportZipUrl(genre?: string, tag?: string): string {
  const params = buildExportParams(genre, tag, 'zip');
  return `${BASE}/assets/export?${params}`;
}

// --- Server-side auto-tagging (Groq Vision) ---

export interface TaggingStatus {
  state: 'idle' | 'queued' | 'running' | 'done' | 'failed' | 'unknown';
  done?: number;
  total?: number;
  written?: number;
  failed?: number;
  untagged_processed?: number;
  total_s3?: number;
  already_tagged?: number;
  error?: string;
  updated_at?: number;
}

export type MediaType = 'video' | 'photo';

export async function startTagUntagged(
  limit = 0,
  mediaType: MediaType = 'video',
): Promise<{ ok: boolean; task_id: string }> {
  const params = new URLSearchParams();
  if (limit > 0) params.set('limit', String(limit));
  if (mediaType !== 'video') params.set('media_type', mediaType);
  const suffix = params.toString() ? `?${params.toString()}` : '';
  const res = await fetch(`${BASE}/assets/tag-untagged${suffix}`, { method: 'POST' });
  if (res.status === 409) throw new Error('Разметка уже выполняется');
  if (!res.ok) {
    // Surface the server's `detail` (e.g. broker/redis unavailable) instead of a bare code.
    let detail = '';
    try {
      detail = (await res.json()).detail ?? '';
    } catch {
      /* non-JSON body */
    }
    throw new Error(detail ? `${res.status}: ${detail}` : `startTagUntagged: ${res.status}`);
  }
  return res.json();
}

export async function fetchTagUntaggedStatus(mediaType: MediaType = 'video'): Promise<TaggingStatus> {
  const q = mediaType !== 'video' ? `?media_type=${mediaType}` : '';
  const res = await fetch(`${BASE}/assets/tag-untagged/status${q}`);
  if (!res.ok) throw new Error(`fetchTagUntaggedStatus: ${res.status}`);
  return res.json();
}

export interface ActivationStatus {
  state: 'idle' | 'queued' | 'running' | 'done' | 'failed' | 'unknown';
  phase?: 'starting' | 'indexing' | 'inventory' | 'tagging' | 'snapshot';
  done?: number;
  total?: number;
  written?: number;
  failed?: number;
  failure_reasons?: Record<string, number>;
  indexed?: number;
  error?: string;
  updated_at?: number;
}

export async function startActivate(
  limit = 0,
  mediaType: MediaType = 'video',
): Promise<{ ok: boolean; task_id: string }> {
  const params = new URLSearchParams();
  if (limit > 0) params.set('limit', String(limit));
  if (mediaType !== 'video') params.set('media_type', mediaType);
  const suffix = params.toString() ? `?${params.toString()}` : '';
  const res = await fetch(`${BASE}/assets/activate${suffix}`, { method: 'POST' });
  if (res.status === 409) throw new Error('Активация уже выполняется');
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json()).detail ?? ''; } catch { /* non-JSON */ }
    throw new Error(detail ? `${res.status}: ${detail}` : `startActivate: ${res.status}`);
  }
  return res.json();
}

export async function fetchActivateStatus(mediaType: MediaType = 'video'): Promise<ActivationStatus> {
  const q = mediaType !== 'video' ? `?media_type=${mediaType}` : '';
  const res = await fetch(`${BASE}/assets/activate/status${q}`);
  if (!res.ok) throw new Error(`fetchActivateStatus: ${res.status}`);
  return res.json();
}

export function importAssets(
  files: File[],
  genre: string,
  tag: string,
  onProgress?: (pct: number) => void,
  mediaType: MediaType = 'video',
): Promise<ImportResult> {
  const params = new URLSearchParams({ genre, tag });
  if (mediaType !== 'video') params.set('media_type', mediaType);
  return new Promise<ImportResult>((resolve, reject) => {
    const form = new FormData();
    for (const file of files) form.append('files', file, file.name);
    const xhr = new XMLHttpRequest();
    xhr.upload.addEventListener('progress', (e) => {
      if (onProgress && e.lengthComputable && e.total > 0) {
        onProgress(Math.min(100, Math.round((e.loaded / e.total) * 100)));
      }
    });
    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch (err) {
          reject(new Error(`importAssets: bad JSON (${(err as Error).message})`));
        }
      } else {
        reject(new Error(`importAssets: ${xhr.status} ${xhr.responseText.slice(0, 300)}`));
      }
    });
    xhr.addEventListener('error', () => reject(new Error('importAssets: network error')));
    xhr.addEventListener('abort', () => reject(new Error('importAssets: aborted')));
    xhr.open('POST', `${BASE}/assets/import?${params}`);
    xhr.send(form);
  });
}
