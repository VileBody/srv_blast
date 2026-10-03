/* Кадры роликов на столе. Каждый ролик привязан к своему набору с шага «Фон»: у вайбов
   кадры — раскадровка «Пула» (реальные клипы, которые уйдут в рендер), у фото — подборка,
   у цвета — однотонный фон со стробом. Выбирают кадры прямо в превью ролика: механика
   раскадровки «Пула» — стрелки, «Заменить кадр», варианты, «Готово» закрепляет. */
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type Ref } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../../lib/api';
import { isVideoUrl, posterOf } from '../../../lib/media';
import type { StoryboardCandidate } from '../../../lib/types';
import { useWizardStore, type StoryboardVideo } from '../../../stores/wizardStore';
import { fmtSec, seedKeyFor, useRecipeCuts } from '../storyboardData';
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
function VideoFrame({ url, offset = 0, at, playing, className, style, onFrame }: {
  url: string; offset?: number; at: number; playing: boolean; className?: string; style?: CSSProperties;
  /** кадр встал (загрузился или перемотан) — размытая подложка широкого кадра снимает его */
  onFrame?: (video: HTMLVideoElement) => void;
}) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const video = ref.current;
    if (!video || !onFrame) return undefined;
    const paint = () => onFrame(video);
    video.addEventListener('loadeddata', paint);
    video.addEventListener('seeked', paint);
    return () => { video.removeEventListener('loadeddata', paint); video.removeEventListener('seeked', paint); };
  }, [onFrame]);
  useEffect(() => {
    const video = ref.current;
    if (!video) return;
    const want = offset + Math.max(0, at);
    // на паузе — точно в кадр, но без повторной перемотки на то же место на каждом рендере:
    // присвоение currentTime запускает seek, даже если значение не изменилось
    const drift = Math.abs(video.currentTime - want);
    if (playing ? drift > 0.25 : drift > 0.001) { try { video.currentTime = want; } catch { /* метаданные ещё не пришли */ } }
    if (playing && video.paused) void video.play().catch(() => undefined);
    if (!playing && !video.paused) video.pause();
  });
  // обложка — тот же JPEG кадра, что в полосе миниатюр (уже в кэше): пока клип грузится или
  // сервер ещё готовит его копию, видно нужный кадр, а не чёрный прямоугольник
  return <video ref={ref} className={className} style={style} src={url} poster={posterOf(url, offset + 0.1) ?? undefined} muted playsInline preload="auto" />;
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
    if (!thumb && isVideoUrl(frame.url)) return <ContainVideo frame={frame} at={at} playing={playing} className={className} style={style} />;
    return (
      <div className={`mt-fv mt-fv-amb ${className}`} style={style}>
        {media('bg')}
        {media('fg')}
      </div>
    );
  }
  return <div className={`mt-fv mt-fv-cov ${className}`} style={style}>{media('fg')}</div>;
}

/**
 * Широкий клип в вертикали: по центру видео, под ним размытый он же. Подложка — картинка, а не
 * второй <video> того же клипа: два декодера на кадр (и вдвое трафика) ради размытого пятна.
 * Есть JPEG-кадр прослойки — берём его; нет (мок, свои адреса) — снимок с основного видео на
 * маленький canvas, когда кадр встал.
 */
