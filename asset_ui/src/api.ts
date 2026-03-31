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
