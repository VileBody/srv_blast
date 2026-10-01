import type React from 'react';
import { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { cn } from '../../lib/cn';
import { cssZoom } from '../../lib/zoom';
import { PAUSE, PLAY, Svg, W12 } from './WizardFrame';
import { AsrWord, useWizardStore } from '../../stores/wizardStore';
import { usePlaybackUrl, useWaveSourceUrl } from './useFragmentAudio';
import { useSubtitleClock } from '../../lib/subtitleClock';

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
/* на телефоне дорожка ~300px: если вписывать столько же зон, слова ужимаются до «з.»;
   держим плотность выше и даём дорожке скроллиться пальцем */
const MIN_PX_PER_SEC_NARROW = 120;
const NARROW_LANE_PX = 480;
/* Геометрия дорожки в шкале макета wizard12 (как «Отрывок» на шаге «Трек»): сверху 14 → слова 40 →
   16 → ползунок 8 → 12 → подписи 11px. Пунктир сетки и плейхед идут от верха до ползунка. */
const BOX_H = 116;
const WORD_TOP = 14;
const WORD_H = 40;
const BAR_TOP = 70;
const BAR_H = 8;
const LABEL_TOP = 90;
/** пунктир сетки и полосы-подложки — от верха до НИЗА ползунка */
const GRID_H = BAR_TOP + BAR_H;
/** пунктир не упирается в края: чуть короче сверху и снизу */
const DASH_INSET = 4; // только снизу: сверху пунктир идёт от самого края
/** магнит к биту при переносе слова: в пределах этого окна старт прилипает к сетке */
const SNAP_S = 0.07;
const TOOLTIP_DELAY_MS = 350;
/** боковые поля дорожки — и у слов, и у ползунка */
const X0 = 14;
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

function TimelineNote({ eyebrow, text }: { eyebrow: string; text: string }) {
  return (
    <div className="w12-stl-note">
      <span className="w12-stl-note-dot" aria-hidden />
      <span><b>{eyebrow}</b>{text}</span>
    </div>
  );
}

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
  const pxPerSec = Math.max(laneW < NARROW_LANE_PX ? MIN_PX_PER_SEC_NARROW : MIN_PX_PER_SEC, (zoom * (laneW - X0 * 2)) / (beats.length ? BEATS_VISIBLE * beatPeriod : ZONES_VISIBLE));
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
  /** плеер уже может играть — только тогда качаем файл второй раз ради волны */
  const [audioReady, setAudioReady] = useState(false);
  const waveRef = useRef<HTMLCanvasElement>(null);
  const [wave, setWave] = useState<{ url: string; data: Float32Array; rate: number } | null>(null);
  const waveUrl = useWaveSourceUrl(track);
  useEffect(() => {
    if (!waveUrl || asr.status !== 'COMPLETED' || !audioReady) return;
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
  }, [waveUrl, asr.status, audioReady, clipStart, clipEnd]);
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

  // Плеер создаётся заранее и сразу подгружает файл (preload=auto), а не в момент клика:
  // иначе «плей» ждал метаданных и первого сика по сети — на проде это выливалось в 10–15 с.
  useEffect(() => {
    setPlaying(false);
    setTime(clipStart);
    setAudioReady(false);
    if (!url) { audioRef.current = null; return; }
    const el = new Audio();
    el.preload = 'auto';
    el.src = url;
    el.onended = () => setPlaying(false);
    el.oncanplay = () => setAudioReady(true);
    audioRef.current = el;
    return () => { el.pause(); if (audioRef.current === el) audioRef.current = null; };
  }, [url, clipStart]);

  /** сик с учётом того, что до метаданных браузер его молча игнорирует */
  const seekEl = (el: HTMLAudioElement, sec: number, then?: () => void) => {
    const run = () => { el.currentTime = sec; then?.(); };
    if (el.readyState >= 1) run();
    else el.addEventListener('loadedmetadata', run, { once: true });
  };

  const seek = (sec: number) => {
    const clamped = Math.min(clipEnd, Math.max(clipStart, sec));
    const el = audioRef.current;
    if (el) seekEl(el, clamped);
    setTime(clamped);
  };

  const toggle = () => {
    const el = audioRef.current;
    if (!el) return;
    if (playing) { el.pause(); setPlaying(false); return; }
    // доиграли до конца отрывка — плей начинает заново с его начала, а не с той же точки конца
    const atEnd = time >= clipEnd - 0.05 || el.currentTime >= clipEnd - 0.05 || el.ended;
    const from = atEnd ? clipStart : Math.max(clipStart, time);
    const restart = atEnd || el.readyState < 1 || el.currentTime < clipStart;
    if (restart) { setTime(from); seekEl(el, from, () => { void el.play(); }); }
    else void el.play();
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

  // Время плеера — в общие часы: превью субтитров справа рисует кадр по нему, а его кнопка
  // плея управляет этим же плеером (звук один). Пока плеер не трогали — null: превью
  // показывает первую фразу целиком, а не пустой кадр до первого слова.
  const publishClock = useSubtitleClock((state) => state.publish);
  const toggleRef = useRef(toggle);
  toggleRef.current = toggle;
  const touched = useRef(false);
  if (playing || time !== clipStart) touched.current = true;
  useEffect(() => { publishClock({ time: touched.current ? time : null, playing }); }, [publishClock, time, playing]);
  useEffect(() => {
    touched.current = false;
    publishClock({ toggle: () => toggleRef.current(), time: null, playing: false });
    return () => publishClock({ toggle: null, time: null, playing: false });
  }, [publishClock, url, clipStart]);

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
    // сдвиг мыши — визуальные пиксели, pxPerSec — пиксели дорожки (визард под zoom)
    const dx = (e.clientX - drag.originX) / cssZoom(scrollRef.current) / pxPerSec;
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
    seek(clipStart + ((clientX - rect.left) / cssZoom(box) + box.scrollLeft - X0) / pxPerSec);
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
  const weakWords = asr.words.filter((w) => w.weak).map((w) => w.text);
  const progress = Math.min(1, Math.max(0, (time - clipStart) / duration));

  return (
    <section className="w12-sec" aria-label={t('wizard.subs.timeline.title')}>
      <div className="w12-sec-head">
        <h2>
          <span className="w12-l">{t('wizard.subs.timeline.title')}</span>
          {/* «?» — как работать с дорожкой (по ховеру и фокусу), сразу у заголовка */}
          <span className="w12-stl-help">
            <button type="button" className="w12-help-dot" aria-label={t('wizard.subs.timeline.help')}><span className="w12-l">?</span></button>
            <span role="tooltip" className="w12-stl-tip-box">{t('wizard.subs.timeline.hint')}</span>
          </span>
        </h2>
        <div className="w12-side">
          {asr.status === 'FAILED' && <span className="w12-stl-failed">{t('wizard.subs.timeline.failed')}</span>}
          <button type="button" className="w12-ghost" onClick={resetAsrEdits} disabled={!asr.edited}>
            <Svg>{W12.reset}</Svg><span className="w12-l">{t('wizard.subs.timeline.reset')}</span>
          </button>
        </div>
      </div>

      <div className="w12-cut">
        {/* Диагностика примерки — то, что раньше всплывало только ошибкой рендера */}
        {ready && (weakWords.length > 0 || asr.notes.includes('window_clamped')) && (
          <div className="w12-stl-notes">
            {asr.notes.includes('window_clamped') && asr.workingEnd !== null && (
              <TimelineNote eyebrow={t('wizard.subs.timeline.noteWindow')} text={t('wizard.subs.timeline.windowClamped', { at: fmt(asr.workingEnd - clipStart) })} />
            )}
            {weakWords.length > 0 && (
              <TimelineNote eyebrow={t('wizard.subs.timeline.noteWords')} text={t('wizard.subs.timeline.weakWords', { words: weakWords.map((w) => `«${w}»`).join(', ') })} />
            )}
          </div>
        )}

        {/* Таймлайн: дорожка слов, ползунок на всю ширину и подписи сетки */}
        <div className="w12-stl-box" style={{ height: BOX_H }}>
          <div
            ref={scrollRef}
            id="subtitle-timeline-lane"
            tabIndex={0}
            onKeyDown={onKey}
            className="w12-stl-lane"
            onScroll={onLaneScroll}
            onWheel={onWheel}
            onPointerDown={(e) => {
              // клик по пустому месту дорожки — перемотка
              seekFromLane(e.clientX);
              setSelected(null);
            }}
          >
            {!ready ? (
              <div className="w12-stl-empty">
                {asr.status === 'FAILED' ? (asr.error || t('wizard.subs.timeline.failed'))
                  : asr.status === 'IDLE' ? t('wizard.subs.timeline.idleHint')
                    : <><span className="spinner" aria-hidden="true" />{t('wizard.subs.timeline.runningHint')}</>}
              </div>
            ) : (
              <div className="w12-stl-canvas" style={{ width }}>
                {/* подложка: каждая ВТОРАЯ зона сетки чуть светлее — дорожка читается и через одно окно */}
                {ticks.filter((_, i) => i % 2 === 0).map((s) => (
                  <span key={`b${s}`} aria-hidden className="w12-stl-zone" style={{ left: X0 + (s - clipStart) * pxPerSec, width: Math.min(gridUnit * pxPerSec, (clipEnd - s) * pxPerSec), height: GRID_H }} />
                ))}
                {/* пунктир сетки: от верха до низа ползунка */}
                {ticks.map((s) => (
                  <span key={s} aria-hidden className="w12-stl-tick" style={{ left: X0 + (s - clipStart) * pxPerSec, height: GRID_H - DASH_INSET }} />
                ))}
                {/* волна отрывка — фоном под словами */}
                <canvas ref={waveRef} aria-hidden className="w12-stl-wave" style={{ left: X0, width: Math.ceil(duration * pxPerSec), height: BAR_TOP }} />
                {/* слова: обычное / играет сейчас / выделенное (обводка) / фокусное (белое) / слабо легло */}
                {asr.words.map((word, index) => {
                  const cur = live && live.index === index ? live : word;
                  const left = X0 + (cur.tStart - clipStart) * pxPerSec;
                  const w = Math.max(14, (cur.tEnd - cur.tStart) * pxPerSec);
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
                        'w12-stl-word',
                        word.focus && 'w12-focus',
                        !word.focus && activeIndex === index && 'w12-on',
                        word.weak && !word.focus && 'w12-weak',
                        selected === index && 'w12-sel',
                        hovered === index && 'w12-hover'
                      )}
                      style={{ left, width: w, top: WORD_TOP, height: WORD_H }}
                    >
                      {w >= 32 && <span className="w12-l">{word.text}</span>}
                      {hovered === index && !drag && (
                        <span role="tooltip" className="w12-stl-tip w12-num">{fmt(cur.tStart - clipStart)} – {fmt(cur.tEnd - clipStart)}</span>
                      )}
                      {/* ручки длительности — тянут только край */}
                      <span onPointerDown={onPillDown(index, 'start')} className="w12-stl-edge w12-l-edge" />
                      <span onPointerDown={onPillDown(index, 'end')} className="w12-stl-edge w12-r-edge" />
                    </div>
                  );
                })}
                {/* подписи сетки */}
                {ticks.map((s) => (
                  <span key={`l${s}`} aria-hidden className={cn('w12-stl-lbl w12-num', X0 + (s - clipStart) * pxPerSec >= 30 && 'w12-mid')} style={{ left: X0 + (s - clipStart) * pxPerSec, top: LABEL_TOP }}>
                    {gridStep < 1 || beats.length ? fmt(s - clipStart) : fmt(s - clipStart).slice(0, 5)}
                  </span>
                ))}
                {/* плейхед: линия с ромбиком, от верха до ползунка; тянется */}
                <span
                  role="presentation"
                  onPointerDown={onHeadDown}
                  onPointerMove={onHeadMove}
                  className="w12-stl-head"
                  style={{ transform: `translateX(${X0 + progress * duration * pxPerSec - 7}px)`, height: BAR_TOP + BAR_H / 2 }}
                />
              </div>
            )}
          </div>
          {/* ползунок: прокрутка дорожки влево-вправо (свой скроллбар) */}
          {ready && (
            <div
              ref={barRef}
              role="scrollbar"
              aria-controls="subtitle-timeline-lane"
              aria-orientation="horizontal"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round((scroll.left / Math.max(1, scroll.total - scroll.visible)) * 100) || 0}
              onPointerDown={onBarDown}
              onPointerMove={onBarMove}
              onPointerUp={onBarUp}
              onPointerCancel={onBarUp}
              className="w12-stl-sb"
              style={{ top: BAR_TOP, height: BAR_H, left: X0, right: X0 }}
            >
              <span
                aria-hidden
                style={{
                  width: `max(60px, ${thumbFrac * 100}%)`,
                  left: `calc(${scroll.left / Math.max(1, scroll.total - scroll.visible)} * (100% - max(60px, ${thumbFrac * 100}%)))`
                }}
              />
            </div>
          )}
        </div>

        {/* Плеер отрывка, время, «Фокус» для выбранного слова и масштаб дорожки — одной строкой, как у «Отрывка» */}
        <div className="w12-cut-row">
          <button type="button" className="w12-play-cut" onClick={toggle} disabled={!url || !ready} style={url && ready ? undefined : { opacity: 0.5 }}>
            <span className="w12-dot">{playing ? PAUSE : PLAY}</span>
            <span className="w12-l">{playing ? t('wizard.subs.timeline.pause') : t('wizard.subs.timeline.play')}</span>
          </button>
          <span className="w12-stl-time w12-num"><span className="w12-l">{fmt(time - clipStart)}</span></span>
          <button
            type="button"
            className="w12-stl-focus"
            disabled={selected === null}
            onClick={() => { if (selected !== null) toggleAsrFocus(selected); }}
            aria-pressed={focusOn}
          >
            <span className="w12-mi w12-cap" aria-hidden="true" style={{ '--m': 'url(/assets/figma/pd-star.svg)', '--r': 1.05 } as React.CSSProperties} />
            <span className="w12-l">{focusOn ? t('wizard.subs.timeline.unfocus') : t('wizard.subs.timeline.focus')}</span>
          </button>
          {/* масштаб дорожки: обзор всех слов ↔ точная правка */}
          <label className="w12-stl-zoom">
            <span aria-hidden className="w12-l">−</span>
            <input
              type="range"
              min={0.5}
              max={5}
              step={0.05}
              value={zoom}
              onChange={(e) => setZoom(Number(e.target.value))}
              aria-label={t('wizard.subs.timeline.zoom')}
              disabled={!ready}
            />
            <span aria-hidden className="w12-l">+</span>
          </label>
        </div>
      </div>
    </section>
  );
}
