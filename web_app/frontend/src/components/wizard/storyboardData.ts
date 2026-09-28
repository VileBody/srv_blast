import { useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import type { StoryboardCutsResponse } from '../../lib/types';
import { TimelinePace, useWizardStore } from '../../stores/wizardStore';
import { dropToSeconds, normalizeDropTime, timingToSeconds } from './useFragmentAudio';

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
  const key = [track?.id ?? '', timingFrom, timingTo, drop].join('|');

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
      setTimeline({ key, cuts: [...data.cuts[timeline.pace]], edited: false, transitions: timeline.key === key ? timeline.transitions : {}, styles: timeline.key === key ? timeline.styles : [] });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.data, key]);

  const setPace = (pace: TimelinePace) => {
    const data = query.data;
    if (!data) return;
    setTimeline({ pace, cuts: [...data.cuts[pace]], edited: false, transitions: {}, styles: [] });
  };

  return {
    window,
    data: query.data,
    loading: query.isLoading,
    error: query.error as Error | null,
    cuts: timeline.key === key && timeline.cuts ? timeline.cuts : query.data?.cuts[timeline.pace] ?? null,
    pace: timeline.pace,
    setPace
  };
}

/** Стабильный seed видео: одинаковые вводные → одинаковый подбор; «перемешать» его сдвигает. */
export function seedKeyFor(batchKey: string, index: number, shuffle: number): string {
  return `${batchKey}:v${index}:s${shuffle}`;
}
