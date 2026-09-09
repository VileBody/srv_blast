import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { SavedTrack } from '../lib/types';
import { DEFAULT_FOOTAGE_TYPE, normalizeFootageType } from '../data/footageTypes';

export type BackgroundMode = 'footage' | 'photo' | 'color';
export type HookKind = 'sound' | 'object' | 'effects' | 'motion' | 'thought';

/** Конфигурация одного хука (Figma W24–W34) */
export interface HookConfig {
  sound?: string;
  soundUrl?: string;
  soundPlaybackUrl?: string;
  object?: string;
  effectHook?: string;
  effectGlue?: string;
  effectStyle?: string;
  motion?: string;
  thought?: string;
}

export const HOOK_LABELS: Record<HookKind, string> = {
  sound: 'Звук',
  object: 'Объект',
  effects: 'Эффекты',
  motion: 'Движение',
  thought: 'Мысль'
};

export interface WizardStateData {
  projectId?: string | null;
  track?: SavedTrack | null;
  lyrics: string;
  fragmentEnabled: boolean;
  fragmentLyrics: string;
  timingMode: 'ai' | 'manual';
  timingFrom: string;
  timingTo: string;
  /** вводные трека перенесены из прошлого батча — визард один раз это проговаривает */
  carriedOverInputs: boolean;
  /**
   * Разделы фона настраиваются параллельно; пилюли пула — производные от настроенности.
   * «+» в футере не коммитит, а переводит к следующему разделу (правка UX).
   */
  background: {
    mode: BackgroundMode;
    footage: string[];
    /**
     * Тип футажей (Figma W12, степпер «‹ Личности ›») — измерение, ортогональное группам:
     * footage[] отвечает «какие группы», footageType — «из какой библиотеки».
     * Хранится стабильный id из data/footage-types.json (НЕ лейбл: отображение переводится
     * через i18n, id уходит в render_job.background.footageType).
     */
    footageType: string;
    /** Свои исходники пользователя (Figma W39/W49) — имена загруженных файлов */
    uploads: string[];
    photo: string[];
    photoEffects: boolean;
    photoStyle?: string;
    color?: string;
    strobe: boolean;
    glue?: string;
  };
  hooks: {
    dropTime?: string;
    kind?: HookKind;
    configs: Partial<Record<HookKind, HookConfig>>;
  };
  subtitles: {
    color: string;
    pool: string[];
  };
  /** Этап «Пул»: распределение вариаций (Figma W19/W33) */
  allocation: {
    total: number;
    background: Record<string, number>;
    subtitles: Record<string, number>;
    hooks: Record<string, number>;
    strobeFont?: string;
    colorFont?: string;
    seeded: boolean;
  };
  final: {
    subtitleColor: string;
    accentColor: string;
    videosToGenerate: number;
    idempotencyKey: string;
  };
}

/** Пилюли фона — производные от настроенных разделов */
export interface BackgroundPill {
  mode: BackgroundMode;
  label: string;
  count: number;
}

export function backgroundPills(bg: WizardStateData['background']): BackgroundPill[] {
  const pills: BackgroundPill[] = [];
  if (bg.footage.length) pills.push({ mode: 'footage', label: 'Футажи', count: bg.footage.length });
  if (bg.photo.length) pills.push({ mode: 'photo', label: 'Фото', count: bg.photo.length });
  if (bg.color) pills.push({ mode: 'color', label: bg.strobe ? 'Строб' : 'Цвет', count: 1 });
  return pills;
}

export function backgroundVariations(bg: WizardStateData['background']): number {
  return bg.footage.length + bg.photo.length + (bg.color ? 1 : 0);
}

/**
 * Хук считается настроенным, когда его конфиг полон.
 *
 * Склейка и стиль нужны ЛЮБОМУ хуку — без них рендер не знает, чем резать и как красить.
 * Раньше их требовали только у «Эффектов», и хук, собранный вне фуллскрина, уезжал в
 * генерацию наполовину пустым.
 */
export function hookComplete(kind: HookKind, config?: HookConfig): boolean {
  if (!config) return false;
  const own = kind === 'sound' ? Boolean(config.sound && config.soundUrl)
    : kind === 'object' ? Boolean(config.object)
      : kind === 'effects' ? Boolean(config.effectHook)
        : kind === 'motion' ? Boolean(config.motion)
          : Boolean(config.thought);
  return own && Boolean(config.effectGlue) && Boolean(config.effectStyle);
}

