import { useCallback, useEffect, useState } from 'react';
import type { Asset } from '../types';
import { fetchAssets, deleteAsset as apiDelete, type MediaType } from '../api';

export function useAssets(genre?: string, tag?: string, mediaType: MediaType = 'video', bucket?: string) {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [total, setTotal] = useState(0);
  const [index, setIndex] = useState(0);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // Load all assets (paginate through) — scoped to the selected pool, and to
      // one bucket (vibe) when chosen so we browse exactly that bucket's clips.
      const first = await fetchAssets(1, 500, genre, tag, mediaType, bucket);
      let all = first.items;
      const pages = Math.ceil(first.total / 500);
      for (let p = 2; p <= pages; p++) {
        const page = await fetchAssets(p, 500, genre, tag, mediaType, bucket);
        all = all.concat(page.items);
      }
      setAssets(all);
      setTotal(all.length);
      setIndex(0);  // reset to the first clip whenever the filter changes
    } catch (e) {
      console.error('Failed to load assets', e);
    } finally {
      setLoading(false);
    }
  }, [genre, tag, mediaType, bucket]);

  useEffect(() => { load(); }, [load]);

  const current = assets[index] ?? null;

  const next = useCallback(() => {
    setIndex((i) => Math.min(i + 1, assets.length - 1));
  }, [assets.length]);

  const prev = useCallback(() => {
    setIndex((i) => Math.max(i - 1, 0));
  }, []);

  const remove = useCallback(async () => {
    if (!current) return;
    try {
      await apiDelete(current.file_name, current.s3_key, mediaType);
      setAssets((prev) =>
        prev.filter((a) =>
          current.s3_key
            ? a.s3_key !== current.s3_key
            : a.file_name !== current.file_name,
        ),
      );
      setTotal((t) => Math.max(0, t - 1));
    } catch (e) {
      console.error('Failed to delete asset', e);
    }
  }, [current, mediaType]);

  return { assets, current, index, total, loading, next, prev, remove, reload: load };
}
