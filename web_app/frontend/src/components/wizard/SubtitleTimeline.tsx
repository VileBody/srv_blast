import { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '../../lib/cn';
import { AsrWord, useWizardStore } from '../../stores/wizardStore';

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
const ZOOMS = [80, 130, 220];
const PLAYHEAD_TICK_MS = 50;

function fmt(sec: number): string {
  const s = Math.max(0, sec);
  const mm = Math.floor(s / 60);
  const ss = Math.floor(s % 60);
  const cc = Math.floor((s - Math.floor(s)) * 100);
  return `${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}.${String(cc).padStart(2, '0')}`;
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
  const asr = useWizardStore((state) => state.asr);
  const setAsrWord = useWizardStore((state) => state.setAsrWord);
  const toggleAsrFocus = useWizardStore((state) => state.toggleAsrFocus);
  const resetAsrEdits = useWizardStore((state) => state.resetAsrEdits);

  const clipStart = asr.clipStart ?? 0;
  const clipEnd = asr.clipEnd ?? clipStart + 1;
  const duration = Math.max(0.5, clipEnd - clipStart);

  const [zoomIdx, setZoomIdx] = useState(1);
  const pxPerSec = ZOOMS[zoomIdx];
  const [selected, setSelected] = useState<number | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  /** слово во время перетаскивания — локально, в стор коммитим на pointerup */
  const [live, setLive] = useState<{ index: number; tStart: number; tEnd: number } | null>(null);

  // --- плеер отрывка ---
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(clipStart);
  const scrollRef = useRef<HTMLDivElement>(null);
  const url = track?.localUrl ?? null;

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
        const x = (el.currentTime - clipStart) * pxPerSec;
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
      tStart = Math.min(max - len, Math.max(min, drag.tStart + dx));
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
    if (selected === null) return;
    const step = e.shiftKey ? 0.2 : 0.05;
    if (e.key === 'ArrowLeft') { e.preventDefault(); nudge(selected, -step); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); nudge(selected, step); }
    else if (e.key === 'f' || e.key === 'F' || e.key === 'а' || e.key === 'А') { e.preventDefault(); toggleAsrFocus(selected); }
  };

  const ticks = useMemo(() => {
    const out: number[] = [];
    const step = pxPerSec < 100 ? 2 : 1;
    for (let s = Math.ceil(clipStart); s <= clipEnd; s += step) out.push(s);
    return out;
  }, [clipStart, clipEnd, pxPerSec]);

  const activeIndex = asr.words.findIndex((w) => time >= w.tStart && time < w.tEnd);
  const width = Math.ceil(duration * pxPerSec) + 24;
  const ready = asr.status === 'COMPLETED' && asr.words.length > 0;

  return (
    <section className="mt-[24px] rounded-r15 bg-grad-soft-10 px-[28px] py-[24px]" aria-label={t('wizard.subs.timeline.title')}>
      <div className="flex flex-wrap items-center justify-between gap-space-3">
        <div className="flex items-center gap-space-3">
          <span className="wizard-body !text-text">{t('wizard.subs.timeline.title')}</span>
          <span className={cn(
            'rounded-r9 px-[10px] py-[4px] text-[14px] leading-none',
            asr.status === 'COMPLETED' ? 'bg-[var(--success-bg)] text-[var(--success)]'
              : asr.status === 'FAILED' ? 'bg-[var(--error-bg)] text-[var(--error)]'
                : 'bg-accent-20 text-accent-light'
          )}>
            {asr.status === 'COMPLETED' ? t('wizard.subs.timeline.ready')
              : asr.status === 'FAILED' ? t('wizard.subs.timeline.failed')
                : asr.status === 'IDLE' ? t('wizard.subs.timeline.idle') : t('wizard.subs.timeline.running')}
          </span>
          {asr.edited && <span className="text-[14px] text-text-60">{t('wizard.subs.timeline.edited')}</span>}
        </div>
        <div className="flex items-center gap-space-2">
          <button type="button" aria-label={t('wizard.subs.timeline.zoomOut')} disabled={zoomIdx === 0} onClick={() => setZoomIdx((z) => Math.max(0, z - 1))} className="h-[36px] w-[36px] rounded-r9 bg-grad-soft-20 text-[18px] text-text-80 transition hover:text-text disabled:opacity-40">−</button>
          <button type="button" aria-label={t('wizard.subs.timeline.zoomIn')} disabled={zoomIdx === ZOOMS.length - 1} onClick={() => setZoomIdx((z) => Math.min(ZOOMS.length - 1, z + 1))} className="h-[36px] w-[36px] rounded-r9 bg-grad-soft-20 text-[18px] text-text-80 transition hover:text-text disabled:opacity-40">+</button>
          {asr.edited && (
            <button type="button" onClick={resetAsrEdits} className="ml-space-2 h-[36px] rounded-r9 bg-grad-soft-20 px-[14px] text-[14px] text-text-80 transition hover:text-text">
              {t('wizard.subs.timeline.reset')}
            </button>
          )}
        </div>
      </div>

      <div className="mt-[16px] flex items-center gap-space-4">
        <button
          type="button"
          onClick={toggle}
          disabled={!url || !ready}
          aria-label={playing ? t('wizard.subs.timeline.pause') : t('wizard.subs.timeline.play')}
          className="flex h-[56px] w-[56px] shrink-0 items-center justify-center rounded-full bg-grad-btn text-text transition hover:brightness-110 disabled:opacity-40"
        >
          {playing ? (
            <span className="flex gap-[5px]" aria-hidden><span className="h-[18px] w-[5px] rounded-[2px] bg-text" /><span className="h-[18px] w-[5px] rounded-[2px] bg-text" /></span>
          ) : (
            <span aria-hidden className="ml-[4px] h-0 w-0 border-y-[10px] border-l-[16px] border-y-transparent border-l-[var(--text)]" />
          )}
        </button>
        <span className="font-mono text-[18px] tabular-nums text-text">{fmt(time - clipStart)}</span>
        <span className="text-[14px] text-text-40">/ {fmt(duration)}</span>
        <button
          type="button"
          disabled={selected === null}
          onClick={() => { if (selected !== null) toggleAsrFocus(selected); }}
          aria-pressed={selected !== null && Boolean(asr.words[selected]?.focus)}
          className={cn(
            'ml-auto flex h-[40px] items-center gap-[8px] rounded-r9 px-[14px] text-[15px] transition disabled:opacity-40',
            selected !== null && asr.words[selected]?.focus ? 'bg-accent text-text' : 'bg-grad-soft-20 text-text-80 hover:text-text'
          )}
        >
          <span aria-hidden>★</span>
          {selected !== null && asr.words[selected]?.focus ? t('wizard.subs.timeline.unfocus') : t('wizard.subs.timeline.focus')}
        </button>
      </div>

      <div
        ref={scrollRef}
        tabIndex={0}
        onKeyDown={onKey}
        className="no-scrollbar relative mt-[16px] h-[132px] overflow-x-auto overflow-y-hidden rounded-r10 bg-[rgba(5,1,15,0.45)] outline-none focus-visible:ring-1 focus-visible:ring-accent-light"
        onPointerDown={(e) => {
          // клик по пустому месту — перемотка
          const box = scrollRef.current;
          if (!box) return;
          const rect = box.getBoundingClientRect();
          seek(clipStart + (e.clientX - rect.left + box.scrollLeft) / pxPerSec);
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
            {/* шкала секунд */}
            {ticks.map((s) => (
              <span key={s} className="pointer-events-none absolute top-0 h-full border-l border-[rgba(246,245,253,0.08)]" style={{ left: (s - clipStart) * pxPerSec }}>
                <span className="absolute left-[4px] top-[4px] text-[11px] text-text-40">{fmt(s - clipStart).slice(0, 5)}</span>
              </span>
            ))}
            {/* слова */}
            {asr.words.map((word, index) => {
              const cur = live && live.index === index ? live : word;
              const left = (cur.tStart - clipStart) * pxPerSec;
              const w = Math.max(10, (cur.tEnd - cur.tStart) * pxPerSec);
              const isSel = selected === index;
              const isActive = activeIndex === index;
              return (
                <div
                  key={index}
                  role="button"
                  tabIndex={-1}
                  title={`${word.text} · ${fmt(word.tStart - clipStart)}–${fmt(word.tEnd - clipStart)}`}
                  onPointerDown={onPillDown(index, 'move')}
                  onPointerMove={onPillMove}
                  onPointerUp={onPillUp}
                  onPointerCancel={onPillUp}
                  onDoubleClick={(e) => { e.stopPropagation(); toggleAsrFocus(index); }}
                  className={cn(
                    'absolute top-[36px] flex h-[52px] cursor-grab select-none items-center overflow-hidden rounded-r9 px-[10px] text-[15px] leading-none transition-[box-shadow,background-color] active:cursor-grabbing',
                    word.focus ? 'bg-accent text-text' : 'bg-grad-soft-20 text-text-80',
                    isActive && 'brightness-125 text-text',
                    isSel && 'shadow-[inset_0_0_0_2px_var(--accent-light)] z-[2]'
                  )}
                  style={{ left, width: w }}
                >
                  {word.focus && <span aria-hidden className="mr-[6px] text-[12px]">★</span>}
                  <span className="truncate">{word.text}</span>
                  {/* ручки длительности — тянут только край */}
                  <span onPointerDown={onPillDown(index, 'start')} className="absolute inset-y-0 left-0 w-[7px] cursor-ew-resize hover:bg-[rgba(246,245,253,0.18)]" />
                  <span onPointerDown={onPillDown(index, 'end')} className="absolute inset-y-0 right-0 w-[7px] cursor-ew-resize hover:bg-[rgba(246,245,253,0.18)]" />
                </div>
              );
            })}
            {/* плейхед */}
            <span className="pointer-events-none absolute top-0 z-[3] h-full w-[2px] bg-accent-light" style={{ left: (time - clipStart) * pxPerSec }} />
          </div>
        )}
      </div>
      <p className="mt-[10px] text-[13px] leading-[1.35] text-text-40">{t('wizard.subs.timeline.hint')}</p>
    </section>
  );
}
