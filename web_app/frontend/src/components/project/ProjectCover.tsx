import { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { cn } from '../../lib/cn';
import type { ProjectCoverTrack } from '../../lib/types';
import { wavePeaks } from '../wizard/useWavePeaks';

function isPlaceholder(url?: string | null): boolean {
  return !url || url.endsWith('/cover-placeholder.svg');
}

/**
 * Обложка проекта: своя картинка, если человек её загрузил, иначе — дорожка его трека.
 *
 * Дорожка — это не украшение, а сам проект: волна трека, из которого режутся ролики,
 * выбранный отрывок поверх неё и метка дропа. Раньше здесь была буква в рамке со
 * свечением — заглушка, которая ни о чём не говорила. Трека нет (генераций ещё не было
 * или срок хранения файла истёк) — ровная линия без подписи: подпись соврала бы в одном
 * из двух случаев.
 */
export function ProjectCover({ name, src, track, className }: { name: string; src?: string | null; track?: ProjectCoverTrack | null; className?: string }) {
  if (!isPlaceholder(src)) {
    return <img src={src ?? undefined} alt="" className={cn('object-cover', className)} />;
  }
  return <TrackCover name={name} track={track ?? null} className={className} />;
}

const BARS = 64;

/* ── волна: считается в браузере из файла трека и запоминается по треку ── */
interface Wave { peaks: number[]; duration: number }
const memory = new Map<string, Promise<Wave | null>>();
const storageKey = (trackId: string) => `blast:cover-wave:${BARS}:${trackId}`;

function readStored(trackId: string): Wave | null {
  try {
    const raw = localStorage.getItem(storageKey(trackId));
    const value = raw ? (JSON.parse(raw) as Wave) : null;
    return value && Array.isArray(value.peaks) && value.peaks.length === BARS && value.duration > 0 ? value : null;
  } catch {
    return null;
  }
}

async function decodeWave(trackId: string): Promise<Wave | null> {
  const AudioCtx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioCtx) return null;
  const response = await fetch(api.trackAudioUrl(trackId), { credentials: 'include' });
  if (!response.ok) return null;
  const context = new AudioCtx();
  try {
    const buffer = await context.decodeAudioData(await response.arrayBuffer());
    const wave = { peaks: wavePeaks(buffer, BARS).map((v) => Math.round(v * 100) / 100), duration: buffer.duration };
    try { localStorage.setItem(storageKey(trackId), JSON.stringify(wave)); } catch { /* хранилище недоступно — посчитаем в следующий раз */ }
    return wave;
  } finally {
    void context.close();
  }
}

/** Волна трека; null — ещё считается или файл не раскодировался (тогда ровная линия, без выдумки). */
function useTrackWave(trackId: string | null): Wave | null {
  const [wave, setWave] = useState<Wave | null>(() => (trackId ? readStored(trackId) : null));
  useEffect(() => {
    if (!trackId) { setWave(null); return undefined; }
    const stored = readStored(trackId);
    if (stored) { setWave(stored); return undefined; }
    if (!memory.has(trackId)) memory.set(trackId, decodeWave(trackId).catch(() => null));
    let alive = true;
    void memory.get(trackId)!.then((value) => { if (alive) setWave(value); });
    return () => { alive = false; };
  }, [trackId]);
  return wave;
}

const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;

function TrackCover({ name, track, className }: { name: string; track: ProjectCoverTrack | null; className?: string }) {
  const wave = useTrackWave(track?.trackId ?? null);
  const duration = wave?.duration || track?.durationS || 0;
  const pos = (sec: number | null | undefined) => (sec != null && duration > 0 ? Math.min(1, Math.max(0, sec / duration)) : null);
  const from = pos(track?.from);
  const to = pos(track?.to);
  const drop = pos(track?.drop);
  const hasSegment = from !== null && to !== null && to > from;

  // крупная обложка — 64 столбика, плитка на главной — 24 (иначе столбики тоньше пикселя)
  const bars = (count: number) => {
    const levels = Array.from({ length: count }, (_, i) => {
      if (!wave) return 0.06; // волны ещё нет — ровная линия, не выдуманная форма
      const a = Math.floor((i * BARS) / count);
      const b = Math.max(a + 1, Math.floor(((i + 1) * BARS) / count));
      return Math.max(0.1, ...wave.peaks.slice(a, b));
    });
    return (
      <span className={`project-cover-bars n${count}`}>
        {levels.map((level, i) => {
          const centre = (i + 0.5) / count;
          return <i key={i} className={hasSegment && centre >= from! && centre <= to! ? 'in' : undefined} style={{ height: `${Math.round(level * 100)}%` }} />;
        })}
      </span>
    );
  };

  return (
    <span className={cn('project-cover relative block overflow-hidden bg-panel', className)} role="img" aria-label={name}>
      {/* та же дорожка, что на шаге «Трек»: волна, рамка отрывка, линия дропа */}
      <span className="project-cover-lane" aria-hidden="true">
        {bars(BARS)}
        {bars(24)}
        {hasSegment && <span className="project-cover-win" style={{ left: `${from! * 100}%`, width: `${(to! - from!) * 100}%` }} />}
        {drop !== null && <span className="project-cover-drop" style={{ left: `${drop * 100}%` }} />}
      </span>
      {track && (
        <span className="project-cover-meta">
          <span className="nm">{track.filename || name}</span>{hasSegment && <span className="tm">{clock(track.from!)}–{clock(track.to!)}</span>}
        </span>
      )}
    </span>
  );
}
