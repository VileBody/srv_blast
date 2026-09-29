import { KeyboardEvent, PointerEvent, useEffect, useRef, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api, ApiError } from '../../lib/api';
import { cn } from '../../lib/cn';
import { AUDIO_FILE_ACCEPT, isAudioFile } from '../../lib/mediaFiles';
import { useToast } from '../../contexts/ToastContext';
import { useWizardStore } from '../../stores/wizardStore';
import { ActionGuideOverlay } from '../guidance/ActionGuideOverlay';
import { useGuideDismiss } from '../guidance/useGuideDismiss';
import { Button, DropZone, Icon, Surface, Tag } from '../ui/kit';
import { formatClock, formatSeconds, parseClock, snapTenth, SEGMENT_SECONDS, toStoreTiming } from './timing';
import { timingToSeconds, usePlaybackUrl } from './useFragmentAudio';
import { useWavePeaks } from './useWavePeaks';
import { useLyricsUndo, useTried } from './wizardAttempt';

/*
 * Шаг «Трек» (концепт «Трек / Фон», артефакт wizard12 v3).
 *
 * Отрывок выбирается на волне трека: клик ставит окно на лимит тарифа, края тянутся, окно
 * двигается целиком, стрелки двигают края на 0,1 с (с Shift на 1 с). Поля «Начало / Конец»
 * остались для точного ввода — в формате плеера «0:40» или «0:40.5».
 * Длина и лимит видны прямо на окне и рядом с полями; перебор подсвечивается, введённое не
 * стирается. Сдвиг окна очищает текст отрывка, но с «Вернуть» в правой колонке (TextPanel).
 */
