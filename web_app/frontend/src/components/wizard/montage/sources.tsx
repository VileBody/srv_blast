/* Кадры роликов на столе. Каждый ролик привязан к своему набору с шага «Фон»: у вайбов
   кадры — раскадровка «Пула» (реальные клипы, которые уйдут в рендер), у фото — подборка,
   у цвета — однотонный фон со стробом. Выбирают кадры прямо в превью ролика: механика
   раскадровки «Пула» — стрелки, «Заменить кадр», варианты, «Готово» закрепляет. */
import { useEffect, useMemo, useRef, useState, type CSSProperties, type Ref } from 'react';
import { createPortal } from 'react-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../../lib/api';
import { isVideoUrl, posterOf } from '../../../lib/media';
import type { StoryboardCandidate } from '../../../lib/types';
import { useWizardStore, type StoryboardVideo } from '../../../stores/wizardStore';
import { seedKeyFor, useRecipeCuts } from '../storyboardData';
import { shuffleOf, toVideo, withClip } from '../PoolStoryboard';
import '../PoolStoryboard.css';
import { useStripFollow } from '../useStripFollow';
import type { Combo } from './combos';

/** Кадр ролика: клип, фото или цвет — и как он ложится в вертикаль. */
export interface Frame { id: string; url?: string | null; offset?: number; fit: 'cover' | 'contain'; color?: string; strobe?: boolean }

/** Кадры ролика под его исходник. Видео — из раскадровки, фото — подборка, цвет — плашки. */
export function useFramesOf(shots: number) {
  const storyboard = useWizardStore((s) => s.storyboard);
  const bg = useWizardStore((s) => s.background);
  const photos = useQuery({ queryKey: ['photos'], queryFn: api.photos, staleTime: 5 * 60_000, enabled: bg.photo.length > 0 });
  return (c: Combo): Frame[] => {
    if (!c.bgKey) return [];
    if (c.bgKey === '__color__') {
      return Array.from({ length: shots }, (_, k) => ({ id: `color-${k}`, fit: 'cover', color: bg.color || '#f6f5fd', strobe: Boolean(bg.strobe) }));
    }
    if (c.bgKey.startsWith('photo:')) {
      const name = c.bgKey.slice('photo:'.length);
      const url = photos.data?.photos.find((p) => p.name === name)?.previewUrl ?? null;
      return Array.from({ length: shots }, (_, k) => ({ id: `photo-${k}`, url, fit: 'contain' }));
    }
    const wide = (bg.footageFormats?.[c.group ?? ''] ?? (bg.footageType === 'cine16x9' ? '16:9' : '9:16')) === '16:9';
    return (storyboard.videos[c.slotIndex]?.clips ?? []).map((clip) => ({ id: clip.fileName, url: clip.previewUrl, offset: clip.previewOffset, fit: wide ? 'contain' : 'cover' }));
  };
}

/** Видео-кадр: время клипа ведёт таймлайн, а не сам <video>. */
function VideoFrame({ url, offset = 0, at, playing, className, style }: { url: string; offset?: number; at: number; playing: boolean; className?: string; style?: CSSProperties }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const video = ref.current;
    if (!video) return;
    const want = offset + Math.max(0, at);
    if (!playing || Math.abs(video.currentTime - want) > 0.25) { try { video.currentTime = want; } catch { /* метаданные ещё не пришли */ } }
    if (playing && video.paused) void video.play().catch(() => undefined);
    if (!playing && !video.paused) video.pause();
  });
  return <video ref={ref} className={className} style={style} src={url} muted playsInline preload="auto" />;
}

/** Кадр в вертикали: на весь кадр, по центру на размытом фоне или цвет со стробом. */
export function FrameView({ frame, at = 0, t = 0, playing = false, bpm = 128, thumb = false, className = '', style }: {
  frame?: Frame; at?: number; t?: number; playing?: boolean; bpm?: number; thumb?: boolean; className?: string; style?: CSSProperties;
}) {
  if (!frame) return null;
  if (frame.color) {
    const on = !frame.strobe || Math.floor(t * bpm / 60 * 2) % 2 === 0;
    return <div className={`mt-fv ${className}`} style={{ ...style, background: on ? frame.color : '#05010f' }} />;
  }
  if (!frame.url) return null;
  const poster = thumb ? posterOf(frame.url, (frame.offset ?? 0) + 0.1) : null;
  const media = (cls: string) => (isVideoUrl(frame.url!)
    ? (thumb
      ? (poster ? <img className={cls} src={poster} alt="" draggable={false} decoding="async" /> : <video className={cls} src={`${frame.url}#t=${(frame.offset ?? 0) + 0.1}`} muted playsInline preload="metadata" />)
      : <VideoFrame className={cls} url={frame.url!} offset={frame.offset} at={at} playing={playing} />)
    : <img className={cls} src={frame.url!} alt="" draggable={false} />);
  if (frame.fit === 'contain') {
    return (
      <div className={`mt-fv mt-fv-amb ${className}`} style={style}>
        {media('bg')}
        {media('fg')}
      </div>
    );
  }
  return <div className={`mt-fv mt-fv-cov ${className}`} style={style}>{media('fg')}</div>;
}

