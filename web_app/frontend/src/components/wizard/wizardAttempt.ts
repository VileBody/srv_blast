import { create } from 'zustand';

/*
 * «Продолжить» не бывает мёртвым (UI_RULES.md → «Кнопки»): нажатие на неготовом шаге
 * подсвечивает, чего не хватает. Здесь — на каком шаге человек уже пытался пройти дальше;
 * поля шага читают флаг и подсвечивают пропуски, строка действий пишет, чего не хватает.
 */
export const useWizardAttempt = create<{ stage: number | null; message: string; mark: (stage: number, message: string) => void; clear: () => void }>((set) => ({
  stage: null,
  message: '',
  mark: (stage, message) => set({ stage, message }),
  clear: () => set({ stage: null, message: '' })
}));

/** Человек уже жал «Продолжить» на этом шаге, а шаг не был готов. */
export function useTried(stage: number): boolean {
  return useWizardAttempt((state) => state.stage === stage);
}

/*
 * Текст, очищенный из-за сдвига отрывка: строки привязаны к конкретному окну, поэтому при
 * сдвиге они стираются — но с «Вернуть» (вместе с прежним окном), а не тостом постфактум.
 */
export type LyricsUndo = { lyrics: string; fragmentLyrics: string; fragmentEnabled: boolean; timingFrom: string; timingTo: string };
export const useLyricsUndo = create<{ saved: LyricsUndo | null; save: (value: LyricsUndo) => void; drop: () => void }>((set) => ({
  saved: null,
  save: (value) => set({ saved: value }),
  drop: () => set({ saved: null })
}));
