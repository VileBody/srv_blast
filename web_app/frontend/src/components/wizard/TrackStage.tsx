import { KeyboardEvent, PointerEvent, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api, ApiError } from '../../lib/api';
import { cn } from '../../lib/cn';
import { AUDIO_FILE_ACCEPT, isAudioFile } from '../../lib/mediaFiles';
import { useToast } from '../../contexts/ToastContext';
import { useWizardStore } from '../../stores/wizardStore';
import { ActionGuideOverlay } from '../guidance/ActionGuideOverlay';
import { useGuideDismiss } from '../guidance/useGuideDismiss';
import { formatClock, formatSeconds, parseClock, snapTenth, SEGMENT_SECONDS, toStoreTiming } from './timing';
import { timingToSeconds, usePlaybackUrl, useWaveSourceUrl } from './useFragmentAudio';
import { useWavePeaks } from './useWavePeaks';
import { PAUSE, PLAY, Svg, W12 } from './WizardFrame';
import { useLyricsUndo, useTried } from './wizardAttempt';

/*
 * Шаг «Трек» — разметка и поведение по макету wizard12 v3.
 *
 * Отрывок выбирается на волне трека: клик ставит окно на лимит тарифа, края тянутся, окно
 * двигается целиком, стрелки двигают края на 0,1 с (с Shift на 1 с). Поля «Начало / Конец»
 * — для точного ввода в формате плеера «0:40» или «0:40.5». Длина и лимит видны на окне и
 * рядом с полями; перебор подсвечивается, введённое не стирается. Сдвиг окна очищает текст
 * отрывка, но с «Вернуть» справа (TextPanel).
 */
const BARS = 96;
/** Зум волны: ×1…×8. Пики считаются сразу с запасом (BARS × MAX_ZOOM), чтобы при приближении
    волна оставалась детальной, а не растягивала те же 96 столбиков. */
const MAX_ZOOM = 8;
const MIN_CUT = 0.5;

