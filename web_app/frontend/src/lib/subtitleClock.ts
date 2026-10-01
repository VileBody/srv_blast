import { create } from 'zustand';

/**
 * Общие часы примерки субтитров: плеер «Проверки субтитров» (SubtitleTimeline) публикует
 * сюда время отрывка, превью справа рисует по нему кадр. Кнопка плея в превью дёргает
 * плеер таймлайна через toggle — звук один, источник времени один.
 */
interface SubtitleClock {
  /** абсолютные секунды трека; null — плеер не трогали (превью показывает первую фразу) */
  time: number | null;
  playing: boolean;
  toggle: (() => void) | null;
  publish: (patch: Partial<Omit<SubtitleClock, 'publish'>>) => void;
}

export const useSubtitleClock = create<SubtitleClock>((set) => ({
  time: null,
  playing: false,
  toggle: null,
  publish: (patch) => set(patch),
}));
