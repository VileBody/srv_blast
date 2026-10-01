import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useWizardStore } from '../../stores/wizardStore';
import { dropToSeconds, timingToSeconds, usePlaybackUrl } from './useFragmentAudio';
import { PAUSE, PLAY } from './WizardFrame';

/** Сколько слушать до дропа и после: хватает, чтобы услышать нарастание и сам удар. */
const LEAD_S = 3;
const TAIL_S = 2;

const clock = (s: number) => {
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  return `${m}:${rest.toFixed(1).padStart(4, '0')}`;
};

/**
 * Прослушать дроп прямо на шаге FX: раньше, чтобы оценить тайминг, приходилось уходить на
 * «Трек». Играет окно вокруг выбранного дропа (3 с до и 2 с после, внутри отрывка), а без
 * дропа — весь отрывок. Таймер бежит по треку, рядом — отсчёт «до дропа». Смена кандидата
 * во время прослушивания перезапускает окно вокруг нового — так их удобно сравнивать на слух.
 */
export function DropListen() {
  const { t } = useTranslation();
  const track = useWizardStore((s) => s.track);
  const timingFrom = useWizardStore((s) => s.timingFrom);
  const timingTo = useWizardStore((s) => s.timingTo);
  const dropTime = useWizardStore((s) => s.hooks.dropTime);
  const url = usePlaybackUrl(track);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const rafRef = useRef(0);
  const [playing, setPlaying] = useState(false);
  const [now, setNow] = useState<number | null>(null);

  const clipFrom = timingToSeconds(timingFrom);
  const clipTo = timingToSeconds(timingTo);
  const drop = dropToSeconds(dropTime);
  const dropInClip = drop !== null && clipFrom !== null && clipTo !== null && drop >= clipFrom && drop <= clipTo;
  const range = clipFrom === null || clipTo === null || clipTo <= clipFrom ? null
    : dropInClip ? { from: Math.max(clipFrom, drop! - LEAD_S), to: Math.min(clipTo, drop! + TAIL_S) }
      : { from: clipFrom, to: clipTo };

  const stop = () => {
    cancelAnimationFrame(rafRef.current);
    audioRef.current?.pause();
    setPlaying(false);
    setNow(null);
  };

  const start = () => {
    if (!url || !range) return;
    let audio = audioRef.current;
    if (!audio || audio.src !== new URL(url, window.location.href).href) {
      audio?.pause();
      audio = new Audio(url);
      audio.preload = 'auto';
      audioRef.current = audio;
    }
    const a = audio;
    const end = range.to;
    const run = () => {
      a.currentTime = range.from;
      void a.play().catch(() => stop());
      setPlaying(true);
      const tick = () => {
        if (a.currentTime >= end || a.paused) { stop(); return; }
        setNow(a.currentTime);
        rafRef.current = requestAnimationFrame(tick);
      };
      cancelAnimationFrame(rafRef.current);
      rafRef.current = requestAnimationFrame(tick);
    };
    // сик до загрузки метаданных браузер молча игнорирует — ждём их
    if (a.readyState >= 1) run(); else a.addEventListener('loadedmetadata', run, { once: true });
  };

  // другой кандидат во время прослушивания — сразу слушаем вокруг него
  useEffect(() => {
    if (playing) start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dropTime]);
  // смена трека/отрывка и уход со страницы глушат звук
  useEffect(() => stop, [url, timingFrom, timingTo]);

  const left = playing && now !== null && dropInClip ? drop! - now : null;
  const disabled = !url || !range;

  return (
    <div className="w12-drop-listen">
      <span className="w12-num w12-drop-left" aria-live="off">
        {left === null
          ? t(dropInClip ? 'wizard.fx.dropListenHint' : 'wizard.fx.dropListenClipHint')
          : left > 0.05 ? t('wizard.fx.dropIn', { seconds: left.toFixed(1) }) : t('wizard.fx.dropNow')}
      </span>
      <button
        type="button"
        className="w12-drop-play"
        onClick={() => (playing ? stop() : start())}
        disabled={disabled}
        aria-label={playing ? t('wizard.fx.dropListenStop') : t(dropInClip ? 'wizard.fx.dropListen' : 'wizard.fx.dropListenClip')}
      >
        <span className="w12-dot">{playing ? PAUSE : PLAY}</span>
        <span className="w12-num w12-drop-clock">{clock(now ?? range?.from ?? 0)}</span>
      </button>
    </div>
  );
}
