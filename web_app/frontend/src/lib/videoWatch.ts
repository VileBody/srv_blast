import { useSyncExternalStore } from 'react';

/*
 * Какие готовые ролики человек досмотрел в этой вкладке. Окно «Как тебе ролики?» открывалось
 * в ту же секунду, как батч доделался, — оценивать предлагали то, чего ещё не видели. Теперь
 * оно ждёт, пока хоть один ролик батча досмотрят (конец или 85 % длины).
 */
const watched = new Set<string>();
const listeners = new Set<() => void>();

export function markVideoWatched(videoId: string): void {
  if (watched.has(videoId)) return;
  watched.add(videoId);
  listeners.forEach((listener) => listener());
}

/** Досмотренная доля, после которой ролик считается просмотренным. */
export const WATCHED_SHARE = 0.85;

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/** Досмотрен ли хоть один из роликов (по id). */
export function useAnyVideoWatched(videoIds: string[]): boolean {
  const key = videoIds.join('|');
  return useSyncExternalStore(subscribe, () => key.split('|').some((id) => id && watched.has(id)));
}
