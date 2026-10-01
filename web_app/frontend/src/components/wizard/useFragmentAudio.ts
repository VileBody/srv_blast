import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import type { SavedTrack } from '../../lib/types';
import { useWizardStore } from '../../stores/wizardStore';

/**
 * Ссылка на прослушивание трека — всегда свежая с бэка.
 *
 * `track.localUrl` в сторе — presigned-URL на 24 ч, снятый в момент загрузки; черновик
 * визарда переживает его в localStorage, и после этого плеер молча получал 403.
 * Локальные `/static/...` (mock) отдаём как есть — их подписывать нечем и незачем.
 */
export function usePlaybackUrl(track: SavedTrack | null | undefined): string | null {
  const stored = track?.localUrl ?? null;
  // /static/ (mock) и blob: (файл из этой вкладки) играют как есть — свежая ссылка нужна только S3
  const needsFresh = Boolean(track?.id) && !/^(\/static\/|blob:)/.test(stored ?? '');
  const fresh = useQuery({
    queryKey: ['track-playback', track?.id],
    queryFn: () => api.trackPlayback(String(track?.id)),
    enabled: needsFresh,
    staleTime: 6 * 60 * 60_000,
    retry: 1
  });
  if (!needsFresh) return stored;
  return fresh.data?.url ?? null;
}

/**
 * Откуда браузеру скачать трек, чтобы посчитать волну. Плеер играет presigned-ссылку S3, но
 * `fetch` с чужого домена упирается в CORS бакета — волна выходила ровной полосой. Поэтому для
 * сохранённого трека данные идут со своего домена (`/api/wizard/track-audio`); /static (мок) и
 * blob: (файл из этой вкладки) — как есть.
 */
export function useWaveSourceUrl(track: SavedTrack | null | undefined, blobUrl?: string | null): string | null {
  if (blobUrl) return blobUrl;
  const stored = track?.localUrl ?? null;
  if (stored && /^(\/static\/|blob:)/.test(stored)) return stored;
  return track?.id ? api.trackAudioUrl(String(track.id)) : null;
}

/**
 * «01:02:44» или «01:02» → секунды (мм:сс[:мс], мс — сотые и необязательны).
 * Раньше без третьей пары визард молча не пускал дальше — «00:11» считался невалидным,
 * и никто не понимал, что не так. Бэк (`render_job.mmss_seconds`) обе формы читает одинаково.
 */
export function timingToSeconds(value: string): number | null {
  const parsed = /^(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value);
  if (!parsed) return null;
  return Number(parsed[1]) * 60 + Number(parsed[2]) + (parsed[3] ? Number(parsed[3]) / 100 : 0);
}

/*
 * Тайминг дропа приходит из анализа как «mm:ss», а ручной ввод — как «mm:ss:cs».
 * Храним всегда трёхчастную форму: её же ждёт бэк (parse_mmssms), который двухчастную
 * читает как «ss:cs» и ставит дроп в начало трека.
 */
export function normalizeDropTime(value: string): string {
  return /^\d{2}:\d{2}$/.test(value) ? `${value}:00` : value;
}

/** Секунды дропа в любой из двух форм записи. */
export function dropToSeconds(value: string | null | undefined): number | null {
  return value ? timingToSeconds(normalizeDropTime(value)) : null;
}

/**
 * Проигрывание ВЫБРАННОГО ОТРЫВКА загруженного трека — поверх любого превью визарда.
 *
 * До этого послушать трек можно было только на первом шаге, и превью футажа оставалось
 * абстрактным: человек выбирал фон, не понимая, как он ляжет на его музыку. Берём файл по
 * `localUrl` (он лежит на бэке), а не blob-ссылку первого шага: blob живёт в одном экране
 * и умирает после перезагрузки страницы.
 */
export function useFragmentAudio() {
  const track = useWizardStore((state) => state.track);
  const timingFrom = useWizardStore((state) => state.timingFrom);
  const timingTo = useWizardStore((state) => state.timingTo);
  const [playing, setPlaying] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const url = usePlaybackUrl(track);

  // Смена трека и уход со страницы обязаны глушить звук: иначе музыка играет «из ниоткуда»
  useEffect(() => {
    audioRef.current?.pause();
    audioRef.current = null;
    setPlaying(false);
  }, [url]);
  useEffect(() => () => {
    audioRef.current?.pause();
    audioRef.current = null;
  }, []);

  const toggle = () => {
    if (!url) return;
    if (!audioRef.current) {
      audioRef.current = new Audio(url);
      audioRef.current.onended = () => setPlaying(false);
    }
    const audio = audioRef.current;
    if (playing) {
      audio.pause();
      setPlaying(false);
      return;
    }
    const from = timingToSeconds(timingFrom);
    const to = timingToSeconds(timingTo);
    // Играем ровно отрывок, который уедет в ролик, а не трек целиком
    audio.ontimeupdate = to !== null && (from === null || to > from)
      ? () => {
          if (audio.currentTime >= to) {
            audio.pause();
            setPlaying(false);
            audio.ontimeupdate = null;
          }
        }
      : null;
    audio.currentTime = from ?? 0;
    void audio.play();
    setPlaying(true);
  };

  return { available: Boolean(url), playing, toggle };
}