const Arrow = ({ dir }: { dir: 'l' | 'r' }) => (
  <svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true"><path d={dir === 'l' ? 'M14.5 6 8.5 12l6 6' : 'M9.5 6l6 6-6 6'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
);
const I = ({ d, size = 15 }: { d: string; size?: number }) => (
  <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
);
const REROLL = 'M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16M20 20v-4h-4';
const DICE = 'M7.5 4h9A3.5 3.5 0 0 1 20 7.5v9a3.5 3.5 0 0 1-3.5 3.5h-9A3.5 3.5 0 0 1 4 16.5v-9A3.5 3.5 0 0 1 7.5 4zM9 9h.01M15 15h.01M15 9h.01M9 15h.01';
const LOCK = 'M8 11V8a4 4 0 0 1 8 0v3M6 11h12v9H6z';
// подписи «Отмена/Готово» не помещались в кнопки дока рядом со счётчиком — знаки вместо слов
const CROSS = 'M7 7l10 10M17 7L7 17';
const CHECK = 'M5.5 12.5l4.2 4.2L18.5 7.8';
const secs = (t: number) => `${t.toFixed(1).replace('.', ',')} с`;

/**
 * Выбор кадров прямо в превью ролика — механика и вызовы раскадровки «Пула»: варианты
 * кадра — клипы того же вайба без занятых в батче, «Готово» закрепляет выбор в плане
 * (он и уйдёт в рендер), «Отмена» возвращает прежний, кубик перемешивает незакреплённые.
 */
