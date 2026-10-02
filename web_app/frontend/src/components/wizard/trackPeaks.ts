import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import type { SavedTrack } from '../../lib/types';

/*
 * Громкость трека — с сервера, а не из файла.
 *
 * Раньше волну каждый экран считал сам: шаг «Трек», «Текст», «Биты» стола и обложки проектов
 * скачивали трек целиком (часто по разу на экран) и раскодировали его в браузере. Теперь сервер
 * один раз на трек считает громкость каждые 50 мс (`/api/wizard/media/track/:id/peaks`), а
 * столбики любой ширины и для любого отрывка собираются здесь — той же шкалой, что раньше
 * (`wavePeaks`: громкость в дБ, верх — громкие части окна, 30 дБ под ним).
 *
 * Свежезагруженный в этой вкладке трек (blob:) ещё не на сервере — его волну шаг «Трек» по-прежнему
 * считает из файла в памяти: качать при этом нечего.
 */
export interface TrackPeaks { rate: number; duration: number; rms: number[] }

const RANGE_DB = 30;

/** Пики сохранённого трека; null — трека нет на сервере (blob) или ещё грузятся. */
export function useTrackPeaks(track: SavedTrack | null | undefined): TrackPeaks | null {
  const id = track?.id && !String(track.localUrl ?? '').startsWith('blob:') ? String(track.id) : null;
  const query = useQuery({
    queryKey: ['track-peaks', id],
    queryFn: () => api.trackPeaks(id!),
    enabled: Boolean(id),
    staleTime: Infinity,
    gcTime: 60 * 60_000,
    retry: 1
  });
  return query.data ?? null;
}

/** Столбики 0…1 для окна [from, to] секунд трека — `bars` штук, шкала как у волны трека. */
export function peakLevels(peaks: TrackPeaks, from: number, to: number, bars: number): number[] {
  const n = peaks.rms.length;
  const a0 = Math.max(0, Math.floor(from * peaks.rate));
  const b0 = Math.min(n, Math.ceil(to * peaks.rate));
  const span = Math.max(1, b0 - a0);
  const db: number[] = [];
  for (let i = 0; i < bars; i += 1) {
    const a = a0 + Math.floor((i * span) / bars);
    const b = Math.max(a + 1, a0 + Math.floor(((i + 1) * span) / bars));
    let sum = 0;
    let count = 0;
    for (let k = a; k < Math.min(b, n); k += 1) { sum += peaks.rms[k] * peaks.rms[k]; count += 1; }
    db.push(20 * Math.log10(Math.max(Math.sqrt(sum / Math.max(1, count)), 1e-5)));
  }
  const sorted = [...db].sort((x, y) => x - y);
  const top = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] ?? 0;
  return db.map((v) => Math.min(1, Math.max(0, (v - (top - RANGE_DB)) / RANGE_DB)));
}