export function hookPills(hooks: WizardStateData['hooks']): { kind: HookKind; label: string }[] {
  return (Object.keys(HOOK_LABELS) as HookKind[])
    .filter((kind) => hookComplete(kind, hooks.configs[kind]))
    .map((kind) => ({ kind, label: HOOK_LABELS[kind] }));
}

interface WizardStore extends WizardStateData {
  stage: number;
  setStage: (stage: number) => void;
  setProjectId: (projectId?: string | null) => void;
  setTrack: (track: SavedTrack | null) => void;
  setField: <K extends keyof WizardStateData>(key: K, value: WizardStateData[K]) => void;
  setBackground: (patch: Partial<WizardStateData['background']>) => void;
  toggleVibe: (vibe: string) => void;
  setHooks: (patch: Partial<Omit<WizardStateData['hooks'], 'configs'>> & { config?: Partial<HookConfig> }) => void;
  setSubtitles: (patch: Partial<WizardStateData['subtitles']>) => void;
  toggleSubtitleStyle: (style: string) => void;
  setAllocation: (patch: Partial<WizardStateData['allocation']>) => void;
  reset: (projectId?: string | null) => void;
  /** Новый батч по тому же треку: сбрасывает только выбор, вводные трека остаются. */
  newBatch: (projectId?: string | null) => void;
  /** закрыть подсказку «вводные перенесены из прошлого батча» */
  ackCarriedOver: () => void;
  restoreSession: (projectId: string | null | undefined, stage: number, data: Record<string, unknown>) => void;
  stageData: () => Record<string, unknown>;
}

/** Вводные трека заполнены — без них генерировать нечего (это lyric-video). */
export function hasTrackInput(state: Pick<WizardStateData, 'track' | 'lyrics'>): boolean {
  return Boolean(state.track && state.lyrics.trim());
}

const initialData = (projectId?: string | null): WizardStateData => ({
  projectId,
  track: null,
  lyrics: '',
  fragmentEnabled: false,
  fragmentLyrics: '',
  timingMode: 'ai',
  timingFrom: '',
  timingTo: '',
  carriedOverInputs: false,
  background: { mode: 'footage', footage: [], footageType: DEFAULT_FOOTAGE_TYPE, uploads: [], photo: [], photoEffects: false, photoStyle: undefined, color: undefined, strobe: false, glue: undefined },
  hooks: { dropTime: undefined, kind: undefined, configs: {} },
  subtitles: { color: '#f6f5fd', pool: [] },
  allocation: { total: 0, background: {}, subtitles: {}, hooks: {}, strobeFont: undefined, colorFont: undefined, seeded: false },
  final: { subtitleColor: '#ffffff', accentColor: '#8b6fe6', videosToGenerate: 1, idempotencyKey: crypto.randomUUID() }
});

