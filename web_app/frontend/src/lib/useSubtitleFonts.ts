import { useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from './api';
import { injectFontFaces } from './subtitleText';

/**
 * Файлы шрифтов превью субтитров — из двух мест, без подмен:
 *   * бандл сайта `public/fonts/subtitles/` по `manifest.json` — шрифты с правом веб-раздачи
 *     (OFL и собственный Point), едут вместе со сборкой фронта;
 *   * сервер (`files` каталога, `/api/wizard/subtitle-font/<PS>.woff2` из S3) — то, что
 *     залито скриптом scripts/upload_subtitle_fonts_to_s3.py сверх бандла.
 * Шрифта нет ни там, ни там — превью пишет «не загружен» и не рисует.
 */
interface FontManifest { fonts: { ps: string; url: string }[] }

async function fetchManifest(): Promise<Record<string, string>> {
  const response = await fetch('/fonts/subtitles/manifest.json');
  if (!response.ok) throw new Error(`subtitle font manifest: HTTP ${response.status}`);
  const data = (await response.json()) as FontManifest;
  return Object.fromEntries((data.fonts ?? []).map((font) => [font.ps, font.url]));
}

export function useSubtitleFonts() {
  const catalogQuery = useQuery({ queryKey: ['subtitle-fonts'], queryFn: api.subtitleFonts, staleTime: Infinity });
  const manifestQuery = useQuery({ queryKey: ['subtitle-font-manifest'], queryFn: fetchManifest, staleTime: Infinity, retry: 1 });
  const catalog = catalogQuery.data;
  const files = useMemo(() => ({ ...(catalog?.files ?? {}), ...(manifestQuery.data ?? {}) }), [catalog?.files, manifestQuery.data]);
  useEffect(() => { injectFontFaces(files); }, [files]);
  // «готово» — оба источника ответили (манифест мог и не прийти: тогда его шрифтов просто нет)
  const settled = catalogQuery.isSuccess && !manifestQuery.isPending;
  return { catalog, files, settled };
}
