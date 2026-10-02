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
  /** хэш трека: по нему сверяем с треком безлимита (id бывает не один или пропал) */
  audioHash?: string;
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
  /**
   * Модалку безлимита закрыли, не пройдя: в углу остаётся плашка «Безлимит на трек»
   * (FunnelBadge), она открывает модалку снова с тем же контекстом. Переживает
   * перезагрузку; гасится, когда безлимит открыт (или человек стал платящим).
   */
  badge: UnlimitedContext | null;
  openQuiz: (jobId?: string) => void;
  openUnlimited: (ctx: UnlimitedContext) => void;
  close: () => void;
  clearBadge: () => void;
}

const BADGE_KEY = 'blast-funnel-badge-v1';

function loadBadge(): UnlimitedContext | null {
  try {
    const raw = localStorage.getItem(BADGE_KEY);
    return raw ? (JSON.parse(raw) as UnlimitedContext) : null;
  } catch {
    return null;
  }
}

function saveBadge(ctx: UnlimitedContext | null): void {
  try {
    if (!ctx) {
      localStorage.removeItem(BADGE_KEY);
      return;
    }
    // ролики не храним: ссылки на них подписанные и протухают, модалка дочитает их сама
    const { videos: _videos, ...rest } = ctx;
    localStorage.setItem(BADGE_KEY, JSON.stringify(rest));
  } catch {
    /* приватный режим — плашка проживёт до перезагрузки */
  }
}

export const useFunnelUi = create<FunnelUiState>((set) => ({
  open: null,
  queued: null,
  badge: loadBadge(),
  openQuiz: (jobId) => set((state) => (state.open ? {} : { open: { kind: 'quiz', jobId } })),
  // Не перебиваем квиз на середине: ролики могут дособраться, пока человек отвечает.
  openUnlimited: (ctx) => set((state) => (
    state.open?.kind === 'quiz' ? { queued: ctx } : { open: { kind: 'unlimited', ctx } }
  )),
  close: () => set((state) => {
    // Закрыл безлимит, не открыв его: запоминаем контекст для плашки в углу.
    const badge = state.open?.kind === 'unlimited' ? state.open.ctx : state.badge;
    if (badge !== state.badge) saveBadge(badge);
    return state.queued
      ? { open: { kind: 'unlimited', ctx: state.queued }, queued: null, badge }
      : { open: null, badge };
  }),
  clearBadge: () => set((state) => {
    if (state.badge) saveBadge(null);
    return state.badge ? { badge: null } : {};
  })
}));

/* Что уже показано — чтобы окно не всплывало при каждом заходе. */
const SEEN_KEY = 'blast-funnel-seen-v1';

/** Показано ли уже окно с этим ключом; `maxAgeMs` — через сколько показать снова. */
export function funnelSeen(key: string, maxAgeMs?: number): boolean {
  try {
    const at = (JSON.parse(localStorage.getItem(SEEN_KEY) || '{}') as Record<string, number>)[key];
    if (!at) return false;
    return maxAgeMs === undefined || Date.now() - at < maxAgeMs;
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
