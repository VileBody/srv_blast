import { useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';
import type { SavedTrack } from '../../lib/types';
import { useWizardStore, type HookKind } from '../../stores/wizardStore';

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
 * Сколько трека нужно ДО дропа каждому типу хука (секунды от начала отрывка). Дроп на самом
 * старте — нормальный приём (отрывок с припева), и «Эффектам» его хватает: молния встаёт на
 * первый кадр. Остальным нужен разгон до дропа, иначе часть хука молча не попадёт в ролик:
 * «Объект» ставит форму на склейку ДО дропа, «Прогрев» играет звук в окне до него (F1 требует
 * больше секунды), «Движение» — жест-интро (F4_MIN_INTRO_SEC), «Мысль» — голос перед дропом.
 * Такие хуки при раннем дропе не настраиваются. Та же таблица — в бэке (`render_job.DROP_LEAD_S`).
 */
export const DROP_LEAD_S: Record<HookKind, number> = { none: 0, effects: 0, warmup: 1, thought: 1, object: 2, motion: 3 };

/** Секунды от начала отрывка до дропа; null — дропа нет или он вне окна. */
export function dropLead(drop: number | null, from: number | null, to: number | null): number | null {
  if (drop === null || from === null || to === null || drop < from || drop > to) return null;
  return drop - from;
}

/** Хватает ли хуку трека до дропа: «Эффектам» — дроп хоть на первом кадре, остальным — строго больше порога. */
export function hookFitsDrop(kind: HookKind, lead: number): boolean {
  const need = DROP_LEAD_S[kind];
  return need === 0 ? lead >= 0 : lead > need;
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
  /*
   * Состояние берём из событий самого <audio>, а не ставим «играет» сразу после play():
   * раньше при отклонённом play() (копия трека ещё готовится, сеть, политика автоплея)
   * интерфейс считал, что трек играет, — кнопка пряталась, тишина, второй клик «ставил на
   * паузу» то, что не играло. Выглядело так, будто кнопка не работает вовсе.
   */
  const [status, setStatus] = useState<'idle' | 'loading' | 'playing'>('idle');
  const audioRef = useRef<HTMLAudioElement | null>(null);
  /** запуск, ждущий метаданных (сик до них браузер может проигнорировать) — один на плеер */
  const pendingRef = useRef<(() => void) | null>(null);
  /** где остановиться: конец отрывка, если он задан */
  const endRef = useRef<number | null>(null);
  const url = usePlaybackUrl(track);

  const dropPending = () => {
    const audio = audioRef.current;
    if (audio && pendingRef.current) audio.removeEventListener('loadedmetadata', pendingRef.current);
    pendingRef.current = null;
  };

  const stop = () => {
    // без снятия ожидающего запуска медленная сеть доигрывала бы его после стопа
    dropPending();
    audioRef.current?.pause();
    setStatus('idle');
  };

  const release = () => {
    stop();
    audioRef.current = null;
  };

  // Смена трека и уход со страницы обязаны глушить звук: иначе музыка играет «из ниоткуда»
  useEffect(() => release, [url]);
  // другой отрывок — прежний запуск больше не про него
  useEffect(() => stop, [timingFrom, timingTo]);

  const ensureAudio = (src: string) => {
    if (audioRef.current) return audioRef.current;
    const audio = new Audio(src);
    audio.preload = 'auto';
    const mine = () => audioRef.current === audio;
    audio.addEventListener('playing', () => { if (mine()) setStatus('playing'); });
    // буферизация посреди отрывка — показываем ожидание, а не «играет» в тишине
    audio.addEventListener('waiting', () => { if (mine()) setStatus((s) => (s === 'idle' ? s : 'loading')); });
    audio.addEventListener('pause', () => { if (mine()) setStatus('idle'); });
    audio.addEventListener('ended', () => { if (mine()) setStatus('idle'); });
    audio.addEventListener('timeupdate', () => {
      // Играем ровно отрывок, который уедет в ролик, а не трек целиком
      const end = endRef.current;
      if (mine() && end !== null && audio.currentTime >= end) audio.pause();
    });
    audio.addEventListener('error', () => {
      if (!mine()) return;
      // сломанный элемент не переиспользуем: следующий клик попробует заново (копия могла дособраться)
      dropPending();
      audioRef.current = null;
      setStatus('idle');
    });
    audioRef.current = audio;
    return audio;
  };

  const start = () => {
    if (!url) return;
    const audio = ensureAudio(url);
    const from = timingToSeconds(timingFrom);
    const to = timingToSeconds(timingTo);
    endRef.current = to !== null && (from === null || to > from) ? to : null;
    setStatus('loading');
    const run = () => {
      pendingRef.current = null;
      audio.currentTime = from ?? 0;
      audio.play().catch(() => { if (audioRef.current === audio) setStatus('idle'); });
    };
    dropPending();
    if (audio.readyState >= 1) { run(); return; }
    pendingRef.current = run;
    audio.addEventListener('loadedmetadata', run, { once: true });
  };

  const toggle = () => (status === 'idle' ? start() : stop());

  return { available: Boolean(url), playing: status === 'playing', loading: status === 'loading', toggle };
}
