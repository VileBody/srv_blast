import { useEffect, useMemo } from 'react';
import { create } from 'zustand';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import type { StoryboardCutsResponse } from '../../lib/types';
import { footagePlaneOf, recipeKeyOf, TimelinePace, TimelineStyleRange, useWizardStore } from '../../stores/wizardStore';
import { dropToSeconds, normalizeDropTime, timingToSeconds } from './useFragmentAudio';
import type { WizardStateData } from '../../stores/wizardStore';

/*
 * Общие данные таймлайна FX и раскадровки «Пула».
 *
 * Склейки не придумываются на фронте: их считает оркестратор тем же кодом, что и
 * рендер (`mlcore.storyboard_plan`). «Авто» — ровно разбиение рендера по темпу
 * трека; «реже» и «чаще» — от той же сетки битов. Рецепт в сторе держит выбранный
 * темп и склейки; ручная правка склеек помечается `edited` и живёт, пока не сменятся
 * вводные (трек, окно, дроп) или темп.
 */

export const PACES: TimelinePace[] = ['sparse', 'auto', 'dense'];

export interface RecipeWindow {
  start: number;
  end: number;
  drop: number | null;
}

export function useRecipeWindow(): RecipeWindow | null {
  const timingFrom = useWizardStore((s) => s.timingFrom);
  const timingTo = useWizardStore((s) => s.timingTo);
  const dropTime = useWizardStore((s) => s.hooks.dropTime);
  return useMemo(() => {
    const start = timingToSeconds(timingFrom);
    const end = timingToSeconds(timingTo);
    if (start === null || end === null || end <= start) return null;
    const drop = dropToSeconds(dropTime);
    return { start, end, drop: drop !== null && drop >= start && drop <= end ? drop : null };
  }, [timingFrom, timingTo, dropTime]);
}

/**
 * Стили лежат по границам кадров. Новые склейки (темп, окно, дроп) двигают эти
 * границы — стиль переезжает на ближайшие новые границы, а не стирается: его
 * подпись живёт в конфиге хука, который уходит в рендер, и стёртая дорожка
 * разошлась бы с тем, что реально применится.
 */
export function remapStyles(styles: TimelineStyleRange[], oldBounds: number[], newBounds: number[]): TimelineStyleRange[] {
  if (!styles.length || oldBounds.length < 2 || newBounds.length < 2) return styles;
  const nearest = (t: number, from: number, to: number) => {
    let best = from;
    for (let i = from; i <= to; i++) if (Math.abs(newBounds[i] - t) < Math.abs(newBounds[best] - t)) best = i;
    return best;
  };
  const last = newBounds.length - 1;
  const out: TimelineStyleRange[] = [];
  for (const style of [...styles].sort((x, y) => x.lane - y.lane || x.a - y.a)) {
    const t0 = oldBounds[Math.min(style.a, oldBounds.length - 1)];
    const t1 = oldBounds[Math.min(style.b, oldBounds.length - 1)];
    let a = nearest(t0, 0, last - 1);
    const b = Math.max(a + 1, nearest(t1, 1, last));
    // на той же дорожке стили не пересекаются: сжимаем к концу предыдущего
    const sameLane = out.filter((o) => o.lane === style.lane); const prev = sameLane[sameLane.length - 1];
    if (prev && a < prev.b) a = prev.b;
    if (a < b) out.push({ ...style, a, b });
    else {
      // места не осталось — пробуем вторую дорожку на тех же кадрах
      const other = (1 - style.lane) as 0 | 1;
      const a2 = nearest(t0, 0, last - 1);
      if (!out.some((o) => o.lane === other && a2 < o.b && b > o.a)) out.push({ ...style, lane: other, a: a2, b });
    }
  }
  return out;
}

/** Подписи стилей, которые пропали с дорожек, убираются и из конфига хука. */
export function dropOrphanStyleLabels(prev: TimelineStyleRange[], next: TimelineStyleRange[]) {
  const gone = [...new Set(prev.map((s) => s.style))].filter((label) => !next.some((s) => s.style === label));
  if (!gone.length) return;
  const { hooks, setHooks } = useWizardStore.getState();
  const config = hooks.kind ? hooks.configs[hooks.kind] : undefined;
  if (!hooks.kind || !config) return;
  const current = config.effectStyles?.length ? config.effectStyles : (config.effectStyle ? [config.effectStyle] : []);
  const kept = current.filter((label) => !gone.includes(label));
  if (kept.length !== current.length) setHooks({ config: { effectStyles: kept, effectStyle: kept[kept.length - 1] } });
}

