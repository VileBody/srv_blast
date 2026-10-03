import { create } from 'zustand';
import type { VideoVersion } from '../lib/types';

/*
 * Какая модалка воронки открыта и с каким контекстом (docs/BOT_TO_WEB_FLOW.md, раздел 4).
 * Страницы только открывают модалку — рисует её FunnelHost в AppShell, поэтому она
 * переживает переход «Генерация → Батч» (ProcessingPage сам уводит на страницу батча).
 *
 * Перезарядка и трипваер живут не здесь, а в кружке лимитов (LimitsIndicator): строка
 * «Безлимит на трек» в поповере и окно, которое всплывает у кружка, когда лимит кончился.
 *
 * Плашка и «что уже показано» лежат в localStorage под ключом аккаунта: после смены
 * аккаунта в том же браузере чужая плашка не всплывает. Аккаунт сообщает FunnelHost
 * (bindFunnelUser — синхронно в рендере, syncUser — в стор).
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
  /** чей это стор: id аккаунта (null — ещё не знаем) */
  user: string | null;
  open: Open;
  /** безлимит, который попросили, пока открыт квиз: покажем, когда квиз закроют */
  queued: UnlimitedContext | null;
  /**
   * Безлимит уже открыт, а пришёл другой контекст (например, готов другой батч):
   * открытое окно не подменяем посреди шагов. Свежий контекст достанется плашке,
   * если окно закроют не пройдя.
   */
  later: UnlimitedContext | null;
  /**
   * Модалку безлимита закрыли, не пройдя: в углу остаётся плашка «Безлимит на трек»
   * (FunnelBadge), она открывает модалку снова с тем же контекстом. Переживает
   * перезагрузку; гасится, когда безлимит открыт (или человек стал платящим).
   */
  badge: UnlimitedContext | null;
  syncUser: (userId: string) => void;
  openQuiz: (jobId?: string) => void;
  openUnlimited: (ctx: UnlimitedContext) => void;
  /** закрыть, не пройдя: у безлимита остаётся плашка */
  close: () => void;
  /** убрать окно без плашки: воронка недоступна (нет Telegram) или показать нечего */
  dismiss: () => void;
  clearBadge: () => void;
}

/* Аккаунт, под ключом которого лежат плашка и «показано». */
let funnelUser: string | null = null;

/**
 * Сообщить, чей сейчас сайт. Зовётся в рендере FunnelHost (до страниц в том же проходе),
 * чтобы funnelSeen со страниц уже смотрел в ключ этого аккаунта.
 */
export function bindFunnelUser(userId: string | null): void {
  funnelUser = userId;
}

const BADGE_KEY = 'blast-funnel-badge-v1';
const SEEN_KEY = 'blast-funnel-seen-v1';
const scoped = (key: string) => `${key}:${funnelUser}`;

function loadBadge(): UnlimitedContext | null {
  if (!funnelUser) return null;
  try {
    const raw = localStorage.getItem(scoped(BADGE_KEY));
    return raw ? (JSON.parse(raw) as UnlimitedContext) : null;
  } catch {
    return null;
  }
}

function saveBadge(ctx: UnlimitedContext | null): void {
  if (!funnelUser) return;
  try {
    if (!ctx) {
      localStorage.removeItem(scoped(BADGE_KEY));
      return;
    }
    // ролики не храним: ссылки на них подписанные и протухают, модалка дочитает батч сама
    const { videos: _videos, ...rest } = ctx;
    localStorage.setItem(scoped(BADGE_KEY), JSON.stringify(rest));
  } catch {
    /* приватный режим — плашка проживёт до перезагрузки */
  }
}

/** Тот же ли это заход в безлимит: один батч или оба без батча (упёрлись в лимит). */
function sameOffer(a: UnlimitedContext, b: UnlimitedContext): boolean {
  return (a.jobId ?? null) === (b.jobId ?? null);
}

