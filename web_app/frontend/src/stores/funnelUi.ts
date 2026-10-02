import { create } from 'zustand';
import type { VideoVersion } from '../lib/types';

/*
 * Какая модалка воронки открыта и с каким контекстом (docs/BOT_TO_WEB_FLOW.md, раздел 4).
 * Страницы только открывают модалку — рисует её FunnelHost в AppShell, поэтому она
 * переживает переход «Генерация → Батч» (ProcessingPage сам уводит на страницу батча).
 *
 * Перезарядка и трипваер живут не здесь, а в кружке лимитов (LimitsIndicator): строка
 * «Безлимит на трек» в поповере и окно, которое всплывает у кружка, когда лимит кончился.
 */

export interface UnlimitedContext {
  jobId?: string;
  projectId?: string;
  trackId?: string;
  trackTitle?: string;
  videos?: VideoVersion[];
  /** откуда открыли: после роликов или упёрлись в бесплатные ролики */
  source: 'results' | 'gate';
}

type Open =
  | { kind: 'quiz'; jobId?: string }
  | { kind: 'unlimited'; ctx: UnlimitedContext }
  | null;

interface FunnelUiState {
  open: Open;
  /** безлимит, который попросили, пока открыт квиз: покажем, когда квиз закроют */
  queued: UnlimitedContext | null;
  openQuiz: (jobId?: string) => void;
  openUnlimited: (ctx: UnlimitedContext) => void;
  close: () => void;
}

export const useFunnelUi = create<FunnelUiState>((set) => ({
  open: null,
  queued: null,
  openQuiz: (jobId) => set((state) => (state.open ? {} : { open: { kind: 'quiz', jobId } })),
  // Не перебиваем квиз на середине: ролики могут дособраться, пока человек отвечает.
  openUnlimited: (ctx) => set((state) => (
    state.open?.kind === 'quiz' ? { queued: ctx } : { open: { kind: 'unlimited', ctx } }
  )),
  close: () => set((state) => (
    state.queued ? { open: { kind: 'unlimited', ctx: state.queued }, queued: null } : { open: null }
  ))
}));

/* Что уже показано — чтобы окно не всплывало при каждом заходе. */
const SEEN_KEY = 'blast-funnel-seen-v1';

export function funnelSeen(key: string): boolean {
  try {
    return Boolean((JSON.parse(localStorage.getItem(SEEN_KEY) || '{}') as Record<string, number>)[key]);
  } catch {
    return false;
  }
}

export function markFunnelSeen(key: string): void {
  try {
    const all = JSON.parse(localStorage.getItem(SEEN_KEY) || '{}') as Record<string, number>;
    all[key] = Date.now();
    localStorage.setItem(SEEN_KEY, JSON.stringify(all));
  } catch {
    /* приватный режим — просто покажем ещё раз */
  }
}
