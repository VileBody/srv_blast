import { useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';
import type { SavedTrack } from '../../lib/types';
import { useWizardStore } from '../../stores/wizardStore';

/**
 * Ссылка на прослушивание трека.
 *
 * Сохранённый трек играет лёгкую копию со своего домена (`/api/wizard/media/track/:id`, AAC
 * 96 кбит/с — в 3–4 раза легче оригинала). Адрес один на все экраны и не протухает, поэтому
 * браузер качает трек один раз и дальше берёт из кэша: раньше каждый экран просил свежую
 * presigned-ссылку на оригинал, и трек скачивался заново. Файл из этой вкладки (blob:) — как есть.
 */
export function usePlaybackUrl(track: SavedTrack | null | undefined): string | null {
  const stored = track?.localUrl ?? null;
  if (stored?.startsWith('blob:') || !track?.id) return stored;
  return api.trackMediaUrl(String(track.id));
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

/** Секунды → запись дропа «мм:сс:сс» (третья пара — сотые), как её читает бэк. */
export function secondsToDropTime(seconds: number): string {
  const cs = Math.max(0, Math.round(seconds * 100));
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(Math.floor(cs / 6000))}:${pad(Math.floor(cs / 100) % 60)}:${pad(cs % 100)}`;
}

/** Секунды дропа в любой из двух форм записи. */
export function dropToSeconds(value: string | null | undefined): number | null {
  return value ? timingToSeconds(normalizeDropTime(value)) : null;
}

/**
 * Минимум от начала отрывка до дропа. Сборка требует дроп СТРОГО позже начала окна (F1 —
 * больше секунды), иначе оркестратор молча выкидывает весь хук-блок: ролик собирается,
 * но без выбранных эффектов. Тот же порог — в бэке (`production_backend.MIN_DROP_LEAD_S`).
 */
export const MIN_DROP_LEAD_S = 1;

/** Где дроп относительно отрывка: внутри, слишком близко к началу или вне окна. */
export function dropPlacement(drop: number | null, from: number | null, to: number | null): 'ok' | 'early' | 'outside' | null {
  if (drop === null || from === null || to === null) return null;
  if (drop < from || drop > to) return 'outside';
  return drop - from > MIN_DROP_LEAD_S ? 'ok' : 'early';
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
