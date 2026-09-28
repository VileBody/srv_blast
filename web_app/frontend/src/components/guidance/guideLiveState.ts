import { create } from 'zustand';

/**
 * ЖИВОЕ (не персистентное) состояние «этот гайд сейчас dismissed» — на случай,
 * когда шаг N зависит от того, закрыли ли шаг N−1, а живут они в СОСЕДНИХ
 * компонентах без общего доступа к пропсам (например, track-timing в
 * StageOne и text-lyrics в соседнем TextPanel — оба на этапе «Трек», но
 * StageOne не передаёт TextPanel пропсы напрямую). guideMemory даёт только
 * «видел когда-либо» (персистентно, обновляется почти сразу при показе) —
 * этого мало: нужно именно «закрыли ПРЯМО СЕЙЧАС», иначе оба гайда всплывают
 * одновременно на одном экране.
 */
interface GuideLiveState {
  dismissed: Record<string, boolean>;
  setDismissed: (id: string, value: boolean) => void;
}

export const useGuideLiveStore = create<GuideLiveState>((set) => ({
  dismissed: {},
  setDismissed: (id, value) => set((state) => (
    state.dismissed[id] === value ? state : { dismissed: { ...state.dismissed, [id]: value } }
  ))
}));

export function useGuideLiveDismissed(id: string): boolean {
  return useGuideLiveStore((state) => state.dismissed[id] ?? false);
}
