import type React from 'react';
import { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { cn } from '../../lib/cn';
import { SvgMaskIcon } from '../layout/SvgMaskIcon';
import { AsrWord, useWizardStore } from '../../stores/wizardStore';
import { usePlaybackUrl } from './useFragmentAudio';

/*
 * Примерка субтитров (этап «Текст»): плеер отрывка + таймлайн слов из ASR.
 *
 * Слово — пилюля на шкале времени. Её можно перетащить (тайминг слова в будущих
 * субтитрах сдвинется), выбрать и пометить фокусной (Stage2 сделает её акцентом сцены).
 * Правки живут в сторе (`asr.words`) и уезжают в генерацию через stageData.asr.
 *
 * Ограничения переноса — те же, что проверяет оркестратор (`rebuild_stage1_asr_with_words`):
 * слово не выходит за окно отрывка и не наезжает на соседей. Валидируем здесь, чтобы
 * человек упирался в границу мышью, а не получал 422 на сабмите.
 */

const MIN_WORD_S = 0.08;
const GAP_S = 0.01;
/** в окне дорожки — не больше трёх секунд-зон между пунктиром; ниже этого не сжимаем */
const ZONES_VISIBLE = 3;
/** сетка по битам мельче секундной: в окне два такта, чтобы слова не растягивались на пол-экрана */
const BEATS_VISIBLE = 8;
/** минимальная ширина зоны между пунктиром — иначе подписи наезжают друг на друга */
const MIN_ZONE_PX = 150;
const MIN_PX_PER_SEC = 60;
/* Геометрия по Figma: контейнер 540×180; сверху 20 → слова 60 → 20 → ползунок 20 → 20 → тайминги.
   Пунктир секунд и плейхед идут от верха контейнера до ползунка. */
const BOX_H = 180;
const WORD_TOP = 20;
const WORD_H = 60;
const BAR_TOP = 100;
const BAR_H = 20;
const LABEL_TOP = 144; // 16px текста → низ на 160, до края контейнера ровно 20
/** пунктир секунд и полосы-подложки — от верха до НИЗА ползунка */
const GRID_H = BAR_TOP + BAR_H;
/** пунктир не упирается в края: чуть короче сверху и снизу */
const DASH_INSET = 8; // только снизу: сверху пунктир идёт от самого края
/** магнит к биту при переносе слова: в пределах этого окна старт прилипает к сетке */
const SNAP_S = 0.07;
const TOOLTIP_DELAY_MS = 350;
/** боковые поля контейнера (Figma: минимум 20 слева и справа) — и у дорожки, и у ползунка */
const X0 = 20;
const PLAYHEAD_TICK_MS = 50;

function fmt(sec: number): string {
  const s = Math.max(0, sec);
  const mm = Math.floor(s / 60);
  const ss = Math.floor(s % 60);
  const cc = Math.floor((s - Math.floor(s)) * 100);
  return `${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}:${String(cc).padStart(2, '0')}`;
}

/** Границы, в которых слово index может лежать, не задевая соседей и окно */
function bounds(words: AsrWord[], index: number, clipStart: number, clipEnd: number): { min: number; max: number } {
  const prev = words[index - 1];
  const next = words[index + 1];
  return {
    min: prev ? prev.tEnd + GAP_S : clipStart,
    max: next ? next.tStart - GAP_S : clipEnd
  };
}

type Drag = {
  index: number;
  mode: 'move' | 'start' | 'end';
  originX: number;
  tStart: number;
  tEnd: number;
  moved: boolean;
};

export function SubtitleTimeline() {
  const { t } = useTranslation();
  const track = useWizardStore((state) => state.track);
  const playbackUrl = usePlaybackUrl(track);
  const asr = useWizardStore((state) => state.asr);
  const setAsrWord = useWizardStore((state) => state.setAsrWord);
  const toggleAsrFocus = useWizardStore((state) => state.toggleAsrFocus);
  const resetAsrEdits = useWizardStore((state) => state.resetAsrEdits);

  const clipStart = asr.clipStart ?? 0;
  const clipEnd = asr.clipEnd ?? clipStart + 1;
  const duration = Math.max(0.5, clipEnd - clipStart);

  // ширина дорожки меряется по факту: три зоны на любую ширину колонки
  const [laneW, setLaneW] = useState(0);

  // --- сетка битов: bpm и якорь (лучший дроп приснапан к биту) — из того же /hook/analyze, что у FX
  const timingFrom = useWizardStore((state) => state.timingFrom);
  const timingTo = useWizardStore((state) => state.timingTo);
  const dropsQuery = useQuery({
    queryKey: ['drops', timingFrom, timingTo],
    queryFn: () => api.drops(timingFrom, timingTo),
    enabled: Boolean(timingFrom && timingTo),
    staleTime: 5 * 60_000
  });
  const beats = useMemo(() => {
    const bpm = dropsQuery.data?.bpm ?? 0;
    if (!bpm || bpm < 40) return [] as number[];
    const period = 60 / bpm;
    const anchor = dropsQuery.data?.drops.find((d) => d.best)?.seconds ?? dropsQuery.data?.drops[0]?.seconds ?? clipStart;
    const first = anchor - Math.ceil((anchor - clipStart) / period) * period;
    const out: number[] = [];
    for (let b = first; b <= clipEnd + 1e-6; b += period) if (b >= clipStart - 1e-6) out.push(Math.round(b * 1000) / 1000);
    return out;
  }, [dropsQuery.data, clipStart, clipEnd]);
  // Сетка таймлайна = биты (когда bpm известен): пунктир, полосы-подложки и подписи идут по
  // битам, а не по секундам. Без bpm — секунды. В окно помещается ZONES_VISIBLE зон сетки.
  const beatPeriod = dropsQuery.data?.bpm && dropsQuery.data.bpm >= 40 ? 60 / dropsQuery.data.bpm : 0;
  // общий зум: 1 = базовый масштаб (два такта в окне), меньше — обзор всех слов, больше — точная правка
  const [zoom, setZoom] = useState(1);
  const pxPerSec = Math.max(MIN_PX_PER_SEC, (zoom * (laneW - X0 * 2)) / (beats.length ? BEATS_VISIBLE * beatPeriod : ZONES_VISIBLE));
  /*
   * Шаг сетки — стабильная лестница, а не «что влезло»: с bpm это 1 → 2 → 4 (такт) → 8 битов,
   * без bpm — 0.5 → 1 → 2 → 5 с. Берём первый шаг, при котором между пунктиром не меньше
   * MIN_ZONE_PX: при уменьшении зума сетка редеет ступенями (бит → пара → такт), подписи не
   * накладываются, а сами линии остаются на тех же музыкальных долях.
   */
  const gridStep = useMemo(() => {
    const ladder = beats.length ? [1, 2, 4, 8, 16].map((n) => n * beatPeriod) : [0.5, 1, 2, 5, 10];
    return ladder.find((step) => step * pxPerSec >= MIN_ZONE_PX) ?? ladder[ladder.length - 1];
  }, [beats.length, beatPeriod, pxPerSec]);
  const gridUnit = gridStep;
  const snapToBeat = (sec: number) => {
    if (!beats.length) return sec;
    let best = sec;
    let dist = SNAP_S;
    for (const b of beats) {
      const d = Math.abs(b - sec);
      if (d < dist) { dist = d; best = b; }
    }
    return best;
  };

  // --- волна отрывка: декодируем файл один раз, пики считаем под текущий масштаб
  const waveRef = useRef<HTMLCanvasElement>(null);
  const [wave, setWave] = useState<{ url: string; data: Float32Array; rate: number } | null>(null);
  const waveUrl = playbackUrl;
  useEffect(() => {
    if (!waveUrl || asr.status !== 'COMPLETED') return;
    if (wave && wave.url === waveUrl) return;
    let cancelled = false;
    (async () => {
      try {
        const buf = await fetch(waveUrl).then((r) => r.arrayBuffer());
        const ctx = new AudioContext();
        const decoded = await ctx.decodeAudioData(buf);
        void ctx.close();
        const rate = decoded.sampleRate;
        const from = Math.max(0, Math.floor(clipStart * rate));
        const to = Math.min(decoded.length, Math.ceil(clipEnd * rate));
        const mono = new Float32Array(Math.max(0, to - from));
        for (let ch = 0; ch < decoded.numberOfChannels; ch += 1) {
          const src = decoded.getChannelData(ch);
          for (let i = from; i < to; i += 1) mono[i - from] += src[i] / decoded.numberOfChannels;
        }
        if (!cancelled) setWave({ url: waveUrl, data: mono, rate });
      } catch {
        /* волна — украшение: без неё таймлайн работает как раньше */
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waveUrl, asr.status, clipStart, clipEnd]);
  useEffect(() => {
    const canvas = waveRef.current;
    if (!canvas || !wave) return;
    const w = Math.ceil(duration * pxPerSec);
    const h = BAR_TOP;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    const g = canvas.getContext('2d');
    if (!g) return;
    g.scale(dpr, dpr);
    g.clearRect(0, 0, w, h);
    g.fillStyle = 'rgba(246,245,253,0.16)';
    const perPx = wave.data.length / w;
    const mid = h / 2;
    for (let x = 0; x < w; x += 1) {
      const a = Math.floor(x * perPx);
      const b = Math.min(wave.data.length, Math.floor((x + 1) * perPx));
      let peak = 0;
      for (let i = a; i < b; i += 1) { const v = Math.abs(wave.data[i]); if (v > peak) peak = v; }
      const hh = Math.max(1, peak * (h - 24) * 0.5);
      g.fillRect(x, mid - hh, 1, hh * 2);
    }
  }, [wave, duration, pxPerSec]);

  // --- кастомный тултип на слове (вместо нативного title): текст + точные тайминги
  const [hovered, setHovered] = useState<number | null>(null);
  const hoverTimer = useRef<number | null>(null);
  const hoverIn = (index: number) => {
    if (hoverTimer.current) window.clearTimeout(hoverTimer.current);
    hoverTimer.current = window.setTimeout(() => setHovered(index), TOOLTIP_DELAY_MS);
  };
  const hoverOut = () => {
    if (hoverTimer.current) window.clearTimeout(hoverTimer.current);
    hoverTimer.current = null;
    setHovered(null);
  };
  const [selected, setSelected] = useState<number | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  /** слово во время перетаскивания — локально, в стор коммитим на pointerup */
  const [live, setLive] = useState<{ index: number; tStart: number; tEnd: number } | null>(null);

  // --- плеер отрывка ---
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(clipStart);
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const measure = () => { const box = scrollRef.current; if (box) setLaneW(box.clientWidth); };
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [asr.status]);
  const url = playbackUrl;

  useEffect(() => {
    audioRef.current?.pause();
    audioRef.current = null;
    setPlaying(false);
    setTime(clipStart);
  }, [url, clipStart]);
  useEffect(() => () => { audioRef.current?.pause(); audioRef.current = null; }, []);

  const audio = () => {
    if (!url) return null;
    if (!audioRef.current) {
      audioRef.current = new Audio(url);
      audioRef.current.onended = () => setPlaying(false);
    }
    return audioRef.current;
  };

  const seek = (sec: number) => {
    const clamped = Math.min(clipEnd, Math.max(clipStart, sec));
    const el = audio();
    if (el) el.currentTime = clamped;
    setTime(clamped);
  };

  const toggle = () => {
    const el = audio();
    if (!el) return;
    if (playing) { el.pause(); setPlaying(false); return; }
    if (el.currentTime < clipStart || el.currentTime >= clipEnd - 0.05) el.currentTime = clipStart;
    void el.play();
    setPlaying(true);
  };

  // Плейхед: интервал, а не rAF/timeupdate — rAF в браузерном пане не тикает, timeupdate ~4 Гц
  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => {
      const el = audioRef.current;
      if (!el) return;
      if (el.currentTime >= clipEnd) { el.pause(); setPlaying(false); setTime(clipEnd); return; }
      setTime(el.currentTime);
      const box = scrollRef.current;
      if (box) {
        const x = X0 + (el.currentTime - clipStart) * pxPerSec;
        if (x < box.scrollLeft + 40 || x > box.scrollLeft + box.clientWidth - 40) box.scrollLeft = Math.max(0, x - box.clientWidth / 3);
      }
    }, PLAYHEAD_TICK_MS);
    return () => window.clearInterval(id);
  }, [playing, clipEnd, clipStart, pxPerSec]);

  // --- перетаскивание ---
  const onPillDown = (index: number, mode: Drag['mode']) => (e: ReactPointerEvent<HTMLElement>) => {
    e.stopPropagation();
    const word = asr.words[index];
    try {
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    } catch {
      /* синтетический pointerId */
    }
    setSelected(index);
    hoverOut();
    setDrag({ index, mode, originX: e.clientX, tStart: word.tStart, tEnd: word.tEnd, moved: false });
  };
  const onPillMove = (e: ReactPointerEvent<HTMLElement>) => {
    if (!drag) return;
    const dx = (e.clientX - drag.originX) / pxPerSec;
    if (!drag.moved && Math.abs(e.clientX - drag.originX) < 3) return;
    const { min, max } = bounds(asr.words, drag.index, clipStart, clipEnd);
    const len = drag.tEnd - drag.tStart;
    let tStart = drag.tStart;
    let tEnd = drag.tEnd;
    if (drag.mode === 'move') {
      const raw = drag.tStart + dx;
      tStart = Math.min(max - len, Math.max(min, e.altKey ? raw : snapToBeat(raw)));
      tEnd = tStart + len;
    } else if (drag.mode === 'start') {
      tStart = Math.min(drag.tEnd - MIN_WORD_S, Math.max(min, drag.tStart + dx));
    } else {
      tEnd = Math.max(drag.tStart + MIN_WORD_S, Math.min(max, drag.tEnd + dx));
    }
    setDrag({ ...drag, moved: true });
    setLive({ index: drag.index, tStart: Math.round(tStart * 1000) / 1000, tEnd: Math.round(tEnd * 1000) / 1000 });
  };
  const onPillUp = () => {
    if (drag && live && drag.moved) setAsrWord(live.index, { tStart: live.tStart, tEnd: live.tEnd });
    setDrag(null);
    setLive(null);
  };

  const nudge = (index: number, delta: number) => {
    const word = asr.words[index];
    const { min, max } = bounds(asr.words, index, clipStart, clipEnd);
    const len = word.tEnd - word.tStart;
    const tStart = Math.round(Math.min(max - len, Math.max(min, word.tStart + delta)) * 1000) / 1000;
    setAsrWord(index, { tStart, tEnd: Math.round((tStart + len) * 1000) / 1000 });
  };
  const onKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key === ' ') { e.preventDefault(); toggle(); return; }
    if (e.key === 'Escape') { setSelected(null); return; }
    if (selected === null) return;
    const step = e.shiftKey ? 0.2 : 0.05;
    if (e.key === 'ArrowLeft') { e.preventDefault(); nudge(selected, -step); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); nudge(selected, step); }
    else if (e.key === 'f' || e.key === 'F' || e.key === 'а' || e.key === 'А') { e.preventDefault(); toggleAsrFocus(selected); }
  };

  const ticks = useMemo(() => {
    if (beats.length) {
      // каждый k-й бит от якоря (первый бит в окне) — линии сетки всегда лежат на битах
      const every = Math.max(1, Math.round(gridStep / beatPeriod));
      return beats.filter((_, i) => i % every === 0);
    }
    const out: number[] = [];
    for (let s = Math.ceil(clipStart / gridStep) * gridStep; s <= clipEnd + 1e-6; s += gridStep) out.push(Math.round(s * 1000) / 1000);
    return out;
  }, [beats, beatPeriod, gridStep, clipStart, clipEnd]);

  // --- ползунок = прокрутка дорожки слов влево-вправо (не перемотка: плейхед он не трогает) ---
  const barRef = useRef<HTMLDivElement>(null);
  const [scroll, setScroll] = useState({ left: 0, visible: 1, total: 1 });
  const onLaneScroll = () => {
    const box = scrollRef.current;
    if (!box) return;
    setScroll({ left: box.scrollLeft, visible: box.clientWidth, total: Math.max(1, box.scrollWidth) });
  };
  useEffect(() => { onLaneScroll(); }, [asr.words.length, asr.status, pxPerSec]); // eslint-disable-line react-hooks/exhaustive-deps
  const thumbFrac = Math.min(1, scroll.visible / scroll.total);
  const thumbGrab = useRef<{ originX: number; scrollLeft: number } | null>(null);
  const capture = (e: ReactPointerEvent<HTMLElement>) => {
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* синтетический pointerId */ }
  };
  const onBarDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    e.stopPropagation();
    const box = scrollRef.current;
    const bar = barRef.current;
    if (!box || !bar) return;
    capture(e);
    const rect = bar.getBoundingClientRect();
    const thumbW = Math.max(60, rect.width * thumbFrac);
    const thumbX = rect.left + (scroll.left / Math.max(1, scroll.total - scroll.visible)) * (rect.width - thumbW);
    // клик мимо бегунка — прыгнуть туда, дальше тянем как обычный скроллбар
    if (e.clientX < thumbX || e.clientX > thumbX + thumbW) {
      const pct = Math.min(1, Math.max(0, (e.clientX - rect.left - thumbW / 2) / Math.max(1, rect.width - thumbW)));
      box.scrollLeft = pct * (scroll.total - scroll.visible);
      onLaneScroll();
    }
    thumbGrab.current = { originX: e.clientX, scrollLeft: box.scrollLeft };
  };
  const onBarMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const box = scrollRef.current;
    const bar = barRef.current;
    const grab = thumbGrab.current;
    if (!box || !bar || !grab || !e.buttons) return;
    const rect = bar.getBoundingClientRect();
    const thumbW = Math.max(60, rect.width * thumbFrac);
    const perPx = (scroll.total - scroll.visible) / Math.max(1, rect.width - thumbW);
    box.scrollLeft = grab.scrollLeft + (e.clientX - grab.originX) * perPx;
    onLaneScroll();
  };
  const onBarUp = () => { thumbGrab.current = null; };
  const seekFromLane = (clientX: number) => {
    const box = scrollRef.current;
    if (!box) return;
    const rect = box.getBoundingClientRect();
    seek(clipStart + (clientX - rect.left + box.scrollLeft - X0) / pxPerSec);
  };
  const onHeadDown = (e: ReactPointerEvent<HTMLElement>) => { e.stopPropagation(); capture(e); seekFromLane(e.clientX); };
  const onHeadMove = (e: ReactPointerEvent<HTMLElement>) => { if (e.buttons) seekFromLane(e.clientX); };

  const onWheel = (e: React.WheelEvent<HTMLDivElement>) => {
    const box = scrollRef.current;
    if (!box || Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
    box.scrollLeft += e.deltaY;
  };

  const activeIndex = asr.words.findIndex((w) => time >= w.tStart && time < w.tEnd);
  const width = Math.ceil(duration * pxPerSec) + X0 * 2;
  const ready = asr.status === 'COMPLETED' && asr.words.length > 0;

  const focusOn = selected !== null && Boolean(asr.words[selected]?.focus);
  const progress = Math.min(1, Math.max(0, (time - clipStart) / duration));

  return (
    <section className="mt-[24px] rounded-r15 bg-grad-soft-10 px-[28px] py-[24px]" aria-label={t('wizard.subs.timeline.title')}>
      <div className="flex items-center justify-between gap-space-3">
        <div className="flex items-center gap-space-3">
          <span className="wizard-body">{t('wizard.subs.timeline.title')}</span>
          {asr.status === 'FAILED' && <span className="text-[14px] text-[var(--error)]">{t('wizard.subs.timeline.failed')}</span>}
          {(asr.status === 'RUNNING' || asr.status === 'QUEUED') && <span className="text-[14px] text-text-60">{t('wizard.subs.timeline.running')}</span>}
        </div>
        {/* Две круглые кнопки в правом верхнем углу: «?» — как работать (по ховеру), «✕» — сбросить правки */}
        <div className="flex items-center gap-space-2">
          <span className="group relative">
            <button
              type="button"
              aria-label={t('wizard.subs.timeline.help')}
              className="flex h-[36px] w-[36px] items-center justify-center rounded-full bg-grad-soft-20 pt-[2px] text-[18px] leading-none text-text-80 transition duration-200 ease-[cubic-bezier(0.32,0.72,0,1)] hover:text-text hover:shadow-[inset_0_0_0_1px_var(--border-hover)] active:scale-[0.98]"
            >
              ?
            </button>
            <span
              role="tooltip"
              className="pointer-events-none absolute right-0 top-[calc(100%+8px)] z-[5] w-[320px] rounded-r12 bg-[#2b2145] px-space-4 py-space-3 text-[14px] leading-[1.35] text-text opacity-0 shadow-[0_8px_28px_rgba(0,0,0,.45)] ring-1 ring-[var(--accent-light)] transition-opacity duration-200 group-hover:opacity-100 group-focus-within:opacity-100"
            >
              {t('wizard.subs.timeline.hint')}
            </span>
          </span>
          <button
            type="button"
            onClick={resetAsrEdits}
            disabled={!asr.edited}
            aria-label={t('wizard.subs.timeline.reset')}
            title={t('wizard.subs.timeline.reset')}
            className="flex h-[36px] w-[36px] items-center justify-center rounded-full bg-grad-soft-20 pt-[2px] text-[16px] leading-none text-text-80 transition duration-200 ease-[cubic-bezier(0.32,0.72,0,1)] hover:text-text hover:shadow-[inset_0_0_0_1px_var(--border-hover)] active:scale-[0.98] disabled:opacity-35"
          >
            ✕
          </button>
        </div>
      </div>

      {/* Плеер (макет): квадратный play, табло времени, «Фокус» — одна высота 80 */}
      <div className="mt-[20px] flex flex-wrap items-center gap-space-4">
        <button
          type="button"
          onClick={toggle}
          disabled={!url || !ready}
          aria-label={playing ? t('wizard.subs.timeline.pause') : t('wizard.subs.timeline.play')}
          className="flex h-[64px] w-[64px] shrink-0 items-center justify-center rounded-r15 bg-grad-soft-20 text-text-80 transition duration-200 ease-[cubic-bezier(0.32,0.72,0,1)] hover:text-text hover:shadow-[inset_0_0_0_1px_var(--border-hover)] active:scale-[0.98] disabled:opacity-40"
        >
          {playing ? (
            <span className="flex gap-[6px]" aria-hidden><span className="h-[20px] w-[6px] rounded-[2px] bg-text" /><span className="h-[20px] w-[6px] rounded-[2px] bg-text" /></span>
          ) : (
            <span aria-hidden className="ml-[5px] h-0 w-0 border-y-[11px] border-l-[18px] border-y-transparent border-l-[var(--text)]" />
          )}
        </button>
        <span className="flex h-[64px] items-center rounded-r15 bg-grad-soft-20 px-[18px] text-[24px] font-[350] tabular-nums leading-none text-text-80">
          {fmt(time - clipStart)}
        </span>
        <button
          type="button"
          disabled={selected === null}
          onClick={() => { if (selected !== null) toggleAsrFocus(selected); }}
          aria-pressed={focusOn}
          className="group flex h-[64px] items-center gap-[12px] rounded-r15 bg-grad-soft-20 pl-[12px] pr-[20px] text-[24px] font-[350] leading-none text-text-80 transition duration-200 ease-[cubic-bezier(0.32,0.72,0,1)] hover:text-text hover:shadow-[inset_0_0_0_1px_var(--border-hover)] active:scale-[0.98] disabled:opacity-40"
        >
          {focusOn ? (
            <span aria-hidden className="flex h-[40px] w-[40px] shrink-0 items-center justify-center rounded-full bg-text transition duration-200 ease-[cubic-bezier(0.32,0.72,0,1)] group-hover:scale-105">
              <SvgMaskIcon src="/assets/figma/pd-star.svg" style={{ width: 19, height: 18, color: 'var(--accent)' }} />
            </span>
          ) : (
            <img src="/assets/figma/obj-zvezda5.svg" width="40" height="40" alt="" aria-hidden="true" className="h-[40px] w-[40px] shrink-0 transition duration-200 ease-[cubic-bezier(0.32,0.72,0,1)] group-hover:scale-105" />
          )}
          {focusOn ? t('wizard.subs.timeline.unfocus') : t('wizard.subs.timeline.focus')}
        </button>
        {/* зум дорожки: та же пилюля-контейнер, внутри бегунок; крайние значения — обзор / точная правка */}
        <label className="flex h-[64px] min-w-[180px] flex-1 items-center gap-[12px] rounded-r15 bg-grad-soft-20 px-[18px] text-[24px] font-[350] leading-none text-text-80 max-lg:min-w-0">
          <span aria-hidden className="select-none pt-[3px]">−</span>
          <input
            type="range"
            min={0.5}
            max={2.5}
            step={0.05}
            value={zoom}
            onChange={(e) => setZoom(Number(e.target.value))}
            aria-label={t('wizard.subs.timeline.zoom')}
            className="zoom-range min-w-0 flex-1"
            disabled={!ready}
          />
          <span aria-hidden className="select-none pt-[3px]">+</span>
        </label>
      </div>

      {/* Таймлайн (Figma 540×180): один контейнер дефолтного цвета, внутри — дорожка слов,
          ползунок на всю ширину и подписи секунд. Секунды-пунктир и плейхед — от верха до ползунка. */}
      <div className="relative mt-[20px] overflow-hidden rounded-r15 bg-grad-soft-20 ring-1 ring-[rgba(246,245,253,0.06)]" style={{ height: BOX_H }}>
        <div
          ref={scrollRef}
          id="subtitle-timeline-lane"
          tabIndex={0}
          onKeyDown={onKey}
          className="no-scrollbar relative h-full overflow-x-auto overflow-y-hidden outline-none focus-visible:ring-1 focus-visible:ring-accent-light"
          onScroll={onLaneScroll}
          onWheel={onWheel}
          onPointerDown={(e) => {
            // клик по пустому месту дорожки — перемотка
            seekFromLane(e.clientX);
            setSelected(null);
          }}
        >
          {!ready ? (
            <div className="flex h-full items-center justify-center px-space-5 text-center text-[15px] text-text-60">
              {asr.status === 'FAILED' ? (asr.error || t('wizard.subs.timeline.failed'))
                : asr.status === 'IDLE' ? t('wizard.subs.timeline.idleHint')
                  : <span className="flex items-center gap-space-3"><span className="spinner !h-[22px] !w-[22px] !border-2 !border-accent-20 !border-t-accent-light" />{t('wizard.subs.timeline.runningHint')}</span>}
            </div>
          ) : (
            <div className="relative h-full" style={{ width }}>
              {/* подложка: каждая ВТОРАЯ секунда (между пунктиром 1–2, 3–4, …) чуть светлее фона —
                  так таймлайн читается и через одно окно */}
              {ticks.filter((_, i) => i % 2 === 0).map((s) => (
                <span key={`b${s}`} aria-hidden className="pointer-events-none absolute top-0 bg-[rgba(246,245,253,0.05)]" style={{ left: X0 + (s - clipStart) * pxPerSec, width: Math.min(gridUnit * pxPerSec, (clipEnd - s) * pxPerSec), height: GRID_H }} />
              ))}
              {/* пунктир секунд: от верха до низа ползунка */}
              {ticks.map((s) => (
                <span key={s} aria-hidden className="pointer-events-none absolute top-0 border-l-[3px] border-dashed border-[rgba(246,245,253,0.28)]" style={{ left: X0 + (s - clipStart) * pxPerSec - 1.5, height: GRID_H - DASH_INSET }} />
              ))}
              {/* волна отрывка — фоном под словами */}
              <canvas ref={waveRef} aria-hidden className="pointer-events-none absolute top-0" style={{ left: X0, width: Math.ceil(duration * pxPerSec), height: BAR_TOP }} />
              {/* слова: не выделено / выделено (обводка) / фокусное (белое) */}
              {asr.words.map((word, index) => {
                const cur = live && live.index === index ? live : word;
                const left = X0 + (cur.tStart - clipStart) * pxPerSec;
                const w = Math.max(14, (cur.tEnd - cur.tStart) * pxPerSec);
                const isSel = selected === index;
                const isActive = activeIndex === index;
                return (
                  <div
                    key={index}
                    role="button"
                    tabIndex={-1}
                    onPointerEnter={() => hoverIn(index)}
                    onPointerLeave={hoverOut}
                    onPointerDown={onPillDown(index, 'move')}
                    onPointerMove={onPillMove}
                    onPointerUp={onPillUp}
                    onPointerCancel={onPillUp}
                    onDoubleClick={(e) => { e.stopPropagation(); toggleAsrFocus(index); }}
                    className={cn(
                      'absolute flex cursor-grab select-none items-center justify-center rounded-r12 px-[16px] text-[24px] font-[350] leading-none transition-[box-shadow,background-color,color] duration-200 ease-[cubic-bezier(0.32,0.72,0,1)] active:cursor-grabbing',
                      // три состояния (макет): обычное / выделенное / фокусное — все НЕПРОЗРАЧНЫЕ
                      word.focus
                        ? 'bg-text text-accent'
                        : isActive
                          ? 'bg-accent text-text'
                          : 'bg-[var(--tl-pill)] text-text-80 shadow-[inset_0_1px_1px_rgba(255,255,255,0.10)]',
                      isSel && !word.focus && '!text-text shadow-[inset_0_0_0_2px_var(--accent-light)]',
                      isSel && word.focus && 'shadow-[0_0_0_2px_var(--accent-light)]',
                      isSel && 'z-[2]',
                      hovered === index && 'z-[7]'
                    )}
                    style={{ left, width: w, top: WORD_TOP, height: WORD_H }}
                  >
                    {w >= 44 && <span className="truncate">{word.text}</span>}
                    {hovered === index && !drag && (
                      <span role="tooltip" className="pointer-events-none absolute left-1/2 top-[calc(100%+6px)] z-[6] -translate-x-1/2 whitespace-nowrap rounded-r9 bg-[#2b2145] px-[10px] py-[6px] text-[13px] font-[350] leading-none tabular-nums text-text shadow-[0_8px_28px_rgba(0,0,0,.45)] ring-1 ring-[var(--accent-light)]">
                        {fmt(cur.tStart - clipStart)} – {fmt(cur.tEnd - clipStart)}
                      </span>
                    )}
                    {/* ручки длительности — тянут только край */}
                    <span onPointerDown={onPillDown(index, 'start')} className="absolute inset-y-0 left-0 w-[8px] cursor-ew-resize hover:bg-[rgba(246,245,253,0.18)]" />
                    <span onPointerDown={onPillDown(index, 'end')} className="absolute inset-y-0 right-0 w-[8px] cursor-ew-resize hover:bg-[rgba(246,245,253,0.18)]" />
                  </div>
                );
              })}
              {/* подписи секунд */}
              {ticks.map((s) => (
                <span key={`l${s}`} aria-hidden className={cn('pointer-events-none absolute text-[16px] leading-none tabular-nums text-text-60', X0 + (s - clipStart) * pxPerSec >= 40 && '-translate-x-1/2')} style={{ left: X0 + (s - clipStart) * pxPerSec, top: LABEL_TOP }}>
                  {gridStep < 1 || beats.length ? fmt(s - clipStart) : fmt(s - clipStart).slice(0, 5)}
                </span>
              ))}
              {/* плейхед: линия с ромбиками, от верха до ползунка; тянется */}
              <span
                role="presentation"
                onPointerDown={onHeadDown}
                onPointerMove={onHeadMove}
                className="absolute left-0 top-0 z-[3] w-[14px] cursor-ew-resize will-change-transform"
                style={{ transform: `translateX(${X0 + progress * duration * pxPerSec - 5}px)`, height: BAR_TOP + BAR_H / 2 }}
              >
                <span aria-hidden className="absolute inset-y-0 left-1/2 w-[2px] -translate-x-1/2 bg-text" />
                <span aria-hidden className="absolute left-1/2 top-0 h-[12px] w-[12px] -translate-x-1/2 -translate-y-1/2 rotate-45 rounded-[2px] bg-text" />
              </span>
            </div>
          )}
        </div>
        {/* ползунок: крутилка дорожки влево-вправо (кастомный скроллбар), непрозрачный */}
        <div
          ref={barRef}
          role="scrollbar"
          aria-controls="subtitle-timeline-lane"
          aria-orientation="horizontal"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round((scroll.left / Math.max(1, scroll.total - scroll.visible)) * 100) || 0}
          onPointerDown={ready ? onBarDown : undefined}
          onPointerMove={ready ? onBarMove : undefined}
          onPointerUp={onBarUp}
          onPointerCancel={onBarUp}
          className={cn('absolute z-[4] select-none rounded-full bg-[#4b3892]', ready ? 'cursor-pointer' : 'opacity-40')}
          style={{ top: BAR_TOP, height: BAR_H, left: X0, right: X0 }}
        >
          <span
            aria-hidden
            className="absolute top-0 h-full rounded-full bg-text"
            style={{
              width: `max(60px, ${thumbFrac * 100}%)`,
              left: `calc(${scroll.left / Math.max(1, scroll.total - scroll.visible)} * (100% - max(60px, ${thumbFrac * 100}%)))`
            }}
          />
        </div>
      </div>
    </section>
  );
}
