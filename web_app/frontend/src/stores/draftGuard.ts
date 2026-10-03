import { create } from 'zustand';
import { trackTitleOf } from '../components/funnel/useFunnel';
import { stageIndex, useWizardStore } from './wizardStore';

/*
 * Защита черновика визарда от молчаливой замены. «Открыть таймлайн» у батча, «Как исправить»
 * воронки, «Собрать» после безлимита и ссылка из бота подменяют настройку целиком —
 * раньше без вопроса, и полчаса работы над другим треком пропадали одним кликом.
 * Спрашивает DraftReplaceDialog (AppShell и страница ссылки из бота).
 */

/** Куда собираемся: проект и ключ батча (у «открыть батч» — ключ его сабмита). */
export interface DraftTarget {
  projectId: string | null;
  idempotencyKey?: string;
}

/** Что заменится: трек текущей настройки — для текста вопроса. null — терять нечего. */
export function draftAtRisk(target: DraftTarget): { trackTitle: string | null } | null {
  const state = useWizardStore.getState();
  if (!state.track && !state.lyrics.trim()) return null;
  const sameProject = state.projectId === target.projectId;
  // тот же батч — заменять нечем
  if (sameProject && target.idempotencyKey && state.final.idempotencyKey === target.idempotencyKey) return null;
  // Тот же проект, а настройка следующего батча не начата: после сабмита и «ещё батч»
  // в черновике только трек и текст проекта, их и так вернёт открываемый батч.
  if (sameProject && state.reachedIndex <= stageIndex(2)) return null;
  return { trackTitle: trackTitleOf(state.track?.filename) ?? null };
}

interface PendingReplace {
  trackTitle: string | null;
  run: () => void;
  cancel?: () => void;
}

export const useDraftGuard = create<{
  pending: PendingReplace | null;
  ask: (pending: PendingReplace) => void;
  resolve: (replace: boolean) => void;
}>((set, get) => ({
  pending: null,
  ask: (pending) => set({ pending }),
  resolve: (replace) => {
    const pending = get().pending;
    set({ pending: null });
    if (!pending) return;
    if (replace) pending.run();
    else pending.cancel?.();
  }
}));

/** Выполнить замену черновика сразу или после «Заменить» в диалоге. */
export function guardDraft(target: DraftTarget, run: () => void, cancel?: () => void): void {
  const risk = draftAtRisk(target);
  if (!risk) {
    run();
    return;
  }
  useDraftGuard.getState().ask({ trackTitle: risk.trackTitle, run, cancel });
}
