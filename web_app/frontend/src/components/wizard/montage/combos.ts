/* Ролики батча — ровно та раскладка, что считает «Пул» (combinationAt): стол и раскадровка
   говорят про одни и те же видео с одними и теми же номерами. */
import { useMemo } from 'react';
import { combosOf, type Combo } from '../SlicePanel';
import { useWizardStore } from '../../../stores/wizardStore';

export type { Combo };

export function useCombos(): Combo[] {
  const background = useWizardStore((s) => s.background);
  const allocation = useWizardStore((s) => s.allocation);
  const fxVariants = useWizardStore((s) => s.fxVariants);
  const subtitles = useWizardStore((s) => s.subtitles);
  return useMemo(() => combosOf({ background, allocation, fxVariants, subtitles }), [background, allocation, fxVariants, subtitles]);
}
