import { useEffect, useState } from 'react';

/*
 * Форма волны трека для выбора отрывка: громкость по N столбикам, 0…1.
 * Считается в браузере из самого файла (WebAudio), без запроса к бэку. Если файл не
 * раскодировался (CORS у ссылки, формат, который браузер не знает), волны нет — компонент
 * рисует ровную полосу, а не выдуманную форму.
 *
 * Почему не «RMS / максимум»: у сведённой музыки громкость почти везде одинаковая, и линейная
 * шкала давала сплошной кирпич, а тихие места сжимала в точки. Поэтому громкость — в децибелах
 * (так её и слышит ухо), верх шкалы — уровень громких частей трека (95-й перцентиль, а не один
 * случайный пик), видимый диапазон — 30 дБ под ним. Интро, нарастания и дропы читаются формой.
 */
const RANGE_DB = 30;
const cache = new Map<string, Promise<number[] | null>>();

export function wavePeaks(buffer: AudioBuffer, bars: number): number[] {
  const channels = Array.from({ length: buffer.numberOfChannels }, (_, index) => buffer.getChannelData(index));
  const length = buffer.length;
  const size = Math.max(1, Math.floor(length / bars));
  const step = Math.max(1, Math.floor(size / 2048)); // ~2 тыс. отсчётов на столбик хватает
  const levels: number[] = [];
  for (let bar = 0; bar < bars; bar += 1) {
    const start = bar * size;
    const end = Math.min(length, start + size);
    let sum = 0;
    let count = 0;
    for (let i = start; i < end; i += step) {
      // моно-сумма каналов: стерео-партия в одном канале не пропадает
      let sample = 0;
      for (const channel of channels) sample += channel[i];
      sample /= channels.length;
      sum += sample * sample;
      count += 1;
    }
    const rms = Math.sqrt(sum / Math.max(1, count));
    levels.push(20 * Math.log10(Math.max(rms, 1e-5)));
  }
  const sorted = [...levels].sort((a, b) => a - b);
  const top = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))];
  return levels.map((db) => Math.min(1, Math.max(0, (db - (top - RANGE_DB)) / RANGE_DB)));
}

async function decode(url: string, bars: number): Promise<number[] | null> {
  const AudioCtx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioCtx) return null;
  const response = await fetch(url);
  if (!response.ok) return null;
  const data = await response.arrayBuffer();
  const context = new AudioCtx();
  try {
    return wavePeaks(await context.decodeAudioData(data), bars);
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