export function FrameDock({ combo, video, frames, bounds, k, onSeek, onEdit, drop, dockRef, onChanged, onError, request, compact, slot }: {
  combo: Combo; video: StoryboardVideo; frames: Frame[]; bounds: number[]; k: number; onSeek: (k: number) => void;
  /** замена идёт — превью крутит этот кадр по кругу; null — снова играет ролик */
  onEdit: (k: number | null) => void;
  drop: number | null; dockRef?: Ref<HTMLDivElement>; onChanged?: () => void;
  /** запрос замены/перемешивания упал — показать причину (строка статуса стола) */
  onError?: (message: string) => void;
  /** телефон: замену и перемешивание запускает нижняя панель стола (n — счётчик нажатий) */
  request?: { kind: 'edit' | 'shuffle'; n: number } | null;
  /** телефон: полоса кадров уже есть на таймлайне — док виден только пока идёт замена */
  compact?: boolean;
  /**
   * телефон: куда вывести панель замены — на место нижней панели стола. В превью её
   * перекрывала рамка ролика, а «Готово» уезжало за край узкого кадра.
   */
  slot?: HTMLElement | null;
}) {
  const shots = Math.max(1, bounds.length - 1);
  const timingFrom = useWizardStore((s) => s.timingFrom);
  const timingTo = useWizardStore((s) => s.timingTo);
  const batchKey = useWizardStore((s) => s.final.idempotencyKey);
  const storyboard = useWizardStore((s) => s.storyboard);
  const setStoryboardVideo = useWizardStore((s) => s.setStoryboardVideo);
  const recipe = useRecipeCuts();
  const [edit, setEdit] = useState<null | { k: number; orig: StoryboardVideo; candidates: StoryboardCandidate[]; pos: number; loading: boolean }>(null);
  const [busy, setBusy] = useState(false);
  // Номер запроса: ответ, пришедший после смены ролика (или нового запроса), отбрасываем —
  // иначе клип подбора лёг бы в кадр уже другого видео.
  const reqId = useRef(0);
  useEffect(() => { reqId.current += 1; setEdit(null); onEdit(null); setBusy(false); }, [combo.index]); // eslint-disable-line react-hooks/exhaustive-deps
  const allFiles = useMemo(() => Object.values(storyboard.videos).flatMap((v) => v.clips.map((c) => c.fileName)), [storyboard.videos]);

  const startEdit = async () => {
    if (!recipe.cuts) return;
    const id = ++reqId.current;
    setEdit({ k, orig: video, candidates: [], pos: -1, loading: true }); onEdit(k);
    try {
      const res = await api.storyboardAlternatives({ clipFrom: timingFrom, clipTo: timingTo, cuts: recipe.cuts, group: video.group, shot: k, seedKey: video.seedKey, exclude: allFiles, limit: 20 });
      if (id !== reqId.current) return;
      if (!res.candidates.length) { setEdit(null); onEdit(null); onError?.('У вайба не нашлось других клипов для этого кадра'); return; }
      setEdit({ k, orig: video, candidates: res.candidates, pos: 0, loading: false });
      setStoryboardVideo(withClip(video, k, res.candidates[0]));
    } catch (error) {
      if (id !== reqId.current) return;
      setEdit(null); onEdit(null);
      onError?.(error instanceof Error && error.message ? `Не удалось подобрать клипы: ${error.message}` : 'Не удалось подобрать клипы — попробуй ещё раз');
    }
  };
  const variant = (d: number) => {
    if (!edit || edit.loading) return;
    const pos = Math.max(0, Math.min(edit.candidates.length - 1, edit.pos + d));
    if (pos === edit.pos) return;
    setEdit({ ...edit, pos });
    setStoryboardVideo(withClip(edit.orig, edit.k, edit.candidates[pos]));
  };
  const cancel = () => { if (!edit) return; setStoryboardVideo(edit.orig); setEdit(null); onEdit(null); };
  const done = () => {
    if (!edit) return;
    const chosen = edit.candidates[edit.pos];
    const current = useWizardStore.getState().storyboard.videos[video.index] ?? video;
    if (chosen) { setStoryboardVideo({ ...current, pins: { ...current.pins, [edit.k]: chosen.fileName } }); onChanged?.(); }
    setEdit(null); onEdit(null);
  };
  const unpin = (i: number) => { const { [i]: _gone, ...pins } = video.pins; setStoryboardVideo({ ...video, pins }); };
  const shuffle = async () => {
    if (!recipe.cuts || busy) return;
    const seedKey = seedKeyFor(batchKey, video.index, shuffleOf(video.seedKey) + 1);
    // остальные видео того же вайба закреплены целиком: не меняются и не отдают свои клипы
    const others = Object.values(storyboard.videos).filter((v) => v.group === video.group && v.index !== video.index);
    const id = ++reqId.current;
    setBusy(true);
    try {
      const res = await api.storyboardPick({
        clipFrom: timingFrom, clipTo: timingTo, cuts: recipe.cuts,
        videos: [
          ...others.map((v) => ({ index: v.index, group: v.group, seedKey: v.seedKey, pins: Object.fromEntries(v.clips.map((c, i) => [i, c.fileName])) })),
          { index: video.index, group: video.group, seedKey, pins: video.pins }
        ]
      });
      if (id !== reqId.current) return;
      const mine = res.videos.find((v) => v.index === video.index);
      if (mine) { setStoryboardVideo(toVideo(mine, seedKey, video.pins)); onChanged?.(); }
    } catch (error) {
      if (id === reqId.current) onError?.(error instanceof Error && error.message ? `Не удалось перемешать: ${error.message}` : 'Не удалось перемешать — попробуй ещё раз');
    } finally { if (id === reqId.current) setBusy(false); }
  };
  const step = (d: number) => { if (!edit) onSeek((k + d + shots) % shots); };
  const lastRequest = useRef(request?.n ?? 0);
  useEffect(() => {
    if (!request || request.n === lastRequest.current) return;
    lastRequest.current = request.n;
    if (request.kind === 'edit') void startEdit(); else void shuffle();
  }, [request]); // eslint-disable-line react-hooks/exhaustive-deps

  // в замене стрелки листают варианты, Enter/Esc — готово/отмена (таймлайн их не получает)
  useEffect(() => {
    if (!edit) return undefined;
    const key = (e: KeyboardEvent) => {
      if (!['ArrowLeft', 'ArrowRight', 'Enter', 'Escape'].includes(e.key)) return;
      e.stopImmediatePropagation(); e.preventDefault();
      if (e.key === 'ArrowLeft') variant(-1);
      if (e.key === 'ArrowRight') variant(1);
      if (e.key === 'Escape') cancel();
      if (e.key === 'Enter') done();
    };
    window.addEventListener('keydown', key, true);
    return () => window.removeEventListener('keydown', key, true);
  });

  const pinned = Object.keys(video.pins).length;
  const atDrop = drop !== null && Math.abs((bounds[k] ?? -1) - drop) < 0.02;
  const strip = useStripFollow(edit?.k ?? k, frames.length);
  if (compact && !edit) return null;
  // место под панель появляется кадром позже, чем стартует замена — до него ничего не рисуем
  if (compact && edit && !slot) return null;
  if (compact && edit && slot) {
    return createPortal(
      <div className="mm-replace" role="group" aria-label={`Замена кадра ${edit.k + 1}`}>
        <button type="button" className="mm-replace-btn" aria-label="Отмена — вернуть прежний кадр" onClick={cancel}><I d={CROSS} size={18} /></button>
        <div className="mm-replace-var">
          <button type="button" aria-label="Предыдущий вариант" disabled={edit.pos <= 0} onClick={() => variant(-1)}><Arrow dir="l" /></button>
          <span className="cnt num tx">{edit.loading ? 'Подбираем…' : <>{edit.pos + 1}<small> / {edit.candidates.length}</small></>}</span>
          <button type="button" aria-label="Следующий вариант" disabled={edit.loading || edit.pos >= edit.candidates.length - 1} onClick={() => variant(1)}><Arrow dir="r" /></button>
        </div>
        <button type="button" className="mm-replace-btn pri" aria-label="Готово — оставить этот кадр" onClick={done} disabled={edit.loading}><I d={CHECK} size={20} /></button>
      </div>,
      slot
    );
  }
  return (
    <>
      {shots > 1 && !edit && !compact && (
        <>
          <button type="button" className="psb-arrow psb-glass l" aria-label="Предыдущий кадр" onClick={() => step(-1)}><Arrow dir="l" /></button>
          <button type="button" className="psb-arrow psb-glass r" aria-label="Следующий кадр" onClick={() => step(1)}><Arrow dir="r" /></button>
        </>
      )}
      <div ref={dockRef} className="psb-dock psb-glass mt-dock">
        {edit ? (
          <div className="psb-dhead edit">
            <button type="button" className="psb-btn ico" onClick={cancel} aria-label="Отмена" title="Отмена — вернуть прежний · Esc"><I d={CROSS} size={14} /></button>
            <span className="psb-var">
              <button type="button" aria-label="Предыдущий вариант" disabled={edit.pos <= 0} onClick={() => variant(-1)}><Arrow dir="l" /></button>
              <span className="cnt tx">{edit.loading ? '…' : <>{edit.pos + 1} <small>/ {edit.candidates.length}</small></>}</span>
              <button type="button" aria-label="Следующий вариант" disabled={edit.loading || edit.pos >= edit.candidates.length - 1} onClick={() => variant(1)}><Arrow dir="r" /></button>
            </span>
            <button type="button" className="psb-btn pri ico" onClick={done} disabled={edit.loading} aria-label="Готово" title="Готово — оставить и закрепить · Enter"><I d={CHECK} size={15} /></button>
          </div>
        ) : (
          <div className="psb-dhead">
            <div className="psb-info">
              <b>
                <span className="tx">Кадр {k + 1} из {shots} · {secs((bounds[k + 1] ?? 0) - (bounds[k] ?? 0))}</span>
                {atDrop && <span className="psb-badge">дроп</span>}
                {video.pins[k] && <button type="button" className="psb-badge pin" onClick={() => unpin(k)} title="Выбран вручную. Нажми, чтобы открепить">закреплён ×</button>}
                {video.repeats.includes(k) && <span className="psb-badge warn" title="Вайбу не хватило свежих клипов на весь батч — этот клип есть и в другом видео">повтор</span>}
              </b>
              <small className="tx">{combo.bgLabel} · закреплено {pinned} из {shots}</small>
            </div>
            <button type="button" className="psb-btn" onClick={() => void shuffle()} disabled={busy} aria-label="Перемешать" title="Перемешать незакреплённые кадры ролика"><I d={DICE} /></button>
            <button type="button" className="psb-btn pri" onClick={() => void startEdit()}><I d={REROLL} /><span className="tx">Заменить кадр</span></button>
          </div>
        )}
        {!compact && <div ref={strip.ref} className={`psb-strip${edit ? ' editing' : ''}`} data-fade-l={strip.fadeLeft || undefined} data-fade-r={strip.fadeRight || undefined}>
          {frames.map((f, i) => (
            <button key={`${f.id}:${i}`} type="button" className={`psb-seg${edit?.k === i ? ' sel' : ''}${i === k ? ' cur mt-cur' : ''}`} style={{ flexGrow: (bounds[i + 1] ?? 0) - (bounds[i] ?? 0) }} aria-label={`Кадр ${i + 1}`} onClick={() => { if (!edit) onSeek(i); }}>
              <FrameView frame={f} thumb />
              {drop !== null && Math.abs((bounds[i] ?? -1) - drop) < 0.02 && <i className="dm" />}
              {video.pins[i] && <span className="lk"><I d={LOCK} size={9} /></span>}
              {video.repeats.includes(i) && <i className="rp" />}
            </button>
          ))}
        </div>}
      </div>
    </>
  );
}
