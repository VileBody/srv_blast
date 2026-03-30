import type { PaginatedAssets, Taxonomy, ThemeAssignment } from './types';

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

export async function fetchVideoUrl(fileName: string): Promise<string> {
  const res = await fetch(`${BASE}/assets/${encodeURIComponent(fileName)}/video-url`);
  if (!res.ok) throw new Error(`fetchVideoUrl: ${res.status}`);
  const data = await res.json();
  return data.url;
}

export async function updateTags(
  fileName: string,
  assignments: ThemeAssignment[],
): Promise<void> {
  const res = await fetch(`${BASE}/assets/${encodeURIComponent(fileName)}/tags`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ theme_assignments: assignments }),
  });
  if (!res.ok) throw new Error(`updateTags: ${res.status}`);
}

export async function deleteAsset(fileName: string): Promise<void> {
  const res = await fetch(`${BASE}/assets/${encodeURIComponent(fileName)}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(`deleteAsset: ${res.status}`);
}
