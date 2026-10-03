import { RefObject, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

/*
 * Видео, которое ещё не играет, не должно выглядеть как готовая картинка.
 *
 * У превью клипов есть JPEG-кадр (poster.jpg) — он приходит за доли секунды, а лёгкую копию
 * самого клипа сервер при первом показе ещё сжимает (секунды, в очереди — дольше). Обложка на
 * это время лучше чёрного прямоугольника, но без пометки человек принимал её за «картинки
 * вместо видео». Поэтому поверх обложки — явная загрузка, и она гаснет на первом живом кадре.
 *
 * Ответ с ошибкой (сервер не успел сжать клип за своё окно ожидания — 504, обрыв сети) не
 * оставляет вечную обложку: <video> сам не перезапрашивает, перезапрашиваем мы, с паузой.
 * Сжатие на сервере при этом продолжается, так что повтор обычно получает готовую копию.
 */

export type VideoLoad = 'ready' | 'loading' | 'failed';

const HAVE_CURRENT_DATA = 2;
const HAVE_FUTURE_DATA = 3;
/** повторы после ошибки: 1,5 с, 3 с, 4,5 с, 6 с — дальше показываем сбой */
const RETRIES = 4;
const RETRY_STEP_MS = 1500;

/** Состояние загрузки <video> по ref: живой кадр есть — ready; ждёт данных — loading. */
export function useVideoLoad(ref: RefObject<HTMLVideoElement>, src: string | null | undefined): VideoLoad {
  const [state, setState] = useState<VideoLoad>(src ? 'loading' : 'ready');
  useEffect(() => {
    const video = ref.current;
    if (!video || !src) { setState('ready'); return undefined; }
    let retries = 0;
    let timer = 0;
    // кадр на экране есть, как только пришли данные текущей позиции
    const sync = () => setState(video.readyState >= HAVE_CURRENT_DATA ? 'ready' : 'loading');
    // играло и встало ждать сеть — тоже загрузка, а не «замершая картинка»
    const waiting = () => { if (!video.paused && video.readyState < HAVE_FUTURE_DATA) setState('loading'); };
    const failed = () => {
      if (retries >= RETRIES) { setState('failed'); return; }
      retries += 1;
      setState('loading');
      window.clearTimeout(timer);
      timer = window.setTimeout(() => video.load(), RETRY_STEP_MS * retries);
    };
    const ok = () => { retries = 0; sync(); };
    sync();
    const passive = ['loadstart', 'emptied', 'seeking'] as const;
    const good = ['loadeddata', 'canplay', 'playing', 'seeked'] as const;
    passive.forEach((e) => video.addEventListener(e, sync));
    good.forEach((e) => video.addEventListener(e, ok));
    video.addEventListener('waiting', waiting);
    video.addEventListener('error', failed);
    return () => {
      window.clearTimeout(timer);
      passive.forEach((e) => video.removeEventListener(e, sync));
      good.forEach((e) => video.removeEventListener(e, ok));
      video.removeEventListener('waiting', waiting);
      video.removeEventListener('error', failed);
    };
  }, [ref, src]);
  return state;
}

/**
 * Пометка поверх обложки, пока видео грузится: бегущий блик + «Загружаем видео…».
 * Появляется с задержкой (CSS), чтобы клип из кэша не мигал плашкой.
 */
export function VideoLoadingBadge({ state, className = '' }: { state: Exclude<VideoLoad, 'ready'>; className?: string }) {
  const { t } = useTranslation();
  return (
    <span className={`vload${state === 'failed' ? ' failed' : ''} ${className}`} role="status" aria-live="polite">
      <span className="vload-pill">
        {state === 'loading' && <span className="spinner" aria-hidden="true" />}
        <span className="tx">{t(state === 'failed' ? 'wizard.pool.videoFailed' : 'wizard.pool.videoLoading')}</span>
      </span>
    </span>
  );
}

/**
 * Неподвижный кадр там, где видео само не стартует (экономия трафика — играет по наведению
 * или тапу): значок «плей», чтобы кадр не выглядел готовой картинкой.
 */
export function VideoPlayHint() {
  return (
    <span className="vplay" aria-hidden="true">
      <svg viewBox="0 0 20 20" width="16" height="16"><path d="M6 3.5v13l11-6.5L6 3.5Z" fill="currentColor" /></svg>
    </span>
  );
}
