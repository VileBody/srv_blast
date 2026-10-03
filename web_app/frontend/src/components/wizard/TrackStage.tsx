import { KeyboardEvent, PointerEvent, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../../lib/api';
import { cn } from '../../lib/cn';
import { AUDIO_FILE_ACCEPT, isAudioFile } from '../../lib/mediaFiles';
import { useToast } from '../../contexts/ToastContext';
import { useWizardStore } from '../../stores/wizardStore';
import { ActionBar, Button, Dialog } from '../ui/kit';
import { ActionGuideOverlay } from '../guidance/ActionGuideOverlay';
import { useGuideDismiss } from '../guidance/useGuideDismiss';
import { formatClock, formatSeconds, parseClock, snapTenth, SEGMENT_SECONDS, toStoreTiming } from './timing';
import { formatTimeCoarse, formatTimePrecise, formatTimeRange } from '../../lib/timeFormat';
import { timingToSeconds, usePlaybackUrl } from './useFragmentAudio';
import { peakLevels, useTrackPeaks } from './trackPeaks';
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
  const queryClient = useQueryClient();
  const tried = useTried(1);
  const fileInput = useRef<HTMLInputElement>(null);
  const cutRef = useRef<HTMLDivElement>(null);

  /* ── файл ── */
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  // Трек из черновика / прошлого батча: blob-ссылки нет, играем лёгкую копию со своего домена
  const playbackUrl = usePlaybackUrl(track);
  const audioUrl = blobUrl ?? playbackUrl;
  // волна: только что загруженный файл уже в памяти — считаем из него; сохранённый трек —
  // по громкости с сервера, без скачивания файла
  const localPeaksUrl = blobUrl ?? (track?.localUrl?.startsWith('blob:') ? track.localUrl : null);
  const serverPeaks = useTrackPeaks(localPeaksUrl ? null : track);
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
  /*
   * «Сбросить» стирает и серверный черновик: иначе после перезагрузки restoreSession
   * подтянул бы старый трек и выбор обратно (локально пусто — значит, берётся сервер).
   * Сначала пишем пустой черновик, и только после удачной записи чистим локально —
   * при сбое ничего не теряем и говорим об этом.
   */
  const [confirmReset, setConfirmReset] = useState(false);
  const resetDraft = useMutation({
    mutationFn: () => api.saveWizardSession({ projectId, stage: 1, data: {} }),
    onSuccess: (data) => {
      queryClient.setQueryData(['wizard-session'], (old: object | undefined) => ({ ...(old ?? {}), session: data.session }));
      stop();
      setBlobUrl(null);
      useLyricsUndo.getState().drop();
      reset(projectId);
      setConfirmReset(false);
    },
    onError: () => push({ variant: 'error', title: t('wizard.track.resetFail'), text: t('wizard.page.saveFailText') })
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
  // контейнер прокрутки приближенной волны: его ведёт и зум, и автопрокрутка при перетаскивании
  const scrollRef = useRef<HTMLDivElement>(null);
  const drag = useRef<null | { kind: 'l' | 'r' } | { kind: 'move'; off: number; len: number }>(null);
  const timeAt = (clientX: number) => {
    const rect = waveRef.current?.getBoundingClientRect();
    if (!rect || !duration) return 0;
    return Math.max(0, Math.min(duration, ((clientX - rect.left) / rect.width) * duration));
  };
  /** окно на лимит тарифа с началом в точке клика (у конца трека — прижато к концу) */
  const placeWindowAt = (at: number) => {
    const start = snapTenth(Math.max(0, Math.min(at, duration - maxSegmentSeconds)));
    const end = snapTenth(Math.min(duration, start + maxSegmentSeconds));
    setCut(start, end);
    return { start, end };
  };
  /** видимая часть волны: края в пикселях экрана и в секундах трека */
  const viewport = () => {
    const box = scrollRef.current?.getBoundingClientRect();
    const wave = waveRef.current?.getBoundingClientRect();
    if (!box || !wave || !duration || wave.width <= 0) return null;
    const toT = (x: number) => Math.max(0, Math.min(duration, ((x - wave.left) / wave.width) * duration));
    return { left: box.left, right: box.right, t0: toT(box.left), t1: toT(box.right), toX: (sec: number) => wave.left + (sec / duration) * wave.width };
  };
  /*
   * Окно при перетаскивании не уходит за видимую часть приближенной волны: оно упирается в её
   * край, а волна сама подкручивается под него (автопрокрутка ниже). Раньше окно уезжало за край,
   * прокрутка стояла на месте, и в кадре оставалась пустая серая волна.
   * Возвращает новое окно и «нажим» на край: знак — куда крутить, модуль — насколько сильно.
   */
  const AUTO_ZONE_PX = 24;
  const dragStartX = useRef(0);
  const dragPlan = (clientX: number) => {
    const d = drag.current;
    const state = useWizardStore.getState();
    const curFrom = timingToSeconds(state.timingFrom);
    const curTo = timingToSeconds(state.timingTo);
    if (!d || curFrom === null || curTo === null || !duration) return null;
    const view = viewport();
    const t0 = view?.t0 ?? 0;
    const t1 = view?.t1 ?? duration;
    // нажим считаем, только когда указатель уже сдвинулся к этому краю: схватил ручку у края
    // и повёл внутрь — волна не должна сорваться в обратную сторону
    const moved = clientX - dragStartX.current;
    const pushAt = (x: number) => {
      if (!view) return 0;
      if (moved < -2 && x < view.left + AUTO_ZONE_PX) return -Math.min(3, (view.left + AUTO_ZONE_PX - x) / AUTO_ZONE_PX);
      if (moved > 2 && x > view.right - AUTO_ZONE_PX) return Math.min(3, (x - view.right + AUTO_ZONE_PX) / AUTO_ZONE_PX);
      return 0;
    };
    const raw = timeAt(clientX);
    // Границы кадра «не дальше, чем сейчас»: если окно уже частично за краем (волну
    // пролистали вручную), его не дёргает в кадр при захвате — просто дальше наружу не пускаем.
    if (d.kind !== 'move') {
      const edgeNow = d.kind === 'l' ? curFrom : curTo;
      const at = snapTenth(Math.max(Math.min(t0, edgeNow), Math.min(Math.max(t1, edgeNow), raw)));
      const push = pushAt(clientX);
      return d.kind === 'l'
        ? { from: Math.min(at, curTo - MIN_CUT), to: curTo, push }
        : { from: curFrom, to: Math.max(at, curFrom + MIN_CUT), push };
    }
    const wanted = raw - d.off;
    let start = wanted;
    let push = pushAt(clientX);
    // окно короче видимой части — держим его целиком в кадре и давим краем окна, а не указателем
    if (view && d.len <= t1 - t0) {
      start = Math.max(Math.min(t0, curFrom), Math.min(Math.max(t1 - d.len, curFrom), wanted));
      const lead = pushAt(view.toX(wanted));
      const tail = pushAt(view.toX(wanted + d.len));
      push = Math.abs(lead) >= Math.abs(tail) ? lead : tail;
    }
    start = snapTenth(Math.max(0, Math.min(duration - d.len, start)));
    return { from: start, to: snapTenth(start + d.len), push };
  };
  const applyDrag = (clientX: number) => {
    const plan = dragPlan(clientX);
    if (plan) setCut(plan.from, plan.to);
    return plan;
  };
  /*
   * Автопрокрутка: пока окно (или ручка) давит в край видимой части, волна едет в ту же
   * сторону, а окно — вместе с ней. Крутится, только пока идёт перетаскивание: ручную прокрутку
   * в остальное время не трогаем. Скорость — от силы нажима, в пикселях за миллисекунду.
   */
  const AUTO_SPEED = 0.9;
  const auto = useRef<{ raf: number; x: number; ts: number } | null>(null);
  const dragPlanRef = useRef(dragPlan);
  dragPlanRef.current = dragPlan;
  const applyDragRef = useRef(applyDrag);
  applyDragRef.current = applyDrag;
  const stopAutoScroll = () => {
    if (auto.current) cancelAnimationFrame(auto.current.raf);
    auto.current = null;
  };
  const autoTick = (ts: number) => {
    const state = auto.current;
    const box = scrollRef.current;
    if (!state || !drag.current || !box) { stopAutoScroll(); return; }
    const dt = Math.min(48, Math.max(0, ts - state.ts));
    state.ts = ts;
    // окно двигает только нажим на край: стоящий указатель без нажима ничего не меняет
    const push = dragPlanRef.current(state.x)?.push ?? 0;
    if (push) {
      const before = box.scrollLeft;
      box.scrollLeft = before + push * AUTO_SPEED * dt;
      // волна сдвинулась под неподвижным указателем — окно догоняет её в том же кадре
      if (box.scrollLeft !== before) applyDragRef.current(state.x);
    }
    state.raf = requestAnimationFrame(autoTick);
  };
  const startAutoScroll = (clientX: number) => {
    stopAutoScroll();
    auto.current = { raf: 0, x: clientX, ts: performance.now() };
    auto.current.raf = requestAnimationFrame(autoTick);
  };
  useEffect(() => stopAutoScroll, []);
  /** докрутить волну так, чтобы отрезок [a; b] был в кадре (не влезает — его начало) */
  const reveal = (a: number, b: number, smooth: boolean) => {
    const box = scrollRef.current;
    const wave = waveRef.current;
    if (!box || !wave || !duration || box.scrollWidth <= box.clientWidth + 1) return;
    const boxRect = box.getBoundingClientRect();
    const waveRect = wave.getBoundingClientRect();
    const toContent = (sec: number) => waveRect.left - boxRect.left + box.scrollLeft + (sec / duration) * waveRect.width;
    const margin = Math.min(32, box.clientWidth * 0.1);
    const xa = toContent(Math.min(a, b)) - margin;
    const xb = toContent(Math.max(a, b)) + margin;
    let left = box.scrollLeft;
    if (xb - xa > box.clientWidth || xa < left) left = xa;
    else if (xb > left + box.clientWidth) left = xb - box.clientWidth;
    left = Math.max(0, Math.min(box.scrollWidth - box.clientWidth, left));
    if (Math.abs(left - box.scrollLeft) < 1) return;
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    box.scrollTo({ left, behavior: smooth && !reduce ? 'smooth' : 'auto' });
  };
  /** после отпускания, тапа, стрелок и ввода в поля: окно (или сдвинутый край) остаётся в кадре */
  const revealCut = (kind: 'l' | 'r' | 'move', smooth: boolean) => {
    const state = useWizardStore.getState();
    const a = timingToSeconds(state.timingFrom);
    const b = timingToSeconds(state.timingTo);
    if (a === null || b === null) return;
    if (kind === 'l') reveal(a, a, smooth);
    else if (kind === 'r') reveal(b, b, smooth);
    else reveal(a, b, smooth);
  };
  /*
   * Палец на приближенной волне: протяжка листает волну (её скроллит сам браузер —
   * touch-action: pan-x, см. data-pan), а окно ставит только тап без сдвига. Раньше любое
   * касание сразу ставило новое окно и стирало текст отрывка — пролистать волну было нельзя.
   * Ручки окна тянутся и пальцем (у них touch-action: none) — с той же автопрокруткой.
   */
  const tap = useRef<null | { pointerId: number; x: number; y: number }>(null);
  const TAP_SLOP_PX = 8;
  const onWaveDown = (event: PointerEvent<HTMLDivElement>) => {
    if (!track || !duration || event.button !== 0) return;
    const handle = (event.target as HTMLElement).dataset.handle;
    if (event.pointerType === 'touch' && zoom > 1 && handle !== 'l' && handle !== 'r') {
      tap.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    const at = timeAt(event.clientX);
    if (handle === 'l' || handle === 'r') drag.current = { kind: handle };
    else if (selected && at >= from && at <= to) drag.current = { kind: 'move', off: at - from, len: to - from };
    else {
      const { start, end } = placeWindowAt(at);
      drag.current = { kind: 'move', off: at - start, len: end - start };
    }
    dragStartX.current = event.clientX;
    startAutoScroll(event.clientX);
    if (playing === 'cut') stop();
  };
  const endDrag = () => {
    const d = drag.current;
    drag.current = null;
    stopAutoScroll();
    if (d) revealCut(d.kind, true);
  };
  const onWaveUp = (event: PointerEvent<HTMLDivElement>) => {
    endDrag();
    const pending = tap.current;
    tap.current = null;
    if (!pending || pending.pointerId !== event.pointerId) return;
    // тап по окну ничего не меняет (как клик мышью без протяжки), мимо окна — ставит новое
    const at = timeAt(event.clientX);
    if (selected && at >= from && at <= to) return;
    placeWindowAt(at);
    revealCut('move', true);
    if (playing === 'cut') stop();
  };
  const onWaveCancel = () => { endDrag(); tap.current = null; };
  const onWaveMove = (event: PointerEvent<HTMLDivElement>) => {
    const pending = tap.current;
    if (pending && pending.pointerId === event.pointerId && Math.hypot(event.clientX - pending.x, event.clientY - pending.y) > TAP_SLOP_PX) tap.current = null;
    if (!drag.current || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
    if (auto.current) auto.current.x = event.clientX;
    applyDrag(event.clientX);
  };
  const nudge = (edge: 'l' | 'r', event: KeyboardEvent<HTMLSpanElement>) => {
    const dir = ({ ArrowLeft: -1, ArrowRight: 1 } as Record<string, number>)[event.key];
    if (!dir || from === null || to === null) return;
    event.preventDefault();
    const step = (event.shiftKey ? 1 : 0.1) * dir;
    if (edge === 'l') setCut(snapTenth(Math.max(0, Math.min(from + step, to - MIN_CUT))), to);
    else setCut(from, snapTenth(Math.min(duration, Math.max(to + step, from + MIN_CUT))));
    // край, который двигают стрелками, не уходит за видимую часть приближенной волны
    revealCut(edge, false);
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
    // окно, заданное с клавиатуры, докручиваем в кадр приближенной волны
    revealCut('move', true);
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

  const localPeaks = useWavePeaks(localPeaksUrl, BARS * MAX_ZOOM);
  const peaksHi = useMemo(
    () => (localPeaksUrl ? localPeaks : serverPeaks ? peakLevels(serverPeaks, 0, serverPeaks.duration, BARS * MAX_ZOOM) : null),
    [localPeaksUrl, localPeaks, serverPeaks]
  );
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
  const trackMeta = track ? `${formatTimeCoarse(track.durationS)} · ${(track.filename.split('.').pop() ?? 'mp3').toUpperCase()}` : '';

  return (
    <>
      <Dialog
        open={confirmReset}
        title={t('wizard.track.resetTitle')}
        onClose={() => { if (!resetDraft.isPending) setConfirmReset(false); }}
        footer={(
          <ActionBar>
            <Button variant="ghost" disabled={resetDraft.isPending} onClick={() => setConfirmReset(false)}>{t('wizard.track.resetCancel')}</Button>
            <Button variant="primary" loading={resetDraft.isPending} disabled={resetDraft.isPending} onClick={() => resetDraft.mutate()}>{t('wizard.track.resetApply')}</Button>
          </ActionBar>
        )}
      >
        <p className="text-ui-16 text-text-80">{t('wizard.track.resetText')}</p>
      </Dialog>
      {/* ── трек ── */}
      <div className="w12-sec">
        <div className="w12-sec-head">
          <h2><span className="w12-mi w12-cap w12-heavy w12-ttl-ic" aria-hidden="true" style={{ '--m': 'url(/assets/wizard/ic-note.svg)', '--r': 0.75 } as React.CSSProperties} /><span className="w12-l">{t('wizard.track.intro')}</span></h2>
          <div className="w12-side">
            <span>{creditsLeft === null ? t('wizard.track.availableUnlimited') : t('wizard.track.available', { count: creditsLeft })}</span>
            {track && (
              <button type="button" className="w12-ghost" onClick={() => setConfirmReset(true)}>
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
                    values={{ name: previousQuery.data.track.filename, duration: formatTimeCoarse(previousQuery.data.track.durationS) }}
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
                    // это сохранённый трек, а не файл в памяти: играем лёгкую копию со своего домена
                    // и берём пики с сервера (по id). Presigned-оригинал в blobUrl качал весь файл,
                    // а волна упиралась в CORS бакета и рисовалась ровной полосой.
                    setBlobUrl(null);
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
            {/* пока грузится новый трек, второй выбор файла только запутал бы, какой из них победит */}
            <button type="button" className="w12-ghost" disabled={upload.isPending} onClick={() => fileInput.current?.click()}><span className="w12-l">{upload.isPending ? t('wizard.track.uploading') : t('wizard.track.replace')}</span></button>
          </div>
        )}
      </div>

      {/* ── отрывок: тянется до низа карточки шага, волна растёт вместе с ним ── */}
      <div className="w12-sec w12-fill">
        <div className="w12-sec-head">
          <h2><span className="w12-l">{t('wizard.track.segment')}</span></h2>
          <div className="w12-side">
            <span className={cn('w12-chip', over && 'w12-warn')}><span className="w12-l">{t('wizard.track.segmentCap', { seconds: maxSegmentSeconds })}</span></span>
            {!paidPlan && <Link className="w12-link" to="/app/pricing">{t('wizard.track.segmentUpgrade', { seconds: SEGMENT_SECONDS.paid })}</Link>}
          </div>
        </div>
        <div ref={cutRef} className={cn('w12-cut w12-fill', !track && 'w12-off', (backwards || over || (tried && track && !selected)) && 'w12-invalid')}>
          {/* волна в своей подложке по краям столбиков; при зуме растягивается и листается вбок */}
          <div className="w12-wave-box w12-wave-frame">
            <div ref={scrollRef} className="w12-wave-scroll">
            <div className="w12-wave-inner" style={{ width: `${zoom * 100}%` }}>
            <div ref={waveRef} className="w12-wave" data-pan={zoom > 1 || undefined} onPointerDown={onWaveDown} onPointerMove={onWaveMove} onPointerUp={onWaveUp} onPointerCancel={onWaveCancel}>
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
                  <span className="w12-win-label w12-num">{formatTimeRange(from, to)} · {t('wizard.track.secondsValue', { value: formatSeconds(length) })}</span>
                  {(['l', 'r'] as const).map((edge) => (
                    <span
                      key={edge}
                      data-handle={edge}
                      className={cn('w12-handle', edge === 'l' ? 'w12-l' : 'w12-r')}
                      role="slider"
                      tabIndex={0}
                      aria-label={t(edge === 'l' ? 'wizard.track.segStart' : 'wizard.track.segEnd')}
                      aria-valuetext={formatTimePrecise(edge === 'l' ? from : to)}
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
              {Array.from({ length: rulerTicks }, (_, i) => i / (rulerTicks - 1)).map((k) => <span key={k}>{formatTimeCoarse((duration || 95) * k)}</span>)}
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
