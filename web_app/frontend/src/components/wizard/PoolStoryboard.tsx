import { ReactNode, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { posterOf } from '../../lib/media';
import { useTranslation } from 'react-i18next';
import { api } from '../../lib/api';
import { cssZoom } from '../../lib/zoom';
import type { StoryboardCandidate, StoryboardPickedVideo } from '../../lib/types';
import { emptyStoryboard, recipeKeyOf, StoryboardVideo, useWizardStore } from '../../stores/wizardStore';
import { poolGuideId, poolTourTotal, seedKeyFor, useRecipeCuts, useStoryboardBusy } from './storyboardData';
import { usePlaybackUrl } from './useFragmentAudio';
import { ActionGuideOverlay } from '../guidance/ActionGuideOverlay';
import { useGuideLiveDismissed } from '../guidance/guideLiveState';
import { useFxLab } from './FxLab';
import { useStripFollow } from './useStripFollow';
import { useGuideDismiss, useMarkGuideSeen } from '../guidance/useGuideDismiss';
import { StoryboardGuideVisual, StoryboardReplaceGuideVisual } from './timelineGuides';
import './PoolStoryboard.css';

/*
 * Раскадровка «Пула»: одно видео батча с его реальными исходниками.
 *
 * Рецепт (склейки) общий — с таймлайна FX. Клипы подбирает оркестратор тем же кодом,
 * что и рендер, на весь батч сразу: видео одного вайба не делят клипы. Показанное здесь
 * уходит в рендер закреплённым планом (footage_plan) — повторный подбор при рендере
 * дал бы другие клипы, потому что журнал показов подбора меняется с каждым роликом.
 *
 * Правило экрана: кнопки всегда про кадр, который сейчас на экране. «Заменить кадр» →
 * варианты из того же вайба (без клипов, занятых в батче) → «Готово» закрепляет,
 * «Отмена» возвращает прежний. «Перемешать» перебирает незакреплённые кадры видео.
 */

export interface StoryboardSlot {
  index: number;
  /** вайб футажа; без него у видео нет раскадровки (фото, цвет, свои исходники) */
  group?: string;
  reason?: string;
}

export interface StoryboardChip {
  icon: ReactNode;
  text: string;
  off?: boolean;
}

const TD = 0.32;
const eIO = (p: number) => p < 0.5 ? 8 * p ** 4 : 1 - Math.pow(-2 * p + 2, 4) / 2;
const secs = (t: number) => `${t.toFixed(1).replace('.', ',')} с`;
const kadr = (n: number) => n % 10 === 1 && n % 100 !== 11 ? 'кадр' : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? 'кадра' : 'кадров';
const isSvg = (url: string) => /\.svg(\?|$)/.test(url);
export const shuffleOf = (seedKey: string) => Number(/:s(\d+)$/.exec(seedKey)?.[1] ?? 0);

export function toVideo(picked: StoryboardPickedVideo, seedKey: string, pins: Record<number, string>): StoryboardVideo {
  return { index: picked.index, group: picked.group, seedKey, clips: picked.clips, repeats: picked.repeats, pins, plan: picked.plan };
}

/** Клип-кандидат на место кадра k: и превью, и закреплённый план. */
export function withClip(video: StoryboardVideo, k: number, c: StoryboardCandidate): StoryboardVideo {
  const clips = video.clips.map((clip, i) => (i === k ? { ...clip, fileName: c.fileName, previewUrl: c.previewUrl, previewOffset: c.previewOffset, tags: c.tags } : clip));
  const planClips = ((video.plan.clips as Record<string, unknown>[]) ?? []).map((clip, i) => (
    i === k ? { ...clip, file_name: c.fileName, source_offset_sec: 0, start_time: clip.in_point } : clip
  ));
  return { ...video, clips, plan: { ...video.plan, clips: planClips } };
}

const Arrow = ({ dir }: { dir: 'l' | 'r' }) => (
  <svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true"><path d={dir === 'l' ? 'M14.5 6 8.5 12l6 6' : 'M9.5 6l6 6-6 6'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
);
const Svg = ({ d, size = 15, fill = false }: { d: string; size?: number; fill?: boolean }) => (
  <svg viewBox="0 0 24 24" width={size} height={size} fill={fill ? 'currentColor' : 'none'} stroke={fill ? 'none' : 'currentColor'} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
);
const REROLL = 'M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16M20 20v-4h-4';
const DICE = 'M7.5 4h9A3.5 3.5 0 0 1 20 7.5v9a3.5 3.5 0 0 1-3.5 3.5h-9A3.5 3.5 0 0 1 4 16.5v-9A3.5 3.5 0 0 1 7.5 4zM9 9h.01M15 15h.01M15 9h.01M9 15h.01';
const LOCK = 'M8 11V8a4 4 0 0 1 8 0v3M6 11h12v9H6z';
// подписи «Отмена/Готово» не помещались в кнопки дока рядом со счётчиком — знаки вместо слов
const CROSS = 'M7 7l10 10M17 7L7 17';
const CHECK = 'M5.5 12.5l4.2 4.2L18.5 7.8';

/** edited — у видео есть ручные правки с таймлайна: пилюля «Изменён» закреплена слева над чипами. */
export function PoolStoryboard({ slots, current, chips, edited }: { slots: StoryboardSlot[]; current: number; chips: StoryboardChip[]; edited?: boolean }) {
  const track = useWizardStore((s) => s.track);
  const timingFrom = useWizardStore((s) => s.timingFrom);
  const timingTo = useWizardStore((s) => s.timingTo);
  const batchKey = useWizardStore((s) => s.final.idempotencyKey);
  const storyboard = useWizardStore((s) => s.storyboard);
  const background = useWizardStore((s) => s.background);
  const setStoryboard = useWizardStore((s) => s.setStoryboard);
  const setStoryboardVideo = useWizardStore((s) => s.setStoryboardVideo);
  const recipe = useRecipeCuts();
  const cuts = recipe.cuts;
  const footageSlots = slots.filter((s) => s.group);
  const key = JSON.stringify([timingFrom, timingTo, cuts, footageSlots.map((s) => [s.index, s.group])]);
  const [status, setStatus] = useState<{ loading: boolean; error: string | null }>({ loading: false, error: null });

  /* ── подбор на весь батч, когда поменялись окно, склейки или раскладка вайбов ──
        Раскадровка под старые вводные сразу выбрасывается: иначе, пока идёт новый
        подбор (или если он упал, или вайбов не осталось), в рендер ушли бы старые
        склейки и клипы — или генерацию заблокировала бы «устаревшая раскадровка». ── */
  useEffect(() => {
    if (storyboard.key === key) return undefined;
    if (Object.keys(storyboard.videos).length) setStoryboard(emptyStoryboard());
    if (!cuts || !footageSlots.length) { setStatus({ loading: false, error: null }); return undefined; }
    let cancelled = false;
    setStatus({ loading: true, error: null });
    api.storyboardPick({
      clipFrom: timingFrom,
      clipTo: timingTo,
      cuts,
      videos: footageSlots.map((s) => ({ index: s.index, group: String(s.group), seedKey: seedKeyFor(batchKey, s.index, 0) }))
    }).then((res) => {
      if (cancelled) return;
      setStoryboard({ key, videos: Object.fromEntries(res.videos.map((v) => [v.index, toVideo(v, seedKeyFor(batchKey, v.index, 0), {})])) });
      setStatus({ loading: false, error: null });
    }).catch((err: Error) => { if (!cancelled) setStatus({ loading: false, error: err.message }); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  /* ── генерация ждёт, пока раскадровка не соберётся под текущие вводные ──
        Ошибка подбора генерацию не держит: видео без плана рендер подберёт сам по
        склейкам рецепта. Ошибка склеек при выбранном темпе — держит: без них темп
        до рендера не дойдёт. ── */
  const timeline = useWizardStore((s) => s.timeline);
  const currentRecipeKey = useWizardStore((s) => recipeKeyOf(s));
  const recipeCustom = timeline.pace !== 'auto' || timeline.edited;
  const recipeReady = timeline.key === currentRecipeKey && Boolean(timeline.cuts);
  const storyboardPending = footageSlots.length > 0 && Boolean(cuts) && storyboard.key !== key && !status.error;
  const busy = status.loading || storyboardPending || (recipeCustom && !recipeReady);
  const setBusy = useStoryboardBusy((s) => s.setBusy);
  useEffect(() => { setBusy(busy); }, [busy, setBusy]);
  useEffect(() => () => setBusy(false), [setBusy]);

  const slot = slots[current];
  const video = slot?.group && storyboard.key === key ? storyboard.videos[slot.index] : undefined;
  const bounds = useMemo(() => {
    if (!recipe.window || !cuts) return [] as number[];
    return [0, ...cuts.map((c) => c - recipe.window!.start), recipe.window.end - recipe.window.start];
  }, [cuts, recipe.window]);
  const shots = Math.max(0, bounds.length - 1);
  const dur = bounds[bounds.length - 1] ?? 0;
  const dropRel = recipe.window?.drop != null ? recipe.window.drop - recipe.window.start : null;

  /* ── воспроизведение: клипы видео по склейкам + звук отрывка ── */
  const [t, setT] = useState(0.4);
  const tRef = useRef(0.4);
  const [playing, setPlaying] = useState(true);
  // звук ещё качается: время стоит (иначе картинка убегала вперёд без музыки и потом
  // дёргалась назад), на кнопке — загрузка вместо «мёртвого» плея
  const [buffering, setBuffering] = useState(false);
  const bufRef = useRef(false);
  const [edit, setEdit] = useState<null | { k: number; orig: StoryboardVideo; candidates: StoryboardCandidate[]; pos: number; loading: boolean }>(null);
  useEffect(() => { setEdit(null); tRef.current = 0.4; setT(0.4); }, [current]);
  const audioUrl = usePlaybackUrl(track);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  useEffect(() => {
    if (!audioUrl) return undefined;
    const audio = new Audio(audioUrl);
    audio.preload = 'auto';
    audioRef.current = audio;
    return () => { audio.pause(); audioRef.current = null; };
  }, [audioUrl]);
  // Звук только когда играет весь ролик: в режиме замены кадр крутится по кругу без музыки.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !recipe.window) return;
    if (playing && !edit && video) { audio.currentTime = recipe.window.start + tRef.current; void audio.play().catch(() => undefined); } else audio.pause();
  }, [playing, edit, video, recipe.window]);
  useEffect(() => {
    if (!playing || !shots) return undefined;
    let raf = 0; let last = 0;
    const tick = (ts: number) => {
      const audio = audioRef.current;
      const waiting = Boolean(!edit && audio && !audio.paused && audio.readyState < 3);
      if (waiting !== bufRef.current) { bufRef.current = waiting; setBuffering(waiting); }
      if (waiting) { last = ts; raf = requestAnimationFrame(tick); return; }
      let next: number;
      if (!edit && audio && !audio.paused && audio.readyState >= 2 && recipe.window) next = audio.currentTime - recipe.window.start;
      else { next = tRef.current + (last ? (ts - last) / 1000 : 0); }
      last = ts;
      if (edit) { const a = bounds[edit.k]; const b = bounds[edit.k + 1]; if (next >= b || next < a) next = a; }
      else if (next >= dur) { next = 0; if (audio && recipe.window) audio.currentTime = recipe.window.start; }
      tRef.current = next;
      setT(next);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(raf); if (bufRef.current) { bufRef.current = false; setBuffering(false); } };
  }, [playing, shots, edit, bounds, dur, recipe.window]);
  const shotAt = (v: number) => { let s = 0; while (s < shots - 1 && v >= bounds[s + 1]) s++; return s; };
  const s = shotAt(t);
  const since = t - (bounds[s] ?? 0);
  const inTr = !edit && s > 0 && since < TD;
  const videoRefs = useRef<(HTMLVideoElement | null)[]>([]);
  useEffect(() => {
    videoRefs.current.forEach((el, i) => {
      const clip = video?.clips[i];
      if (!el || !clip) return;
      const visible = i === s || (inTr && i === s - 1);
      if (!visible) { if (!el.paused) el.pause(); return; }
      const want = clip.previewOffset + (i === s ? t - bounds[i] : bounds[i + 1] - bounds[i]);
      if (!playing || Math.abs(el.currentTime - want) > 0.25) { try { el.currentTime = Math.max(0, want); } catch { /* метаданные ещё не пришли */ } }
      if (playing && el.paused) void el.play().catch(() => undefined);
      if (!playing && !el.paused) el.pause();
    });
  });

  /* ── действия ── */
  // Во время игры время ведёт звук (тик берёт его из audio.currentTime) — переход на
  // кадр двигает и его, иначе следующий тик вернул бы кадр назад.
  const seekTo = (v: number) => {
    tRef.current = v; setT(v);
    const audio = audioRef.current;
    if (audio && recipe.window) { try { audio.currentTime = recipe.window.start + v; } catch { /* ещё не загрузился */ } }
  };
  const seekShot = (k: number) => seekTo(bounds[k] + 0.001);
  const step = (d: number) => { if (!shots || edit) return; seekShot((s + d + shots) % shots); };
  const allFiles = () => Object.values(storyboard.videos).flatMap((v) => v.clips.map((c) => c.fileName));

  // Номер запроса замены: ответ после перелистывания видео (или нового запроса) отбрасываем,
  // иначе подобранный клип лёг бы в кадр уже другого видео.
  const editReq = useRef(0);
  useEffect(() => { editReq.current += 1; }, [current]);
  // Сбой подбора ОДНОГО кадра — временная строка в доке, а не заглушка на всю раскадровку:
  // раскадровка цела, человек просто пробует ещё раз.
  const [editNote, setEditNote] = useState<string | null>(null);
  useEffect(() => {
    if (!editNote) return undefined;
    const timer = window.setTimeout(() => setEditNote(null), 4000);
    return () => window.clearTimeout(timer);
  }, [editNote]);
  const startEdit = async () => {
    if (!video || !cuts) return;
    const k = s;
    const id = ++editReq.current;
    setEdit({ k, orig: video, candidates: [], pos: -1, loading: true });
    seekTo(bounds[k] + 0.001); setPlaying(true);
    try {
      const res = await api.storyboardAlternatives({ clipFrom: timingFrom, clipTo: timingTo, cuts, group: video.group, shot: k, seedKey: video.seedKey, exclude: allFiles(), limit: 20 });
      if (id !== editReq.current) return;
      if (!res.candidates.length) { setEdit(null); setEditNote('У вайба не нашлось других клипов для этого кадра'); return; }
      setEdit({ k, orig: video, candidates: res.candidates, pos: 0, loading: false });
      setStoryboardVideo(withClip(video, k, res.candidates[0]));
    } catch (err) {
      if (id !== editReq.current) return;
      setEdit(null);
      setEditNote((err as Error).message ? `Не удалось подобрать клипы: ${(err as Error).message}` : 'Не удалось подобрать клипы — попробуй ещё раз');
    }
  };
  const variant = (dir: number) => {
    if (!edit || edit.loading) return;
    const pos = Math.max(0, Math.min(edit.candidates.length - 1, edit.pos + dir));
    if (pos === edit.pos) return;
    setEdit({ ...edit, pos });
    setStoryboardVideo(withClip(edit.orig, edit.k, edit.candidates[pos]));
    seekTo(bounds[edit.k] + 0.001);
  };
  const cancelEdit = () => { if (!edit) return; setStoryboardVideo(edit.orig); setEdit(null); };
  const doneEdit = () => {
    if (!edit || !video) return;
    const chosen = edit.candidates[edit.pos];
    if (chosen) setStoryboardVideo({ ...video, pins: { ...video.pins, [edit.k]: chosen.fileName } });
    setEdit(null);
  };
  const unpin = () => {
    if (!video) return;
    const { [s]: _dropped, ...pins } = video.pins;
    setStoryboardVideo({ ...video, pins });
  };
  const shuffle = async () => {
    if (!video || !cuts) return;
    const seedKey = seedKeyFor(batchKey, video.index, shuffleOf(video.seedKey) + 1);
    // Остальные видео того же вайба закреплены целиком: не меняются и не отдают свои клипы.
    const others = Object.values(storyboard.videos).filter((v) => v.group === video.group && v.index !== video.index);
    setStatus({ loading: true, error: null });
    try {
      const res = await api.storyboardPick({
        clipFrom: timingFrom, clipTo: timingTo, cuts,
        videos: [
          ...others.map((v) => ({ index: v.index, group: v.group, seedKey: v.seedKey, pins: Object.fromEntries(v.clips.map((c, i) => [i, c.fileName])) })),
          { index: video.index, group: video.group, seedKey, pins: video.pins }
        ]
      });
      const mine = res.videos.find((v) => v.index === video.index);
      if (mine) setStoryboardVideo(toVideo(mine, seedKey, video.pins));
      setStatus({ loading: false, error: null });
    } catch (err) {
      setStatus({ loading: false, error: (err as Error).message });
    }
  };

  /* ── клавиатура: стрелки — кадры (в замене — варианты), Esc/Enter — отмена/готово ── */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).matches('input, textarea, select')) return;
      if (edit) {
        if (e.key === 'ArrowLeft') variant(-1);
        if (e.key === 'ArrowRight') variant(1);
        if (e.key === 'Escape') cancelEdit();
        if (e.key === 'Enter') { e.preventDefault(); doneEdit(); }
        return;
      }
      if (e.key === 'ArrowLeft') step(-1);
      if (e.key === 'ArrowRight') step(1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  /* ── лента чипов: листается перетаскиванием ── */
  const railRef = useRef<HTMLDivElement>(null);
  const railDrag = useRef<{ x: number; left: number; moved: boolean } | null>(null);
  const markRef = useRef<HTMLSpanElement>(null);
  const [markW, setMarkW] = useState(0);
  // ширина пилюли — по факту (шрифт догружается, язык меняет текст): от неё отступ и фейд ленты
  useLayoutEffect(() => {
    const el = markRef.current;
    if (!edited || !el) { setMarkW(0); return undefined; }
    const sync = () => setMarkW(el.offsetWidth);
    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(el);
    return () => observer.disconnect();
  }, [edited]);

  const clip = video?.clips[s];
  const pinned = video ? Object.keys(video.pins).length : 0;
  /* ── подсказки 3–4 серии «Пула»: после «Распредели видео» (соседняя панель — поэтому
        её ЖИВОЙ dismissed), только когда у видео на экране есть раскадровка. Замена кадра
        ждёт действия: пока ни один кадр не закреплён, после простоя она вернётся. ── */
  const { t: tr } = useTranslation();
  const frameGuideRef = useRef<HTMLDivElement>(null);
  const dockGuideRef = useRef<HTMLDivElement>(null);
  const fxLab = useFxLab();
  const distributeGuideDismissed = useGuideLiveDismissed(poolGuideId('distribute', fxLab));
  const sbReady = Boolean(video && clip) && !edit && distributeGuideDismissed;
  const [replaceGuideDismissed, setReplaceGuideDismissed] = useGuideDismiss(poolGuideId('replace', fxLab), sbReady && pinned === 0, false);
  const [frameGuideDismissed, setFrameGuideDismissed] = useGuideDismiss(poolGuideId('storyboard', fxLab), false);
  const showFrameGuide = sbReady && !frameGuideDismissed;
  const showReplaceGuide = sbReady && frameGuideDismissed && !replaceGuideDismissed;
  useMarkGuideSeen(poolGuideId('storyboard', fxLab), showFrameGuide);
  useMarkGuideSeen(poolGuideId('replace', fxLab), showReplaceGuide);

  // Ошибка склеек — первой: пока её нет, «Сгенерировать» ждёт, и причина должна быть видна у любого видео.
  const placeholder = !slot ? null
    : recipe.error ? `Не удалось посчитать склейки: ${recipe.error.message}`
    : !slot.group ? (slot.reason ?? 'Исходники этого видео подберутся при генерации')
      : status.error ? `Не удалось подобрать исходники: ${status.error}`
        : !video ? 'Подбираем исходники…'
          : null;

  // лента кадров: на узком экране прокручивается и держит текущий кадр в центре
  const strip = useStripFollow(edit?.k ?? s, video?.clips.length ?? 0);
  return (
    <div className="psb">
      <div ref={frameGuideRef} style={{ position: 'relative', height: '100%', aspectRatio: '9 / 16', maxWidth: '100%' }}>
        <div className="psb-frame" style={{ width: '100%' }}>
          {video && video.clips.map((c, i) => {
            const visible = i === s || (inTr && i === s - 1);
            const style: React.CSSProperties = inTr && i === s ? { clipPath: `inset(0 ${(100 - 100 * eIO(since / TD)).toFixed(1)}% 0 0)`, zIndex: 2 } : { zIndex: i === s ? 2 : 1 };
            if (!c.previewUrl) return null;
            // демо-превью мока — анимированный SVG: <video> его не откроет
            return isSvg(c.previewUrl)
              ? <img key={`${c.fileName}:${i}`} className={`shot${visible ? ' on' : ''}`} src={c.previewUrl} alt="" draggable={false} style={style} />
              // качаем только соседей текущего кадра (предыдущий — для перехода, следующий — к склейке):
              // раньше все кадры ролика грузились разом, на слабой сети это забивало канал
              : <video key={`${c.fileName}:${i}`} ref={(el) => { videoRefs.current[i] = el; }} className={`shot${visible ? ' on' : ''}`} src={Math.abs(i - s) <= 1 || (s === video.clips.length - 1 && i === 0) ? c.previewUrl : undefined} muted playsInline preload="auto" style={style} />;
          })}
          {placeholder && <div className="psb-ph"><span className="tx">{placeholder}</span></div>}
          <div className="psb-shade" />
          <div className="psb-chips" data-mark={edited || undefined} style={markW ? ({ '--mark-w': `${markW}px` } as React.CSSProperties) : undefined}>
            {/* «Изменён» стоит на месте, чипы уезжают под него и тают у его края */}
            {edited && <span ref={markRef} className="psb-chip psb-mark" title={tr('wizard.pool.editedOnTimeline')}><span className="tx">{tr('wizard.pool.edited')}</span></span>}
            <div ref={railRef} className="rail"
              onPointerDown={(e) => { if (e.pointerType !== 'touch' && railRef.current) railDrag.current = { x: e.clientX, left: railRef.current.scrollLeft, moved: false }; }}
              onPointerMove={(e) => { const d = railDrag.current; if (!d || !railRef.current) return; const dx = e.clientX - d.x; if (Math.abs(dx) > 5) { d.moved = true; railRef.current.scrollLeft = d.left - dx / cssZoom(railRef.current); } }}
              onPointerUp={() => { railDrag.current = null; }} onPointerLeave={() => { railDrag.current = null; }}>
              {chips.map((chip, i) => <span key={i} className={`psb-chip psb-glass${chip.off ? ' off' : ''}`}>{chip.icon}<span className="tx">{chip.text}</span></span>)}
            </div>
          </div>
          {/* Стрелки кадров — внутри ролика, у краёв: всё управление кадром живёт в самом видео. */}
          {video && shots > 1 && !edit && (
            <>
              <button type="button" className="psb-arrow psb-glass l" aria-label="Предыдущий кадр" onClick={() => step(-1)}><Arrow dir="l" /></button>
              <button type="button" className="psb-arrow psb-glass r" aria-label="Следующий кадр" onClick={() => step(1)}><Arrow dir="r" /></button>
            </>
          )}
          {video && (
            <button type="button" className="psb-play psb-glass" aria-label={buffering ? 'Загружается' : playing ? 'Пауза' : 'Воспроизвести'} aria-busy={buffering || undefined} aria-pressed={playing} onClick={() => setPlaying((v) => !v)}>
              {buffering ? <span className="spinner" aria-hidden="true" /> : playing ? <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true"><path d="M6 4h3v12H6zM11 4h3v12h-3z" fill="currentColor" /></svg> : <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true"><path d="M6 3.5v13l11-6.5L6 3.5Z" fill="currentColor" /></svg>}
            </button>
          )}
          {video && clip && (
            <div ref={dockGuideRef} className="psb-dock psb-glass">
              {edit ? (
                <div className="psb-dhead edit">
                  <button type="button" className="psb-btn ico" onClick={cancelEdit} aria-label="Отмена" title="Отмена — вернуть прежний клип · Esc"><Svg d={CROSS} size={14} /></button>
                  <span className="psb-var">
                    <button type="button" aria-label="Предыдущий вариант" disabled={edit.pos <= 0} onClick={() => variant(-1)}><Arrow dir="l" /></button>
                    <span className="cnt tx">{edit.loading ? '…' : <>{edit.pos + 1} <small>/ {edit.candidates.length}</small></>}</span>
                    <button type="button" aria-label="Следующий вариант" disabled={edit.loading || edit.pos >= edit.candidates.length - 1} onClick={() => variant(1)}><Arrow dir="r" /></button>
                  </span>
                  <button type="button" className="psb-btn pri ico" onClick={doneEdit} disabled={edit.loading} aria-label="Готово" title="Готово — оставить этот клип и закрепить"><Svg d={CHECK} size={15} /></button>
                </div>
              ) : (
                <div className="psb-dhead">
                  <div className="psb-info">
                    <b>
                      <span className="tx">Кадр {s + 1} из {shots} · {secs(bounds[s + 1] - bounds[s])}</span>
                      {dropRel !== null && Math.abs(bounds[s] - dropRel) < 0.01 && <span className="psb-badge">дроп</span>}
                      {video.pins[s] && <button type="button" className="psb-badge pin" onClick={unpin} title="Заменён вручную и закреплён. Нажми, чтобы открепить">закреплён ×</button>}
                      {video.repeats.includes(s) && <span className="psb-badge warn" title="Вайбу не хватило свежих клипов на весь батч — этот клип есть и в другом видео">повтор</span>}
                    </b>
                    <small className={editNote ? 'tx psb-note' : 'tx'} role={editNote ? 'status' : undefined}>{editNote ?? (clip.tags.length ? clip.tags.join(' · ') : `${shots} ${kadr(shots)} · закреплено ${pinned}`)}</small>
                  </div>
                  <button type="button" className="psb-btn" onClick={() => void shuffle()} disabled={status.loading} aria-label="Перемешать видео" title="Перемешать незакреплённые кадры этого видео"><Svg d={DICE} /></button>
                  <button type="button" className="psb-btn pri" onClick={() => { setReplaceGuideDismissed(true); void startEdit(); }} aria-label="Заменить кадр" title="Подобрать другой клип для кадра на экране"><Svg d={REROLL} /><span className="tx">Заменить кадр</span></button>
                </div>
              )}
              <div ref={strip.ref} className={`psb-strip${edit ? ' editing' : ''}`} data-fade-l={strip.fadeLeft || undefined} data-fade-r={strip.fadeRight || undefined}>
                {video.clips.map((c, i) => (
                  <button key={`${c.fileName}:${i}`} type="button" className={`psb-seg${edit?.k === i ? ' sel' : ''}${i === s ? ' cur' : ''}`} style={{ flexGrow: bounds[i + 1] - bounds[i] }} aria-label={`Кадр ${i + 1}`} onClick={() => { if (!edit) seekShot(i); }}>
                    {c.previewUrl && (isSvg(c.previewUrl) ? <img src={c.previewUrl} alt="" draggable={false} />
                      : posterOf(c.previewUrl, c.previewOffset + 0.1) ? <img src={posterOf(c.previewUrl, c.previewOffset + 0.1)!} alt="" draggable={false} decoding="async" />
                        : <video src={`${c.previewUrl}#t=${c.previewOffset + 0.1}`} muted playsInline preload="metadata" />)}
                    {dropRel !== null && Math.abs(bounds[i] - dropRel) < 0.01 && <i className="dm" />}
                    {video.pins[i] && <span className="lk"><Svg d={LOCK} size={9} /></span>}
                    {video.repeats.includes(i) && <i className="rp" />}
                    <i className="pg" style={{ width: i === s ? `${((t - bounds[i]) / (bounds[i + 1] - bounds[i]) * 100).toFixed(1)}%` : 0 }} />
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      <ActionGuideOverlay
        open={showFrameGuide}
        targetRef={frameGuideRef}
        title={tr('wizard.pool.guideStoryboardTitle')}
        text={tr('wizard.pool.guideStoryboardText')}
        dismissLabel={tr('wizard.pool.guideNext')}
        progressLabel={tr('wizard.guideProgress', { current: 3, total: poolTourTotal(background) })}
        onDismiss={() => setFrameGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<StoryboardGuideVisual />}
      />
      <ActionGuideOverlay
        open={showReplaceGuide}
        targetRef={dockGuideRef}
        title={tr('wizard.pool.guideReplaceTitle')}
        text={tr('wizard.pool.guideReplaceText')}
        dismissLabel={tr('wizard.pool.guideNext')}
        progressLabel={tr('wizard.guideProgress', { current: 4, total: poolTourTotal(background) })}
        onDismiss={() => setReplaceGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<StoryboardReplaceGuideVisual />}
      />
    </div>
  );
}