function ContainVideo({ frame, at, playing, className, style }: { frame: Frame; at: number; playing: boolean; className: string; style?: CSSProperties }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const poster = posterOf(frame.url, (frame.offset ?? 0) + 0.1);
  const snap = useCallback((video: HTMLVideoElement) => {
    const canvas = canvasRef.current;
    if (!canvas || !video.videoWidth) return;
    try { canvas.getContext('2d')?.drawImage(video, 0, 0, canvas.width, canvas.height); } catch { /* кадр ещё не готов — подложка останется тёмной до следующего */ }
  }, []);
  return (
    <div className={`mt-fv mt-fv-amb ${className}`} style={style}>
      {poster ? <img className="bg" src={poster} alt="" draggable={false} decoding="async" /> : <canvas ref={canvasRef} className="bg" width={32} height={18} />}
      <VideoFrame className="fg" url={frame.url!} offset={frame.offset} at={at} playing={playing} onFrame={poster ? undefined : snap} />
    </div>
  );
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
  const { t, i18n } = useTranslation();
  const secs = (v: number) => t('wizard.pool.sbSecs', { n: fmtSec(v, i18n.language) });
  const timingFrom = useWizardStore((s) => s.timingFrom);
  const timingTo = useWizardStore((s) => s.timingTo);
  const batchKey = useWizardStore((s) => s.final.idempotencyKey);
  const storyboard = useWizardStore((s) => s.storyboard);
  const setStoryboardVideo = useWizardStore((s) => s.setStoryboardVideo);
  const recipe = useRecipeCuts();
  // sbKey — раскадровка, под которую открыта замена: если её пересобрали, прежний клип уже не про неё
  const [edit, setEdit] = useState<null | { k: number; orig: StoryboardVideo; sbKey: string; candidates: StoryboardCandidate[]; pos: number; loading: boolean }>(null);
  const editRef = useRef(edit);
  editRef.current = edit;
  const [busy, setBusy] = useState(false);
  // Номера запросов: ответ, пришедший после смены ролика, отмены или нового запроса,
  // отбрасываем — иначе клип подбора лёг бы в кадр уже другого видео (или вернул бы замену).
  const editReq = useRef(0);
  const shuffleReq = useRef(0);
  const cutsRef = useRef(recipe.cuts);
  cutsRef.current = recipe.cuts;
  /** Бросить замену: пролистанный вариант не должен уехать в рендер — возвращаем прежний клип. */
  const abandon = () => {
    editReq.current += 1;
    const cur = editRef.current;
    if (cur && useWizardStore.getState().storyboard.key === cur.sbKey) setStoryboardVideo(cur.orig);
    editRef.current = null;
  };
  // другой ролик или стол закрыли (док размонтирован) — замена и перемешивание бросаются
  useEffect(() => () => {
    abandon(); shuffleReq.current += 1;
    setEdit(null); onEdit(null); setBusy(false);
  }, [combo.index]); // eslint-disable-line react-hooks/exhaustive-deps
  const allFiles = useMemo(() => Object.values(storyboard.videos).flatMap((v) => v.clips.map((c) => c.fileName)), [storyboard.videos]);

  const startEdit = async () => {
    // замена и перемешивание взаимоисключающие: оба переписывают кадры этого ролика
    if (!recipe.cuts || edit || busy) return;
    const id = ++editReq.current;
    const sbKey = storyboard.key;
    setEdit({ k, orig: video, sbKey, candidates: [], pos: -1, loading: true }); onEdit(k);
    try {
      const res = await api.storyboardAlternatives({ clipFrom: timingFrom, clipTo: timingTo, cuts: recipe.cuts, group: video.group, shot: k, seedKey: video.seedKey, exclude: allFiles, limit: 20 });
      if (id !== editReq.current) return;
      if (useWizardStore.getState().storyboard.key !== sbKey) { setEdit(null); onEdit(null); return; }
      if (!res.candidates.length) { setEdit(null); onEdit(null); onError?.(t('wizard.pool.sbNoAlternatives')); return; }
      setEdit({ k, orig: video, sbKey, candidates: res.candidates, pos: 0, loading: false });
      setStoryboardVideo(withClip(video, k, res.candidates[0]));
    } catch (error) {
      if (id !== editReq.current) return;
      setEdit(null); onEdit(null);
      onError?.(error instanceof Error && error.message ? t('wizard.pool.sbAltFailed', { msg: error.message }) : t('wizard.pool.sbAltFailedRetry'));
    }
  };
  const variant = (d: number) => {
    if (!edit || edit.loading) return;
    const pos = Math.max(0, Math.min(edit.candidates.length - 1, edit.pos + d));
    if (pos === edit.pos) return;
    setEdit({ ...edit, pos });
    setStoryboardVideo(withClip(edit.orig, edit.k, edit.candidates[pos]));
  };
  // отмена в т.ч. пока варианты ещё грузятся: поздний ответ не должен снова открыть замену
  const cancel = () => { if (!edit) return; abandon(); setEdit(null); onEdit(null); };
  const done = () => {
    if (!edit) return;
    const chosen = edit.candidates[edit.pos];
    const current = useWizardStore.getState().storyboard.videos[video.index] ?? video;
    if (chosen) { setStoryboardVideo({ ...current, pins: { ...current.pins, [edit.k]: chosen.fileName } }); onChanged?.(); }
    editReq.current += 1;
    setEdit(null); onEdit(null);
  };
  const unpin = (i: number) => { const { [i]: _gone, ...pins } = video.pins; setStoryboardVideo({ ...video, pins }); };
  const shuffle = async () => {
    if (!recipe.cuts || busy || edit) return;
    const seedKey = seedKeyFor(batchKey, video.index, shuffleOf(video.seedKey) + 1);
    // остальные видео того же вайба закреплены целиком: не меняются и не отдают свои клипы
    const others = Object.values(storyboard.videos).filter((v) => v.group === video.group && v.index !== video.index);
    const id = ++shuffleReq.current;
    const sbKey = storyboard.key;
    const cutsKey = JSON.stringify(recipe.cuts);
    setBusy(true);
    try {
      const res = await api.storyboardPick({
        clipFrom: timingFrom, clipTo: timingTo, cuts: recipe.cuts,
        videos: [
          ...others.map((v) => ({ index: v.index, group: v.group, seedKey: v.seedKey, pins: Object.fromEntries(v.clips.map((c, i) => [i, c.fileName])) })),
          { index: video.index, group: video.group, seedKey, pins: video.pins }
        ]
      });
      if (id !== shuffleReq.current) return;
      // пока ждали, склейки или раскадровка поменялись — ответ про старые кадры, не кладём его
      if (useWizardStore.getState().storyboard.key !== sbKey || JSON.stringify(cutsRef.current) !== cutsKey) return;
      const mine = res.videos.find((v) => v.index === video.index);
      if (mine) { setStoryboardVideo(toVideo(mine, seedKey, video.pins)); onChanged?.(); }
    } catch (error) {
      if (id === shuffleReq.current) onError?.(error instanceof Error && error.message ? t('wizard.pool.sbShuffleFailed', { msg: error.message }) : t('wizard.pool.sbShuffleFailedRetry'));
    } finally { if (id === shuffleReq.current) setBusy(false); }
  };
  const step = (d: number) => { if (!edit) onSeek((k + d + shots) % shots); };
  const lastRequest = useRef(request?.n ?? 0);
  useEffect(() => {
    if (!request || request.n === lastRequest.current) return;
    lastRequest.current = request.n;
    // занятый док не молчит: иначе тап по инструменту на телефоне выглядел бы мёртвым
    if (busy) { onError?.(t('wizard.pool.sbShuffleBusy')); return; }
    if (edit) return;
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
      <div className="mm-replace" role="group" aria-label={t('wizard.pool.sbReplacing', { n: edit.k + 1 })}>
        <button type="button" className="mm-replace-btn" aria-label={t('wizard.pool.sbCancelShot')} onClick={cancel}><I d={CROSS} size={18} /></button>
        <div className="mm-replace-var">
          <button type="button" aria-label={t('wizard.pool.sbPrevVar')} disabled={edit.pos <= 0} onClick={() => variant(-1)}><Arrow dir="l" /></button>
          <span className="cnt num tx">{edit.loading ? t('wizard.pool.sbFinding') : <>{edit.pos + 1}<small> / {edit.candidates.length}</small></>}</span>
          <button type="button" aria-label={t('wizard.pool.sbNextVar')} disabled={edit.loading || edit.pos >= edit.candidates.length - 1} onClick={() => variant(1)}><Arrow dir="r" /></button>
        </div>
        <button type="button" className="mm-replace-btn pri" aria-label={t('wizard.pool.sbDoneShot')} onClick={done} disabled={edit.loading}><I d={CHECK} size={20} /></button>
      </div>,
      slot
    );
  }
  return (
    <>
      {shots > 1 && !edit && !compact && (
        <>
          <button type="button" className="psb-arrow psb-glass l" aria-label={t('wizard.pool.sbPrevShot')} onClick={() => step(-1)}><Arrow dir="l" /></button>
          <button type="button" className="psb-arrow psb-glass r" aria-label={t('wizard.pool.sbNextShot')} onClick={() => step(1)}><Arrow dir="r" /></button>
        </>
      )}
      <div ref={dockRef} className="psb-dock psb-glass mt-dock">
        {edit ? (
          <div className="psb-dhead edit">
            <button type="button" className="psb-btn ico" onClick={cancel} aria-label={t('wizard.pool.sbCancel')} title={t('wizard.pool.sbCancelTip')}><I d={CROSS} size={14} /></button>
            <span className="psb-var">
              <button type="button" aria-label={t('wizard.pool.sbPrevVar')} disabled={edit.pos <= 0} onClick={() => variant(-1)}><Arrow dir="l" /></button>
              <span className="cnt tx">{edit.loading ? '…' : <>{edit.pos + 1} <small>/ {edit.candidates.length}</small></>}</span>
              <button type="button" aria-label={t('wizard.pool.sbNextVar')} disabled={edit.loading || edit.pos >= edit.candidates.length - 1} onClick={() => variant(1)}><Arrow dir="r" /></button>
            </span>
            <button type="button" className="psb-btn pri ico" onClick={done} disabled={edit.loading} aria-label={t('wizard.pool.sbDone')} title={t('wizard.pool.sbDoneTip')}><I d={CHECK} size={15} /></button>
          </div>
        ) : (
          <div className="psb-dhead">
            <div className="psb-info">
              <b>
                <span className="tx">{t('wizard.pool.sbShotOf', { n: k + 1, total: shots, secs: secs((bounds[k + 1] ?? 0) - (bounds[k] ?? 0)) })}</span>
                {atDrop && <span className="psb-badge">{t('wizard.pool.sbDrop')}</span>}
                {video.pins[k] && <button type="button" className="psb-badge pin" onClick={() => unpin(k)} title={t('wizard.pool.sbPickedTip')}>{t('wizard.pool.sbPinned')}</button>}
                {video.repeats.includes(k) && <span className="psb-badge warn" title={t('wizard.pool.sbRepeatTip')}>{t('wizard.pool.sbRepeat')}</span>}
              </b>
              <small className="tx">{t('wizard.pool.sbDockMeta', { bg: t(`chip.${combo.bgLabel}`, { defaultValue: combo.bgLabel }), pinned, shots })}</small>
            </div>
            <button type="button" className="psb-btn" onClick={() => void shuffle()} disabled={busy} aria-busy={busy || undefined} aria-label={t('wizard.pool.sbShuffle')} title={t('wizard.pool.sbShuffleTip')}><I d={DICE} /></button>
            <button type="button" className="psb-btn pri" onClick={() => void startEdit()} disabled={busy} title={t('wizard.pool.sbReplaceTip')}><I d={REROLL} /><span className="tx">{t('wizard.pool.sbReplace')}</span></button>
          </div>
        )}
        {!compact && <div ref={strip.ref} className={`psb-strip${edit ? ' editing' : ''}`} data-fade-l={strip.fadeLeft || undefined} data-fade-r={strip.fadeRight || undefined}>
          {frames.map((f, i) => (
            <button key={`${f.id}:${i}`} type="button" className={`psb-seg${edit?.k === i ? ' sel' : ''}${i === k ? ' cur mt-cur' : ''}`} style={{ flexGrow: (bounds[i + 1] ?? 0) - (bounds[i] ?? 0) }} aria-label={t('wizard.pool.sbShot', { n: i + 1 })} onClick={() => { if (!edit) onSeek(i); }}>
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
