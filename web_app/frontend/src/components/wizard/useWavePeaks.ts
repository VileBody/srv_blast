import { useEffect, useState } from 'react';

/*
 * Форма волны трека для выбора отрывка: громкость по N столбикам, 0…1.
 * Считается в браузере из самого файла (WebAudio), без запроса к бэку. Если файл не
 * раскодировался (CORS у ссылки, формат, который браузер не знает), волны нет — компонент
 * рисует ровную полосу, а не выдуманную форму.
 */
const cache = new Map<string, Promise<number[] | null>>();

async function decode(url: string, bars: number): Promise<number[] | null> {
  const AudioCtx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioCtx) return null;
  const response = await fetch(url);
  if (!response.ok) return null;
  const data = await response.arrayBuffer();
  const context = new AudioCtx();
  try {
    const buffer = await context.decodeAudioData(data);
    const channel = buffer.getChannelData(0);
    const size = Math.max(1, Math.floor(channel.length / bars));
    const peaks: number[] = [];
    for (let bar = 0; bar < bars; bar += 1) {
      let sum = 0;
      const start = bar * size;
      const end = Math.min(channel.length, start + size);
      // среднеквадратичное — ровнее пиков и честнее показывает громкие места
      for (let i = start; i < end; i += 16) sum += channel[i] * channel[i];
      peaks.push(Math.sqrt(sum / Math.max(1, (end - start) / 16)));
    }
    const max = Math.max(...peaks, 1e-6);
    return peaks.map((value) => value / max);
  } finally {
    void context.close();
  }
}

export function useWavePeaks(url: string | null, bars: number): number[] | null {
  const [peaks, setPeaks] = useState<number[] | null>(null);
  useEffect(() => {
    setPeaks(null);
    if (!url) return undefined;
    const key = `${bars}:${url}`;
    if (!cache.has(key)) cache.set(key, decode(url, bars).catch(() => null));
    let alive = true;
    void cache.get(key)!.then((value) => { if (alive) setPeaks(value); });
    return () => { alive = false; };
  }, [url, bars]);
  return peaks;
}