const BARS = 96;
const MIN_CUT = 0.5;
const PLAY = <path d="M7.5 5v14l11.5-7z" className="fill-current" />;
const PAUSE = <path d="M8 5.5v13M16 5.5v13" />;

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
  const replaceInput = useRef<HTMLInputElement>(null);
  const cutRef = useRef<HTMLDivElement>(null);

  /* ── файл ── */
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  // Трек из черновика / прошлого батча: blob-ссылки нет, играем по свежей presigned-ссылке
  const playbackUrl = usePlaybackUrl(track);
  const audioUrl = blobUrl ?? playbackUrl;
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
  useEffect(() => {
    if (!playing) return undefined;
    let raf = 0;
    const tick = () => {
      const audio = audioRef.current;
      if (!audio) return;
      const end = playing === 'cut' ? to : null;
      if (audio.ended || (end !== null && audio.currentTime >= end)) { stop(); return; }
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

  /* ── отрывок ── */
  const duration = track?.durationS ?? 0;
  const from = timingToSeconds(timingFrom);
  const to = timingToSeconds(timingTo);
  const selected = from !== null && to !== null;
  const length = selected ? to - from : 0;
  const backwards = selected && length <= 0;
  const over = selected && length > maxSegmentSeconds + 1e-6;
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
      // клик мимо окна ставит новое окно на лимит тарифа
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

  /* поля «Начало / Конец»: принимают «0:40», «40», «0:40.5»; ошибку показывают рамкой, не стирают */
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
    <label className="inline-flex items-center gap-[8px] text-ui-14 text-text-60">
      {t(which === 'from' ? 'wizard.track.start' : 'wizard.track.end')}
      <input
        value={draft[which] ?? (selected ? formatClock(which === 'from' ? from : to) : '')}
        onChange={(event) => { setDraft((d) => ({ ...d, [which]: event.target.value })); setBad((b) => ({ ...b, [which]: false })); }}
        onBlur={() => commitField(which)}
        onKeyDown={(event) => { if (event.key === 'Enter') (event.target as HTMLInputElement).blur(); }}
        inputMode="decimal"
        autoComplete="off"
        placeholder={t('wizard.track.clockPlaceholder')}
        aria-invalid={bad[which] || undefined}
        className={cn(
          'h-ctl w-[84px] rounded-r10 border bg-field text-center text-ui-16 tabular-nums text-text outline-none transition-[border-color] duration-150 placeholder:text-text-40 focus:border-accent-line',
          bad[which] ? 'border-warning' : 'border-transparent'
        )}
      />
    </label>
  );

  const peaks = useWavePeaks(audioUrl, BARS);
  const cutMessage = backwards
    ? t('wizard.track.segmentBackwards')
    : over
      ? t('wizard.track.segmentOver', { seconds: formatSeconds(length), max: maxSegmentSeconds })
      : tried && track && !selected ? t('wizard.track.needCut') : null;
  const cutInvalid = backwards || over || Boolean(tried && track && !selected);

  const [guideDismissed, setGuideDismissed] = useGuideDismiss('track-timing', Boolean(track) && (!selected || backwards || over), Boolean(track));
  const trackMeta = track ? `${formatClock(Math.round(track.durationS))} · ${(track.filename.split('.').pop() ?? 'mp3').toUpperCase()}` : '';

  return (
    <div className="flex flex-col gap-[22px]">
      {/* ── трек ── */}
      <section className="flex flex-col gap-[10px]">
        <div className="flex min-h-[28px] flex-wrap items-center justify-between gap-[12px]">
          <h2 className="flex items-center gap-[0.45em] text-ui-20 font-[400] text-text">
            <Icon src="/assets/figma/icon-note.svg" ratio={0.72} heavy tone="muted" />
            {t('wizard.track.intro')}
          </h2>
          <div className="flex items-center gap-[12px] text-ui-14 text-text-60">
            <span>{creditsLeft === null ? t('wizard.track.availableUnlimited') : t('wizard.track.available', { count: creditsLeft })}</span>
            {track && (
              <Button variant="ghost" size="sm" icon={<Icon><path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3M4.5 4.5v4h4" /></Icon>} onClick={() => { stop(); setBlobUrl(null); useLyricsUndo.getState().drop(); reset(projectId); }}>
                {t('wizard.track.reset')}
              </Button>
            )}
          </div>
        </div>
        {!track ? (
          <>
            <DropZone
              title={upload.isPending ? t('wizard.track.uploading') : t('wizard.track.dropTitle')}
              hint={t('wizard.track.dropFormats')}
              accept={AUDIO_FILE_ACCEPT}
              busy={upload.isPending}
              invalid={tried}
              onFiles={(files) => takeFile(files[0])}
            />
            {previousQuery.data?.track && (
              <Surface className="flex items-center gap-[12px] py-[6px] pl-[16px] pr-[6px]">
                <Icon src="/assets/figma/icon-note.svg" ratio={0.72} heavy tone="muted" />
                <span className="min-w-0 flex-1 truncate text-ui-14 text-text-60">
                  <Trans
                    i18nKey="wizard.track.previousRow"
                    values={{ name: previousQuery.data.track.filename, duration: formatClock(Math.round(previousQuery.data.track.durationS)) }}
                    components={{ b: <span className="text-text" /> }}
                  />
                </span>
                <Button
                  size="sm"
                  onClick={() => {
                    const previous = previousQuery.data.track;
                    if (!previous) return;
                    setTrack(previous);
                    setBlobUrl(previous.localUrl || null);
                  }}
                >
                  {t('wizard.track.take')}
                </Button>
              </Surface>
            )}
          </>
        ) : (
          <Surface className="flex items-center gap-[14px] p-[12px]">
            <button
              type="button"
              onClick={() => play('track')}
              disabled={!audioUrl}
              aria-label={playing === 'track' ? t('wizard.track.pause') : t('wizard.track.listen')}
              className="grid h-ctl w-ctl shrink-0 place-items-center rounded-full bg-text text-ui-20 text-bg transition-transform duration-150 active:scale-95 disabled:opacity-40"
            >
              <Icon>{playing === 'track' ? PAUSE : PLAY}</Icon>
            </button>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-ui-16 text-text">{track.filename.replace(/\.[^.]+$/, '')}</span>
              <span className="text-ui-14 tabular-nums text-text-40">{trackMeta}</span>
            </span>
            <input ref={replaceInput} type="file" className="sr-only" accept={AUDIO_FILE_ACCEPT} tabIndex={-1} onChange={(event) => { takeFile(event.target.files?.[0]); event.target.value = ''; }} />
            <Button variant="ghost" size="sm" loading={upload.isPending} onClick={() => replaceInput.current?.click()}>{t('wizard.track.replace')}</Button>
          </Surface>
        )}
      </section>

      {/* ── отрывок ── */}
      <section className="flex flex-col gap-[10px]">
        <div className="flex min-h-[28px] flex-wrap items-center justify-between gap-[12px]">
          <h2 className="text-ui-20 font-[400] text-text">{t('wizard.track.segment')}</h2>
          <div className="flex items-center gap-[12px] text-ui-14">
            <Tag tone={over ? 'warn' : 'default'}>{t('wizard.track.segmentCap', { seconds: maxSegmentSeconds })}</Tag>
            {!paidPlan && (
              <a href="/app/pricing" className="text-text-60 underline decoration-line-strong underline-offset-[3px] transition-colors hover:text-text">
                {t('wizard.track.segmentUpgrade', { seconds: SEGMENT_SECONDS.paid })}
              </a>
            )}
          </div>
        </div>
        <Surface ref={cutRef} className={cn('flex flex-col gap-[12px] px-[16px] pb-[14px] pt-[16px] transition-[border-color,opacity] duration-150', !track && 'pointer-events-none opacity-45', cutInvalid && 'border-warning')}>
          <div>
            <div
              ref={waveRef}
              onPointerDown={onWaveDown}
              onPointerMove={onWaveMove}
              onPointerUp={() => { drag.current = null; }}
              className="relative mt-[26px] h-[120px] cursor-crosshair touch-none select-none rounded-r10"
            >
              <div className="absolute inset-0 flex items-center gap-[2px]" aria-hidden="true">
                {Array.from({ length: BARS }, (_, i) => {
                  const at = ((i + 0.5) / BARS) * duration;
                  const inside = selected && at >= from && at <= to;
                  const height = peaks ? Math.max(0.1, peaks[i] ?? 0) : 0.3;
                  return <i key={i} className={cn('flex-1 rounded-[2px] transition-colors duration-100', inside ? (over || backwards ? 'bg-warning' : 'bg-accent-light') : 'bg-text-20')} style={{ height: `${Math.round(height * 100)}%` }} />; // ui-allow: столбики волны
                })}
              </div>
              {selected && duration > 0 && (
                <div
                  className={cn('absolute -bottom-[6px] -top-[6px] cursor-grab rounded-r10 active:cursor-grabbing', over || backwards ? 'bg-warning-bg shadow-[inset_0_0_0_1.5px_var(--warning)]' : 'bg-accent-soft shadow-[inset_0_0_0_1.5px_var(--accent-light)]')}
                  style={{ left: `${(Math.max(0, from) / duration) * 100}%`, width: `${Math.max(0.5, (Math.max(0, length) / duration) * 100)}%` }}
                >
                  <span className={cn('pointer-events-none absolute -top-[30px] left-1/2 -translate-x-1/2 whitespace-nowrap rounded-r6 px-[8px] py-[2px] text-ui-12 tabular-nums text-text', over || backwards ? 'bg-warning-bg text-warning' : 'bg-accent-strong')}>
                    {formatClock(from)} – {formatClock(to)} · {formatSeconds(length)} с
                  </span>
                  {(['l', 'r'] as const).map((edge) => (
                    <span
                      key={edge}
                      data-handle={edge}
                      role="slider"
                      tabIndex={0}
                      aria-label={t(edge === 'l' ? 'wizard.track.segStart' : 'wizard.track.segEnd')}
                      aria-valuetext={formatClock(edge === 'l' ? from : to)}
                      aria-valuemin={0}
                      aria-valuemax={Math.round(duration)}
                      aria-valuenow={Math.round(edge === 'l' ? from : to)}
                      onKeyDown={(event) => nudge(edge, event)}
                      className={cn('absolute inset-y-0 grid w-[18px] cursor-ew-resize place-items-center focus-visible:outline-none', edge === 'l' ? '-left-[9px]' : '-right-[9px]')}
                    >
                      {/* ui-allow: ручка края — пиксельная геометрия */}
                      <i data-handle={edge} className="h-[30px] w-[4px] rounded-[3px] bg-text shadow-[0_1px_4px_rgba(5,1,15,.6)]" />
                    </span>
                  ))}
                </div>
              )}
              {head !== null && duration > 0 && <span className="pointer-events-none absolute bottom-[4px] top-[4px] w-[2px] rounded-full bg-text" style={{ left: `${(head / duration) * 100}%` }} />}
              {track && !selected && (
                <div className="pointer-events-none absolute inset-0 grid place-items-center">
                  <span className="rounded-r10 bg-scrim px-[12px] py-[6px] text-ui-14 text-text-80 backdrop-blur-[6px]">{t('wizard.track.waveHint')}</span>
                </div>
              )}
            </div>
            <div className="mt-[12px] flex justify-between text-ui-12 tabular-nums text-text-40" aria-hidden="true">
              {[0, 0.25, 0.5, 0.75, 1].map((k) => <span key={k}>{formatClock(Math.round((duration || 0) * k))}</span>)}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-[12px]">
            <Button onClick={() => play('cut')} disabled={!selected || backwards} icon={<Icon>{playing === 'cut' ? PAUSE : PLAY}</Icon>}>
              {playing === 'cut' ? t('wizard.track.pause') : t('wizard.track.playCut')}
            </Button>
            {field('from')}
            {field('to')}
            <span className="ml-auto text-ui-14 text-text-60 max-md:ml-0 max-md:w-full">
              <Trans
                i18nKey="wizard.track.segmentOf"
                values={{ seconds: selected ? formatSeconds(Math.max(0, length)) : '0', max: maxSegmentSeconds }}
                components={{ b: <span className={cn('tabular-nums', over || backwards ? 'text-warning' : 'text-text')} /> }}
              />
            </span>
          </div>
          {cutMessage && <p role="alert" className="text-ui-14 text-warning">{cutMessage}</p>}
        </Surface>
      </section>

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
    </div>
  );
}

/** Подсказка: окно на волне, правый край тянется. */
function WaveGuideVisual() {
  const bars = [30, 44, 26, 58, 72, 50, 84, 66, 92, 70, 54, 80, 62, 40, 56, 34, 48, 28, 38, 24];
  return (
    <div className="relative flex h-[52px] w-full items-center gap-[3px]" aria-hidden="true">
      {/* ui-allow: иллюстрация гайда */}
      {bars.map((h, i) => <i key={i} className={cn('flex-1 rounded-[2px]', i >= 6 && i <= 12 ? 'bg-accent-light' : 'bg-text-20')} style={{ height: `${h}%` }} />)}
      {/* окно: левый край на месте, правый тянется — заливка идёт вместе с ним */}
      {/* ui-allow: иллюстрация гайда */}
      <span className="guide-track-fill-move absolute inset-y-0 left-[29%] right-[36%] origin-left rounded-[8px] bg-accent-soft shadow-[inset_0_0_0_1.5px_var(--accent-light)]" />
      <span className="absolute left-[29%] top-1/2 h-[22px] w-[3px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-text" />
      <span className="guide-track-handle-right absolute right-[36%] top-1/2 h-[22px] w-[3px] translate-x-1/2 rounded-full bg-text" />
    </div>
  );
}