export const useFunnelUi = create<FunnelUiState>((set) => ({
  user: null,
  open: null,
  queued: null,
  later: null,
  badge: null,
  // Сменился аккаунт — окна и плашка прежнего ему не принадлежат.
  syncUser: (userId) => set((state) => {
    if (state.user === userId) return {};
    bindFunnelUser(userId);
    const switched = state.user !== null;
    return {
      user: userId,
      badge: loadBadge(),
      ...(switched ? { open: null, queued: null, later: null } : {})
    };
  }),
  openQuiz: (jobId) => set((state) => (state.open ? {} : { open: { kind: 'quiz', jobId } })),
  openUnlimited: (ctx) => set((state) => {
    // Не перебиваем квиз на середине: ролики могут дособраться, пока человек отвечает.
    if (state.open?.kind === 'quiz') return { queued: ctx };
    // Безлимит уже на экране: шаги не подменяем (план, ветка, прогресс), свежий контекст —
    // плашке на случай, если окно закроют.
    if (state.open?.kind === 'unlimited') {
      return sameOffer(state.open.ctx, ctx) || (state.later && sameOffer(state.later, ctx)) ? {} : { later: ctx };
    }
    return { open: { kind: 'unlimited', ctx } };
  }),
  close: () => set((state) => {
    if (state.open?.kind === 'unlimited') {
      // Закрыл безлимит, не открыв его: запоминаем контекст для плашки в углу.
      // Отложенный свежий контекст уходит в плашку и сам уже не всплывает — только по ней.
      const badge = state.later ?? state.open.ctx;
      if (state.later?.jobId) markFunnelSeen(`unlimited:${state.later.jobId}`);
      saveBadge(badge);
      return { open: null, later: null, badge };
    }
    return state.queued
      ? { open: { kind: 'unlimited', ctx: state.queued }, queued: null }
      : { open: null };
  }),
  dismiss: () => set((state) => (
    state.open?.kind === 'quiz' && state.queued
      ? { open: { kind: 'unlimited', ctx: state.queued }, queued: null }
      : { open: null, later: null }
  )),
  clearBadge: () => set((state) => {
    if (state.badge) saveBadge(null);
    return state.badge ? { badge: null } : {};
  })
}));

/* Что уже показано — чтобы окно не всплывало при каждом заходе. */

/**
 * Показано ли уже окно с этим ключом; `maxAgeMs` — через сколько показать снова.
 * Пока не знаем аккаунт — считаем показанным: лучше промолчать, чем показать чужое.
 */
export function funnelSeen(key: string, maxAgeMs?: number): boolean {
  if (!funnelUser) return true;
  try {
    const at = (JSON.parse(localStorage.getItem(scoped(SEEN_KEY)) || '{}') as Record<string, number>)[key];
    if (!at) return false;
    return maxAgeMs === undefined || Date.now() - at < maxAgeMs;
  } catch {
    return false;
  }
}

export function markFunnelSeen(key: string): void {
  if (!funnelUser) return;
  try {
    const all = JSON.parse(localStorage.getItem(scoped(SEEN_KEY)) || '{}') as Record<string, number>;
    all[key] = Date.now();
    localStorage.setItem(scoped(SEEN_KEY), JSON.stringify(all));
  } catch {
    /* приватный режим — просто покажем ещё раз */
  }
}

/*
 * Квиз пропустили (крестик, Esc, «Пропустить»): без памяти он открывался бы на каждом новом
 * батче, пока не пройден. Помним неделю — по ключу аккаунта, как и всё «показанное».
 */
const QUIZ_SKIPPED = 'quiz:skipped';
const QUIZ_SKIP_MAX_AGE_MS = 7 * 24 * 3600 * 1000;

export function markQuizSkipped(): void {
  markFunnelSeen(QUIZ_SKIPPED);
}

export function quizSkippedRecently(): boolean {
  return funnelSeen(QUIZ_SKIPPED, QUIZ_SKIP_MAX_AGE_MS);
}