/** Склейки отрывка с бэка + синхронизация рецепта в сторе. */
export function useRecipeCuts() {
  const track = useWizardStore((s) => s.track);
  const timingFrom = useWizardStore((s) => s.timingFrom);
  const timingTo = useWizardStore((s) => s.timingTo);
  const dropTime = useWizardStore((s) => s.hooks.dropTime);
  const timeline = useWizardStore((s) => s.timeline);
  const setTimeline = useWizardStore((s) => s.setTimeline);
  const window = useRecipeWindow();
  const drop = dropTime ? normalizeDropTime(dropTime) : '';
  const key = recipeKeyOf({ track, timingFrom, timingTo, hooks: { dropTime } });

  const query = useQuery<StoryboardCutsResponse>({
    queryKey: ['storyboard-cuts', key],
    queryFn: () => api.storyboardCuts({ trackId: String(track?.id ?? ''), clipFrom: timingFrom, clipTo: timingTo, dropTime: drop }),
    enabled: Boolean(track?.id && window),
    staleTime: Infinity,
    retry: 1
  });

  // Новые вводные → рецепт берёт склейки выбранного темпа и забывает ручные правки
  // и переходы (их индексы относились к другим склейкам).
  useEffect(() => {
    const data = query.data;
    if (!data) return;
    if (timeline.key !== key || timeline.cuts === null) {
      const cuts = [...data.cuts[timeline.pace]];
      const styles = timeline.cuts && window ? remapStyles(timeline.styles, [window.start, ...timeline.cuts, window.end], [window.start, ...cuts, window.end]) : timeline.styles;
      dropOrphanStyleLabels(timeline.styles, styles);
      setTimeline({ key, cuts, edited: false, transitions: timeline.key === key ? timeline.transitions : {}, styles });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.data, key]);

  const setPace = (pace: TimelinePace) => {
    const data = query.data;
    if (!data || !window) return;
    const cuts = [...data.cuts[pace]];
    const old = timeline.cuts ?? data.cuts[timeline.pace];
    const styles = remapStyles(timeline.styles, [window.start, ...old, window.end], [window.start, ...cuts, window.end]);
    dropOrphanStyleLabels(timeline.styles, styles);
    setTimeline({ pace, cuts, edited: false, transitions: {}, styles });
  };

  return {
    window,
    data: query.data,
    loading: query.isLoading,
    error: query.error as Error | null,
    /** склейки не пришли (сеть, 5xx) — запросить ещё раз: staleTime Infinity сам не повторит */
    retry: () => { void query.refetch(); },
    cuts: timeline.key === key && timeline.cuts ? timeline.cuts : query.data?.cuts[timeline.pace] ?? null,
    pace: timeline.pace,
    setPace
  };
}

/**
 * Раскадровка «Пула» ещё не готова для текущих вводных (идёт подбор или склейки
 * выбранного темпа ещё не пришли). Пока так — «Сгенерировать» ждёт: иначе в рендер
 * ушли бы «авто»-склейки вместо выбранных.
 */
export const useStoryboardBusy = create<{ busy: boolean; setBusy: (busy: boolean) => void }>((set) => ({
  busy: false,
  setBusy: (busy) => set((state) => (state.busy === busy ? state : { busy }))
}));

/** Секунды с одним знаком под язык интерфейса: «1,5» в русском, «1.5» в английском. */
export function fmtSec(value: number, lang: string): string {
  const s = value.toFixed(1);
  return lang.startsWith('ru') ? s.replace('.', ',') : s;
}

/** Стабильный seed видео: одинаковые вводные → одинаковый подбор; «перемешать» его сдвигает. */
export function seedKeyFor(batchKey: string, index: number, shuffle: number): string {
  return `${batchKey}:v${index}:s${shuffle}`;
}

/**
 * Id подсказок «Пула». В режиме вариантов FX тур свой (другие тексты и визуал шага 2) — и
 * id свои: у старых «видел» записан у всех, кто проходил прежний тур.
 */
export function poolGuideId(id: 'total' | 'distribute' | 'storyboard' | 'replace' | 'timeline', variants: boolean): string {
  return `${variants ? 'pool2' : 'pool'}-${id}`;
}

/** Раскадровка «Пула» есть только у футажа из вайбов — от неё зависит длина тура «Пула». */
export function poolStoryboardAvailable(bg: WizardStateData['background']): boolean {
  // по подборке каждого футажа, а не по открытому сейчас списку типа на «Фоне»
  return bg.footage.some((group) => footagePlaneOf(bg, group) === 'vibes');
}

/** Тур «Пула»: всего → распределение → [раскадровка → замена кадра] → таймлайн. */
export function poolTourTotal(bg: WizardStateData['background']): number {
  return poolStoryboardAvailable(bg) ? 5 : 3;
}
