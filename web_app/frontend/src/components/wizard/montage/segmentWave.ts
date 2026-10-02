import { useEffect, useState } from 'react';
import { wavePeaks } from '../useWavePeaks';

/*
 * Волна отрывка для дорожки «Биты» на монтажном столе. Раньше там были одни точки битов, и
 * склейку, хук или стиль приходилось ставить «на глаз» — без самой музыки под рукой.
 *
 * Файл трека декодируется один раз на ссылку (тот же источник, что у волны шага «Трек»),
 * пики считаются по окну отрывка тем же способом, что у волны трека (`wavePeaks`: громкость
 * в дБ, верх шкалы — громкие части окна), поэтому видны вступление, нарастание и дроп.
 * Не раскодировалось — волны нет, дорожка остаётся с битами, как раньше.
 */
const decoded = new Map<string, Promise<AudioBuffer | null>>();

function decode(url: string): Promise<AudioBuffer | null> {
  if (!decoded.has(url)) {
    decoded.set(url, (async () => {
      const AudioCtx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!AudioCtx) return null;
      const response = await fetch(url, { credentials: 'include' });
      if (!response.ok) return null;
      const context = new AudioCtx();
      try {
        return await context.decodeAudioData(await response.arrayBuffer());
      } finally {
        void context.close();
      }
    })().catch(() => null));
  }
  return decoded.get(url)!;
}

/** Окно [start, end] трека отдельным буфером — пики считаются только по нему. */
function slice(buffer: AudioBuffer, start: number, end: number): AudioBuffer | null {
  const rate = buffer.sampleRate;
  const from = Math.max(0, Math.floor(start * rate));
  const to = Math.min(buffer.length, Math.ceil(end * rate));
  if (to - from < 2) return null;
  const out = new AudioBuffer({ length: to - from, sampleRate: rate, numberOfChannels: buffer.numberOfChannels });
  for (let ch = 0; ch < buffer.numberOfChannels; ch += 1) out.copyToChannel(buffer.getChannelData(ch).subarray(from, to), ch);
  return out;
}

/** Пики отрывка (0…1), `bars` столбиков на всё окно; null — ещё считается или не вышло. */
export function useSegmentWave(url: string | null, start: number, end: number, bars: number): number[] | null {
  const [peaks, setPeaks] = useState<number[] | null>(null);
  useEffect(() => {
    setPeaks(null);
    if (!url || !(end > start) || bars < 1) return undefined;
    let alive = true;
    void decode(url).then((buffer) => {
      if (!alive || !buffer) return;
      const part = slice(buffer, start, end);
      if (part) setPeaks(wavePeaks(part, bars));
    });
    return () => { alive = false; };
  }, [url, start, end, bars]);
  return peaks;
}