export function TrackStage({ creditsLeft, maxSegmentSeconds, paidPlan }: { creditsLeft: number | null; maxSegmentSeconds: number; paidPlan: boolean }) {
  const { t } = useTranslation();
  const { push } = useToast();
  const track = useWizardStore((state) => state.track);
  const setTrack = useWizardStore((state) => state.setTrack);
  const timingFrom = useWizardStore((state) => state.timingFrom);
  const timingTo = useWizardStore((state) => state.timingTo);
  const setField = useWizardStore((state) => state.setField);
  const projectId = useWizardStore((state) => state.projectId);
  const reset = useWizardStore((state) => state.reset);
  const tried = useTried(1);
  const fileInput = useRef<HTMLInputElement>(null);
  const cutRef = useRef<HTMLDivElement>(null);

  /* ── файл ── */
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  // Трек из черновика / прошлого батча: blob-ссылки нет, играем по свежей presigned-ссылке
  const playbackUrl = usePlaybackUrl(track);
  const audioUrl = blobUrl ?? playbackUrl;
  const waveSourceUrl = useWaveSourceUrl(track, blobUrl);
  const previousQuery = useQuery({ queryKey: ['wizard-previous-track'], queryFn: api.previousTrack, enabled: !track, staleTime: 30_000 });
  const upload = useMutation({
    mutationFn: api.uploadTrack,
    onSuccess: (data, file) => {
      setTrack(data.track);
      const url = URL.createObjectURL(file);
      setBlobUrl((prev) => { if (prev) URL.revokeObjectURL(prev); return url; });
      // Реальная длительность — из метаданных файла (mock возвращает заглушку)
      const probe = new Audio(url);
      probe.onloadedmetadata = () => { if (Number.isFinite(probe.duration)) setTrack({ ...data.track, durationS: probe.duration }); };
      setField('timingFrom', '');
      setField('timingTo', '');
      push({ variant: 'success', title: t('wizard.track.loaded'), text: data.track.filename });
    },
    // 402 — исчерпан лимит треков: причина и куда идти, а не общий «не загрузилось»
    onError: (error) => {
      const limitReached = error instanceof ApiError && error.status === 402;
      push({
        variant: 'error',
        title: limitReached ? t('wizard.track.limitReached') : t('wizard.track.loadFail'),
        text: limitReached ? String((error.detail as { detail?: string })?.detail ?? '') : undefined,
        action: limitReached ? { label: t('wizard.track.limitCta'), href: '/app/pricing' } : undefined
      });
    }
  });
  const takeFile = (file?: File | null) => {
    if (!file) return;
    if (!isAudioFile(file)) { push({ variant: 'error', title: t('wizard.track.audioOnly') }); return; }
    upload.mutate(file);
  };
  const [dragOver, setDragOver] = useState(false);

  /* ── звук: один плеер на трек и на отрывок ── */
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState<'track' | 'cut' | null>(null);
  const [head, setHead] = useState<number | null>(null);
  useEffect(() => {
    if (!audioUrl) return undefined;
    const audio = new Audio();
    audio.preload = 'auto';
    audio.src = audioUrl;
    audioRef.current = audio;
    setPlaying(null);
    return () => { audio.pause(); if (audioRef.current === audio) audioRef.current = null; };
  }, [audioUrl]);
  const stop = () => { audioRef.current?.pause(); setPlaying(null); setHead(null); };

  /* ── отрывок ── */
  const duration = track?.durationS ?? 0;
  const from = timingToSeconds(timingFrom);
  const to = timingToSeconds(timingTo);
  const selected = from !== null && to !== null;
  const length = selected ? to - from : 0;
  const backwards = selected && length <= 0;
  const over = selected && length > maxSegmentSeconds + 1e-6;
  const cutOk = selected && !backwards && !over;

  useEffect(() => {
    if (!playing) return undefined;
    let raf = 0;
    const tick = () => {
      const audio = audioRef.current;
      if (!audio) return;
      if (audio.ended || (playing === 'cut' && to !== null && audio.currentTime >= to)) { stop(); return; }
      setHead(audio.currentTime);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing]);
  const play = (what: 'track' | 'cut') => {
    const audio = audioRef.current;
    if (!audio) return;
    if (playing === what) { stop(); return; }
    const start = () => { audio.currentTime = what === 'cut' ? (from ?? 0) : 0; void audio.play(); };
    // Сик до загрузки метаданных браузер молча игнорирует — ждём их
    if (audio.readyState >= 1) start(); else audio.addEventListener('loadedmetadata', start, { once: true });
    setPlaying(what);
  };

  const setCut = (a: number, b: number) => {
    const nextFrom = toStoreTiming(a);
    const nextTo = toStoreTiming(b);
    const state = useWizardStore.getState();
    if (nextFrom === state.timingFrom && nextTo === state.timingTo) return;
    // Строки привязаны к окну: сдвиг их стирает, но «Вернуть» возвращает и текст, и окно
    if (state.lyrics.trim()) {
      useLyricsUndo.getState().save({ lyrics: state.lyrics, fragmentLyrics: state.fragmentLyrics, fragmentEnabled: state.fragmentEnabled, timingFrom: state.timingFrom, timingTo: state.timingTo });
      setField('lyrics', '');
      setField('fragmentLyrics', '');
      setField('fragmentEnabled', false);
    }
    setField('timingMode', 'manual');
    setField('timingFrom', nextFrom);
    setField('timingTo', nextTo);
  };

  /* перетаскивание по волне: клик ставит окно на лимит, края тянутся, окно двигается целиком */
  const waveRef = useRef<HTMLDivElement>(null);
  const drag = useRef<null | { kind: 'l' | 'r' } | { kind: 'move'; off: number; len: number }>(null);
  const timeAt = (clientX: number) => {
    const rect = waveRef.current?.getBoundingClientRect();
    if (!rect || !duration) return 0;
    return Math.max(0, Math.min(duration, ((clientX - rect.left) / rect.width) * duration));
  };
  const onWaveDown = (event: PointerEvent<HTMLDivElement>) => {
    if (!track || !duration || event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const at = timeAt(event.clientX);
    const handle = (event.target as HTMLElement).dataset.handle;
    if (handle === 'l' || handle === 'r') drag.current = { kind: handle };
    else if (selected && at >= from && at <= to) drag.current = { kind: 'move', off: at - from, len: to - from };
    else {
      const start = snapTenth(Math.max(0, Math.min(at, duration - maxSegmentSeconds)));
      const end = snapTenth(Math.min(duration, start + maxSegmentSeconds));
      setCut(start, end);
      drag.current = { kind: 'move', off: at - start, len: end - start };
    }
    if (playing === 'cut') stop();
  };
  const onWaveMove = (event: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || !event.currentTarget.hasPointerCapture(event.pointerId) || from === null || to === null) return;
    const at = snapTenth(timeAt(event.clientX));
    if (d.kind === 'l') setCut(Math.min(at, to - MIN_CUT), to);
    else if (d.kind === 'r') setCut(from, Math.max(at, from + MIN_CUT));
    else if (d.kind === 'move') {
      const start = snapTenth(Math.max(0, Math.min(duration - d.len, timeAt(event.clientX) - d.off)));
      setCut(start, snapTenth(start + d.len));
    }
  };
  const nudge = (edge: 'l' | 'r', event: KeyboardEvent<HTMLSpanElement>) => {
    const dir = ({ ArrowLeft: -1, ArrowRight: 1 } as Record<string, number>)[event.key];
    if (!dir || from === null || to === null) return;
    event.preventDefault();
    const step = (event.shiftKey ? 1 : 0.1) * dir;
    if (edge === 'l') setCut(snapTenth(Math.max(0, Math.min(from + step, to - MIN_CUT))), to);
    else setCut(from, snapTenth(Math.min(duration, Math.max(to + step, from + MIN_CUT))));
  };

  /* поля «Начало / Конец»: «0:40», «40», «0:40.5»; ошибку показывают рамкой, не стирают */
  const [draft, setDraft] = useState<{ from?: string; to?: string }>({});
  const [bad, setBad] = useState<{ from?: boolean; to?: boolean }>({});
  const commitField = (which: 'from' | 'to') => {
    const raw = draft[which];
    if (raw === undefined) return;
    const value = parseClock(raw);
    if (value === null || value > duration) { setBad((b) => ({ ...b, [which]: true })); return; }
    setBad((b) => ({ ...b, [which]: false }));
    setDraft((d) => ({ ...d, [which]: undefined }));
    if (which === 'from') setCut(snapTenth(value), to ?? snapTenth(Math.min(duration, value + maxSegmentSeconds)));
    else setCut(from ?? snapTenth(Math.max(0, value - maxSegmentSeconds)), snapTenth(value));
  };
  const field = (which: 'from' | 'to') => (
    <label className="w12-tf">
      {t(which === 'from' ? 'wizard.track.start' : 'wizard.track.end')}
      <input
        className={cn('w12-num', bad[which] && 'w12-bad')}
        value={draft[which] ?? (selected ? formatClock(which === 'from' ? from : to) : '')}
        onChange={(event) => { setDraft((d) => ({ ...d, [which]: event.target.value })); setBad((b) => ({ ...b, [which]: false })); }}
        onBlur={() => commitField(which)}
        onKeyDown={(event) => { if (event.key === 'Enter') (event.target as HTMLInputElement).blur(); }}
        inputMode="decimal"
        autoComplete="off"
        placeholder={t('wizard.track.clockPlaceholder')}
        aria-invalid={bad[which] || undefined}
      />
    </label>
  );

  const peaksHi = useWavePeaks(waveSourceUrl, BARS * MAX_ZOOM);
  const [zoom, setZoom] = useState(1);
  const barCount = BARS * zoom;
  // столбики текущего зума: максимум по группе пиков высокого разрешения
  const peaks = useMemo(() => {
    if (!peaksHi) return null;
    const group = peaksHi.length / barCount;
    return Array.from({ length: barCount }, (_, i) => {
      let max = 0;
      for (let k = Math.floor(i * group); k < Math.floor((i + 1) * group); k++) max = Math.max(max, peaksHi[k] ?? 0);
      return max;
    });
  }, [peaksHi, barCount]);
  // при смене зума окно отрывка держим в центре видимой части
  const scrollRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const box = scrollRef.current;
    if (!box || !duration) return;
    const focus = selected ? (from + to) / 2 : (head ?? 0);
    box.scrollLeft = Math.max(0, (focus / duration) * box.scrollWidth - box.clientWidth / 2);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoom]);
  const rulerTicks = 4 * zoom + 1;
  const cutMessage = backwards
    ? t('wizard.track.segmentBackwards')
    : over
      ? t('wizard.track.segmentOver', { seconds: formatSeconds(length), max: maxSegmentSeconds })
      : tried && track && !selected ? t('wizard.track.needCut') : null;
  const [guideDismissed, setGuideDismissed] = useGuideDismiss('track-timing', Boolean(track) && !cutOk, Boolean(track));
  const trackMeta = track ? `${formatClock(Math.round(track.durationS))} · ${(track.filename.split('.').pop() ?? 'mp3').toUpperCase()}` : '';

  return (
    <>
      {/* ── трек ── */}
      <div className="w12-sec">
        <div className="w12-sec-head">
          <h2><span className="w12-mi w12-cap w12-heavy w12-ttl-ic" aria-hidden="true" style={{ '--m': 'url(/assets/wizard/ic-note.svg)', '--r': 0.75 } as React.CSSProperties} /><span className="w12-l">{t('wizard.track.intro')}</span></h2>
          <div className="w12-side">
            <span>{creditsLeft === null ? t('wizard.track.availableUnlimited') : t('wizard.track.available', { count: creditsLeft })}</span>
            {track && (
              <button type="button" className="w12-ghost" onClick={() => { stop(); setBlobUrl(null); useLyricsUndo.getState().drop(); reset(projectId); }}>
                <Svg>{W12.reset}</Svg><span className="w12-l">{t('wizard.track.reset')}</span>
              </button>
            )}
          </div>
        </div>
        <input ref={fileInput} type="file" className="sr-only" accept={AUDIO_FILE_ACCEPT} tabIndex={-1} onChange={(event) => { takeFile(event.target.files?.[0]); event.target.value = ''; }} />
        {!track ? (
          <div className="w12-sec">
            <button
              type="button"
              className={cn('w12-drop', tried && 'w12-invalid', dragOver && 'w12-over')}
              disabled={upload.isPending}
              onClick={() => fileInput.current?.click()}
              onDragOver={(event) => { event.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(event) => { event.preventDefault(); setDragOver(false); takeFile(event.dataTransfer.files?.[0]); }}
            >
              <span className="w12-plus">{upload.isPending ? <span className="spinner" aria-hidden="true" /> : <Svg>{W12.plus}</Svg>}</span>
              <span><b>{upload.isPending ? t('wizard.track.uploading') : t('wizard.track.dropTitle')}</b><span>{t('wizard.track.dropFormats')}</span></span>
            </button>
            {previousQuery.data?.track && (
              <div className="w12-prev-row">
                <Svg style={{ color: 'var(--w12-text-3)' }}>{W12.note}</Svg>
                <span className="w12-txt">
                  <Trans
                    i18nKey="wizard.track.previousRow"
                    values={{ name: previousQuery.data.track.filename, duration: formatClock(Math.round(previousQuery.data.track.durationS)) }}
                    components={{ b: <b /> }}
                  />
                </span>
                <button
                  type="button"
                  className="w12-small-btn"
                  onClick={() => {
                    const previous = previousQuery.data.track;
                    if (!previous) return;
                    setTrack(previous);
                    setBlobUrl(previous.localUrl || null);
                  }}
                >
                  <span className="w12-l">{t('wizard.track.take')}</span>
                </button>
              </div>
            )}
          </div>
        ) : (
          <div className="w12-track-row">
            <button type="button" className="w12-round" onClick={() => play('track')} disabled={!audioUrl} aria-label={playing === 'track' ? t('wizard.track.pause') : t('wizard.track.listen')}>
              {playing === 'track' ? PAUSE : PLAY}
            </button>
            <span className="w12-name"><b>{track.filename.replace(/\.[^.]+$/, '')}</b><span className="w12-num">{trackMeta}</span></span>
            <button type="button" className="w12-ghost" onClick={() => fileInput.current?.click()}><span className="w12-l">{upload.isPending ? t('wizard.track.uploading') : t('wizard.track.replace')}</span></button>
          </div>
        )}
      </div>

      {/* ── отрывок: тянется до низа карточки шага, волна растёт вместе с ним ── */}
      <div className="w12-sec w12-fill">
        <div className="w12-sec-head">
          <h2><span className="w12-l">{t('wizard.track.segment')}</span></h2>
          <div className="w12-side">
            <span className={cn('w12-chip', over && 'w12-warn')}><span className="w12-l">{t('wizard.track.segmentCap', { seconds: maxSegmentSeconds })}</span></span>
            {!paidPlan && <a className="w12-link" href="/app/pricing">{t('wizard.track.segmentUpgrade', { seconds: SEGMENT_SECONDS.paid })}</a>}
          </div>
        </div>
        <div ref={cutRef} className={cn('w12-cut w12-fill', !track && 'w12-off', (backwards || over || (tried && track && !selected)) && 'w12-invalid')}>
          {/* волна в своей подложке по краям столбиков; при зуме растягивается и листается вбок */}
          <div className="w12-wave-box w12-wave-frame">
            <div ref={scrollRef} className="w12-wave-scroll">
            <div className="w12-wave-inner" style={{ width: `${zoom * 100}%` }}>
            <div ref={waveRef} className="w12-wave" onPointerDown={onWaveDown} onPointerMove={onWaveMove} onPointerUp={() => { drag.current = null; }}>
              <div className="w12-bars" aria-hidden="true">
                {Array.from({ length: barCount }, (_, i) => {
                  const at = ((i + 0.5) / barCount) * duration;
                  const inside = selected && at >= from && at <= to;
                  const height = peaks ? Math.max(0.12, peaks[i] ?? 0) : 0.3;
                  return <i key={i} className={cn(inside && 'w12-in')} style={{ height: `${Math.round(height * 100)}%` }} />;
                })}
              </div>
              {selected && duration > 0 && (
                <div className={cn('w12-win', (over || backwards) && 'w12-over')} style={{ left: `${(Math.max(0, from) / duration) * 100}%`, width: `${Math.max(0.5, (Math.max(0, length) / duration) * 100)}%` }}>
                  <span className="w12-win-label w12-num">{formatClock(from)} – {formatClock(to)} · {formatSeconds(length)} с</span>
                  {(['l', 'r'] as const).map((edge) => (
                    <span
                      key={edge}
                      data-handle={edge}
                      className={cn('w12-handle', edge === 'l' ? 'w12-l' : 'w12-r')}
                      role="slider"
                      tabIndex={0}
                      aria-label={t(edge === 'l' ? 'wizard.track.segStart' : 'wizard.track.segEnd')}
                      aria-valuetext={formatClock(edge === 'l' ? from : to)}
                      aria-valuemin={0}
                      aria-valuemax={Math.round(duration)}
                      aria-valuenow={Math.round(edge === 'l' ? from : to)}
                      onKeyDown={(event) => nudge(edge, event)}
                    />
                  ))}
                </div>
              )}
              {head !== null && duration > 0 && <span className="w12-playhead" style={{ left: `${(head / duration) * 100}%` }} />}
              {track && !selected && <div className="w12-wave-hint"><span>{t('wizard.track.waveHint')}</span></div>}
            </div>
            <div className="w12-ruler w12-num" aria-hidden="true">
              {Array.from({ length: rulerTicks }, (_, i) => i / (rulerTicks - 1)).map((k) => <span key={k}>{formatClock(Math.round((duration || 95) * k))}</span>)}
            </div>
            </div>
            </div>
          </div>
          <div className="w12-cut-row">
            <button type="button" className="w12-play-cut" onClick={() => play('cut')} disabled={!cutOk} style={cutOk ? undefined : { opacity: 0.5 }}>
              <span className="w12-dot">{playing === 'cut' ? PAUSE : PLAY}</span>
              <span className="w12-l">{playing === 'cut' ? t('wizard.track.pause') : t('wizard.track.playCut')}</span>
            </button>
            {field('from')}
            {field('to')}
            {/* зум вместо «12,0 с из 15 с»: длина и так видна на окне, а точный тайминг ловится приближением */}
            <div className="w12-zoom" role="group" aria-label={t('wizard.track.zoom')}>
              <button type="button" className="w12-zoom-btn" onClick={() => setZoom((z) => Math.max(1, z - 1))} disabled={!track || zoom <= 1} aria-label={t('wizard.track.zoomOut')}>−</button>
              <span className="w12-zoom-val w12-num" aria-live="polite">×{zoom}</span>
              <button type="button" className="w12-zoom-btn" onClick={() => setZoom((z) => Math.min(MAX_ZOOM, z + 1))} disabled={!track || zoom >= MAX_ZOOM} aria-label={t('wizard.track.zoomIn')}>+</button>
            </div>
          </div>
          {cutMessage && <p className="w12-cut-msg" role="alert">{cutMessage}</p>}
        </div>
      </div>

      <ActionGuideOverlay
        open={Boolean(track) && !guideDismissed}
        targetRef={cutRef}
        title={t('wizard.track.guideTitle')}
        text={t('wizard.track.guideText', { seconds: maxSegmentSeconds })}
        dismissLabel={t('wizard.track.guideDismiss')}
        progressLabel={t('wizard.guideProgress', { current: 1, total: 2 })}
        onDismiss={() => setGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<WaveGuideVisual />}
      />
    </>
  );
}

/** Подсказка: окно на волне, правый край тянется. */
function WaveGuideVisual() {
  const bars = [30, 44, 26, 58, 72, 50, 84, 66, 92, 70, 54, 80, 62, 40, 56, 34, 48, 28, 38, 24];
  return (
    <div className="relative flex h-[52px] w-full items-center gap-[3px]" aria-hidden="true">
      {/* ui-allow: иллюстрация гайда */}
      {bars.map((h, i) => <i key={i} className={cn('flex-1 rounded-[2px]', i >= 6 && i <= 12 ? 'bg-accent-light' : 'bg-text-20')} style={{ height: `${h}%` }} />)}
      {/* ui-allow: иллюстрация гайда */}
      <span className="guide-track-fill-move absolute inset-y-0 left-[29%] right-[36%] origin-left rounded-[8px] bg-accent-soft shadow-[inset_0_0_0_1.5px_var(--accent-light)]" />
      <span className="absolute left-[29%] top-1/2 h-[22px] w-[3px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-text" />
      <span className="guide-track-handle-right absolute right-[36%] top-1/2 h-[22px] w-[3px] translate-x-1/2 rounded-full bg-text" />
    </div>
  );
}