export const useWizardStore = create<WizardStore>()(
  persist(
    (set, get) => ({
      ...initialData(),
      stage: 1,
      setStage: (stage) => set({ stage: Math.max(1, Math.min(5, stage)) }),
      // Смена проекта = смена черновика. Стор персистится и чистится только успешным
      // сабмитом, поэтому без сброса трек и выбор проекта A утекали в проект B.
      setProjectId: (projectId) => set((state) => (
        state.projectId && state.projectId !== projectId
          ? { ...initialData(projectId), stage: 1 }
          : { projectId }
      )),
      setTrack: (track) => set({ track, hooks: initialData().hooks }),
      setField: (key, value) => set({ [key]: value } as Partial<WizardStore>),
      setBackground: (patch) => set((state) => ({ background: { ...state.background, ...patch } })),
      toggleVibe: (vibe) => set((state) => {
        const bg = state.background;
        if (bg.mode === 'color') return state;
        const list = bg.mode === 'footage' ? bg.footage : bg.photo;
        const next = list.includes(vibe) ? list.filter((item) => item !== vibe) : [...list, vibe];
        return { background: { ...bg, [bg.mode]: next } };
      }),
      setHooks: (patch) => set((state) => {
        const { config, ...rest } = patch;
        const kind = rest.kind ?? state.hooks.kind;
        const configs = config && kind
          ? { ...state.hooks.configs, [kind]: { ...state.hooks.configs[kind], ...config } }
          : state.hooks.configs;
        return { hooks: { ...state.hooks, ...rest, configs } };
      }),
      setSubtitles: (patch) => set((state) => ({ subtitles: { ...state.subtitles, ...patch } })),
      toggleSubtitleStyle: (style) => set((state) => {
        const pool = state.subtitles.pool.includes(style)
          ? state.subtitles.pool.filter((item) => item !== style)
          : [...state.subtitles.pool, style];
        return { subtitles: { ...state.subtitles, pool } };
      }),
      setAllocation: (patch) => set((state) => ({ allocation: { ...state.allocation, ...patch } })),
      reset: (projectId) => set({ ...initialData(projectId), stage: 1 }),
      newBatch: (projectId) => set((state) => {
        // Трек/текст/тайминг — это вводные проекта, а не батча: переспрашивать их незачем.
        // Всё остальное (фон, хуки, субтитры, распределение) собирается заново.
        const fresh = initialData(projectId);
        const carried = hasTrackInput(state);
        return {
          ...fresh,
          track: state.track,
          lyrics: state.lyrics,
          fragmentEnabled: state.fragmentEnabled,
          fragmentLyrics: state.fragmentLyrics,
          timingMode: state.timingMode,
          timingFrom: state.timingFrom,
          timingTo: state.timingTo,
          // Человеку надо СКАЗАТЬ, что вводные переехали из прошлого батча, и дать
          // их поменять — иначе он либо не заметит подмену, либо решит, что визард
          // потерял шаг. Флаг разовый и в localStorage не уезжает.
          carriedOverInputs: carried,
          stage: 1
        };
      }),
      ackCarriedOver: () => set({ carriedOverInputs: false }),
      restoreSession: (projectId, stage, raw) => set((state) => {
        // Browser state is newer and wins. The server copy is for a cleared
        // browser or a second device, not for overwriting an active draft.
        if (state.track || state.lyrics.trim()) return state;
        const fresh = initialData(projectId);
        const timing = (raw.timing ?? {}) as Record<string, unknown>;
        const fragment = typeof raw.fragment === 'string' ? raw.fragment : '';
        return {
          ...fresh,
          projectId,
          track: (raw.track as SavedTrack | null | undefined) ?? null,
          lyrics: typeof raw.lyrics === 'string' ? raw.lyrics : '',
          fragmentEnabled: Boolean(fragment),
          fragmentLyrics: fragment,
          timingMode: timing.mode === 'ai' ? 'ai' : 'manual',
          timingFrom: typeof timing.from === 'string' ? timing.from : '',
          timingTo: typeof timing.to === 'string' ? timing.to : '',
          background: (() => {
            const merged = { ...fresh.background, ...((raw.background as Partial<WizardStateData['background']>) ?? {}) };
            // Черновик мог быть сохранён на прошлой версии реестра типов футажей
            // (standard/persons/movies). Приводим здесь, иначе id уедет в render_job
            // как есть и подбор не найдёт такой план.
            merged.footageType = normalizeFootageType(merged.footageType);
            return merged;
          })(),
          hooks: { ...fresh.hooks, ...((raw.hooks as Partial<WizardStateData['hooks']>) ?? {}) },
          subtitles: { ...fresh.subtitles, ...((raw.subtitles as Partial<WizardStateData['subtitles']>) ?? {}) },
          allocation: { ...fresh.allocation, ...((raw.allocation as Partial<WizardStateData['allocation']>) ?? {}) },
          final: { ...fresh.final, ...((raw.final as Partial<WizardStateData['final']>) ?? {}) },
          stage: Math.max(1, Math.min(5, Number(stage) || 1))
        };
      }),
      stageData: () => {
        const state = get();
        return {
          track: state.track,
          lyrics: state.lyrics,
          fragment: state.fragmentEnabled ? state.fragmentLyrics : null,
          timing: state.timingMode === 'manual' ? { from: state.timingFrom, to: state.timingTo } : { mode: 'ai' },
          background: state.background,
          hooks: state.hooks,
          subtitles: state.subtitles,
          allocation: state.allocation,
          final: state.final
        };
      }
    }),
    {
      name: 'blast-wizard-v4',
      partialize: (state) => ({
        projectId: state.projectId,
        track: state.track,
        lyrics: state.lyrics,
        fragmentEnabled: state.fragmentEnabled,
        fragmentLyrics: state.fragmentLyrics,
        timingMode: state.timingMode,
        timingFrom: state.timingFrom,
        timingTo: state.timingTo,
        background: state.background,
        hooks: state.hooks,
        subtitles: state.subtitles,
        allocation: state.allocation,
        final: state.final,
        stage: state.stage
      })
    }
  )
);
