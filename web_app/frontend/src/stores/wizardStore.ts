import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { SavedTrack } from '../lib/types';
import { DEFAULT_FOOTAGE_TYPE, footageTypePlane, normalizeFootageType } from '../data/footageTypes';

export type BackgroundMode = 'footage' | 'photo' | 'color';
export type HookKind = 'warmup' | 'object' | 'effects' | 'motion' | 'thought' | 'none';

export interface SourceVideoPlan {
  id: string;
  format: '9:16' | '16:9';
  sourceIds: string[];
}

/** Конфигурация одного хука (Figma W24–W34) */
export interface HookConfig {
  warmupKind?: 'audio' | 'video';
  soundDuration?: number;
  videoUrl?: string;
  videoWidth?: number;
  videoHeight?: number;
  videoDuration?: number;
  videoHasAudio?: boolean;
  sound?: string;
  soundUrl?: string;
  soundPlaybackUrl?: string;
  object?: string;
  effectHook?: string;
  /** Длина echo-шлейфа slow shutter: штатная, до конца или три футажа после дропа. */
  effectHookExtend?: '' | 'to_end' | 'after_drop:3';
  effectGlue?: string;
  effectStyle?: string;
  /** Выбранные стилизации образуют отдельное измерение распределения в Пуле. */
  effectStyles?: string[];
  /** Грейд на весь ролик, а не только до дропа (manifest: effect_extra_full). */
  effectStyleFull?: boolean;
  motion?: string;
  thought?: string;
}

export const HOOK_LABELS: Record<HookKind, string> = {
  warmup: 'Прогрев',
  object: 'Объект',
  effects: 'Эффекты',
  motion: 'Движение',
  thought: 'Мысль',
  none: 'Без хука'
};

/** Слово из примерки субтитров (ASR отрывка), тайминги — абсолютные секунды трека */
export interface AsrWord {
  text: string;
  tStart: number;
  tEnd: number;
  /** помечено автором как фокусное — Stage2 обязан сделать его акцентом сцены */
  focus?: boolean;
  /** выравниватель не уверен в слове — подсветить и попросить проверить текст/окно */
  weak?: boolean;
}

export type AsrStatus = 'IDLE' | 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED';

export type SubtitleFocusStyle = 'italic' | 'bold_italic' | 'faux_italic';

/**
 * Визуальные параметры текста поверх выбранного стиля субтитров. Имена пресетов —
 * числа живут на рендере (app/subtitle_font_layout.py); шрифты и пары — каталог
 * рендера (GET /api/wizard/subtitle-fonts). Бэк приводит настройки к стилю каждой
 * вариации и отклоняет то, что стиль не примет (docs/WIZARD_SUBTITLE_CUSTOMIZATION.md).
 */
export interface SubtitleTextSettings {
  /** PostScript-имя из каталога; null — стандартный шрифт стиля (прод) */
  font: string | null;
  /** акцентный шрифт пары для фокус-слов; null — без пары */
  accentFont: string | null;
  size: 'small' | 'medium' | 'large';
  /** растяжение букв по высоте — только шрифтам с засечками */
  height: 'compact' | 'normal' | 'tall';
  shadow: 'none' | 'soft' | 'strong';
  // 'down' — только для 16:9 (рендер отклоняет его для вертикали). Обводки нет:
  // смотр в AE 2026-09-29 — нигде не выглядит хорошо.
  position: 'left' | 'center' | 'right' | 'down';
  /** второй (и последний) цвет кадра: фокус/ударное слово; null — цвета стиля */
  accentColor: string | null;
  /** brat: фокус-слово курсивом (шрифт brat зафиксирован) */
  focusStyle: SubtitleFocusStyle | null;
}

export const DEFAULT_SUBTITLE_TEXT_SETTINGS: SubtitleTextSettings = {
  // large = авто-максимум (прод); medium/small — только меньше
  font: null, accentFont: null, size: 'large', height: 'normal',
  shadow: 'soft', position: 'center', accentColor: null, focusStyle: null
};

/** Стиль, который сейчас настраивается: выбранная вкладка, если она ещё в пуле, иначе первый в пуле. */
export function activeTextTab(subtitles: Pick<WizardStateData['subtitles'], 'pool' | 'textTab'>): string | null {
  return subtitles.textTab && subtitles.pool.includes(subtitles.textTab) ? subtitles.textTab : subtitles.pool[0] ?? null;
}

/** Настройки текста стиля: сохранённые поверх стандартных. */
export function textSettingsFor(subtitles: Pick<WizardStateData['subtitles'], 'textByStyle'>, style: string | null | undefined): SubtitleTextSettings {
  return { ...DEFAULT_SUBTITLE_TEXT_SETTINGS, ...((style && subtitles.textByStyle?.[style]) || {}) };
}

function normalizeTextByStyle(raw: unknown): Record<string, SubtitleTextSettings> {
  if (!raw || typeof raw !== 'object') return {};
  return Object.fromEntries(Object.entries(raw as Record<string, Partial<SubtitleTextSettings>>)
    .map(([style, value]) => [style, { ...DEFAULT_SUBTITLE_TEXT_SETTINGS, ...(value ?? {}) }]));
}

/** Все фоны батча дают 16:9 — только тогда доступна позиция «снизу». */
export function allBackgroundsWide(background: WizardStateData['background']): boolean {
  // все разделы фона сразу — как backgroundVariations(): батч разворачивает их все,
  // независимо от того, какая вкладка фона открыта сейчас
  const formats: string[] = [];
  for (const group of background.footage) {
    formats.push(background.footageFormats?.[group] ?? (background.footageType === 'cine16x9' ? '16:9' : '9:16'));
  }
  for (const plan of background.sourceVideos ?? []) formats.push(plan.format);
  for (let i = 0; i < background.photo.length; i++) formats.push('4:3');
  if (background.color) formats.push('9:16');
  return formats.length > 0 && formats.every((format) => format === '16:9');
}

/**
 * Примерка субтитров: ASR отрывка запускается сразу после шага «Трек», к шагу «Текст»
 * слова лежат на таймлайне и их можно подвинуть / пометить фокусными.
 * `key` — трек+окно+текст (считает бэк): другие вводные → другая примерка, правки
 * старой не переносятся (рендер сверяет окно и текст и пересчитал бы ASR сам).
 * `source` — как пришло из ASR (для «сбросить»), `words` — рабочая копия с правками.
 */
export interface AsrPreviewState {
  key: string;
  jobId: string | null;
  status: AsrStatus;
  words: AsrWord[];
  source: AsrWord[];
  clipStart: number | null;
  clipEnd: number | null;
  edited: boolean;
  error: string | null;
  /** предупреждения примерки (window_clamped: трек кончился раньше конца окна) */
  notes: string[];
  workingEnd: number | null;
}

/** Частота склеек: «авто» — ровно разбиение рендера по темпу трека, остальные — от той же сетки битов. */
export type TimelinePace = 'sparse' | 'auto' | 'dense';

export interface TimelineStyleRange {
  uid: number;
  /** подпись стилизации из effects-registry (как в hooks.configs.effectStyles) */
  style: string;
  lane: 0 | 1;
  /** диапазон кадров [a, b) — стиль всегда лежит по границам склеек */
  a: number;
  b: number;
}

/** Рецепт ролика с таймлайна FX: общий для всех видео батча. */
export interface TimelineRecipe {
  /** вводные, под которые посчитаны склейки (трек + окно + дроп) */
  key: string;
  pace: TimelinePace;
  /** абсолютные секунды трека; null — склейки ещё не пришли с бэка */
  cuts: number[] | null;
  /** склейки двигали руками: смена вводных или темпа их пересчитает */
  edited: boolean;
  /** индекс склейки → подпись перехода из effects-registry */
  transitions: Record<number, string>;
  styles: TimelineStyleRange[];
}

export interface StoryboardClip {
  fileName: string;
  inPoint: number;
  outPoint: number;
  previewUrl: string | null;
  previewOffset: number;
  tags: string[];
}

/** Одно видео батча в раскадровке «Пула»: реальные клипы по склейкам рецепта. */
export interface StoryboardVideo {
  index: number;
  group: string;
  seedKey: string;
  clips: StoryboardClip[];
  /** кадры, где вайбу не хватило свежих клипов и клип повторяется из другого видео */
  repeats: number[];
  /** кадр → закреплённый клип (замена руками) */
  pins: Record<number, string>;
  /** закреплённый план — уходит в рендер как footage_plan */
  plan: Record<string, unknown>;
}

export interface StoryboardState {
  /** вводные подбора: окно + склейки + раскладка вайбов по видео */
  key: string;
  videos: Record<number, StoryboardVideo>;
}

/**
 * Вводные, под которые посчитаны склейки рецепта: трек + окно + дроп. Одна функция
 * и для таймлайна, и для отправки черновика — склейки под другие вводные в рендер
 * не уходят (их сервер примет за «ещё считаются» и остановит генерацию).
 */
export function recipeKeyOf(s: { track?: { id?: string | number } | null; timingFrom: string; timingTo: string; hooks: { dropTime?: string } }): string {
  const raw = s.hooks.dropTime ?? '';
  const drop = /^\d{2}:\d{2}$/.test(raw) ? `${raw}:00` : raw;
  return [s.track?.id ?? '', s.timingFrom, s.timingTo, drop].join('|');
}

/**
 * Режим вариантов FX — поведение по умолчанию. `?fxLab=0` — аварийный откат на классический
 * шаг FX (hooks.configs); флаг липкий на вкладку: навигация визарда переписывает адрес и
 * теряет параметр. `?fxLab=1` снимает откат. Нужен и вне React: stageData собирается в сторе.
 */
export function fxVariantsMode(): boolean {
  if (typeof window === 'undefined') return true;
  const param = new URLSearchParams(window.location.search).get('fxLab');
  try {
    if (param === '0') window.sessionStorage.setItem('fxLab', '0');
    else if (param !== null) window.sessionStorage.removeItem('fxLab');
    return window.sessionStorage.getItem('fxLab') !== '0';
  } catch {
    return param !== '0';
  }
}

/**
 * Черновик, собранный в классическом шаге FX (hooks.configs), — в варианты: по варианту на
 * каждый стиль типа (раньше стили у типа копились списком и делились в «Пуле» отдельно).
 * Недонастроенные конфиги тоже переезжают — на шаге FX у них будет метка «настроить».
 */
export function variantsFromLegacyHooks(hooks: WizardStateData['hooks']): FxVariant[] {
  const out: FxVariant[] = [];
  for (const kind of Object.keys(HOOK_LABELS) as HookKind[]) {
    const config = hooks.configs[kind];
    if (!config) continue;
    const styles = config.effectStyles?.length ? config.effectStyles : (config.effectStyle ? [config.effectStyle] : []);
    for (const style of styles.length ? styles : [undefined]) {
      out.push({
        id: `v-${kind}-${out.length + 1}-${Date.now().toString(36)}`,
        kind,
        config: { ...config, effectStyles: style ? [style] : [], effectStyle: style },
        color: FX_VARIANT_PALETTE[out.length % FX_VARIANT_PALETTE.length]
      });
    }
  }
  return out;
}

export const emptyTimeline = (): TimelineRecipe => ({ key: '', pace: 'auto', cuts: null, edited: false, transitions: {}, styles: [] });
export const emptyStoryboard = (): StoryboardState => ({ key: '', videos: {} });

/**
 * Правки монтажного стола — по роликам батча. Стол открывается с «Пула» и правит ролик
 * целиком: хук, склейка по умолчанию, переходы на стыках, стили по кадрам, стиль субтитров.
 * `sig` — комбинация ролика (фон · субтитры · вариант FX), под которую сделаны правки: если
 * распределение «Пула» поменяло ролику комбинацию, его правки больше не про него.
 */
export interface MontageVideo {
  sig: string;
  kind: HookKind;
  config: HookConfig;
  /** индекс склейки → подпись перехода; не указано — склейка ролика по умолчанию */
  transitions: Record<number, string>;
  styles: TimelineStyleRange[];
  sub?: string;
  /** рамка ролика (id каталога рамок) — PNG-маска поверх всех слоёв, как шаг «Рамка» в боте */
  frame?: string | null;
  edited: boolean;
}

export interface MontageState {
  videos: Record<number, MontageVideo>;
}

export const emptyMontage = (): MontageState => ({ videos: {} });

/**
 * Вариант FX (шаг FX в режиме вариантов): тип хука + его настройка (хук · склейка · ОДИН
 * стиль). У одного типа может быть несколько вариантов — поэтому это отдельный список, а не
 * hooks.configs[kind]. «Пул» раздаёт ролики по вариантам (allocation.variants), бэк
 * разворачивает их в вариации рендера. recipe — переходы/стили варианта на таймлайне
 * (склейки и темп общие на батч и живут в timeline); draft — создан из таймлайна, хук ещё
 * не выбран: в пул и на бэк не идёт.
 */
/** Цвета вариантов FX: точка варианта в списке, пилюлях и «Комбинациях». */
export const FX_VARIANT_PALETTE = ['#8b6fe6', '#e38fb5', '#6fc7c0', '#e8b45f', '#9fb5ff', '#b7e27a', '#ff9a7a', '#d6a1ff'];

export interface FxVariant { id: string; kind: HookKind; config: HookConfig; color: string; recipe?: TimelineRecipe; draft?: boolean }

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
  /** Максимальный пройденный этап как индекс в STAGE_ORDER. */
  reachedIndex: number;
  /**
   * Разделы фона настраиваются параллельно; пилюли пула — производные от настроенности.
   * «+» в футере не коммитит, а переводит к следующему разделу (правка UX).
   */
  background: {
    mode: BackgroundMode;
    footage: string[];
    footageFormats?: Record<string, string>;
    /**
     * План подборки (vibes / cine16x9 / films), из которой взят каждый футаж. Выпадающий
     * список типа — только какая подборка открыта сейчас: выбрал вайбы, переключился на
     * «Фильмы» — вайбы остаются вайбами (раскадровка, подпись в «Пуле»).
     */
    footagePlanes?: Record<string, string>;
    sourceFormat?: string;
    /**
     * Тип футажей (Figma W12, степпер «‹ Личности ›») — измерение, ортогональное группам:
     * footage[] отвечает «какие группы», footageType — «из какой библиотеки».
     * Хранится стабильный id из data/footage-types.json (НЕ лейбл: отображение переводится
     * через i18n, id уходит в render_job.background.footageType).
     */
    footageType: string;
    /** Свои исходники пользователя (Figma W39/W49) — имена загруженных файлов */
    uploads: string[];
    /** Один пункт = одно будущее видео; sourceIds задают порядок клипов в монтаже. */
    sourceVideos: SourceVideoPlan[];
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
    /**
     * Настройки текста ПО СТИЛЮ (ключ — имя стиля из пула, «Jakson»…): у стилей
     * разные ограничения (скрипт основным не годится для Jakson, у Brat шрифт
     * зафиксирован) — общие настройки блокировали бы друг друга. Нет записи —
     * стандартные настройки стиля (прод).
     */
    textByStyle: Record<string, SubtitleTextSettings>;
    /** какой стиль сейчас настраивается (вкладка блока «Настройки текста» и превью) */
    textTab: string | null;
  };
  /** Варианты FX (режим вариантов шага FX); пусто — классический hooks.configs. */
  fxVariants: FxVariant[];
  /** Этап «Пул»: распределение вариаций (Figma W19/W33) */
  allocation: {
    total: number;
    background: Record<string, number>;
    subtitles: Record<string, number>;
    hooks: Record<string, number>;
    styles: Record<string, number>;
    /** id варианта FX → число роликов (режим вариантов) */
    variants: Record<string, number>;
    strobeFont?: string;
    colorFont?: string;
    seeded: boolean;
  };
  asr: AsrPreviewState;
  timeline: TimelineRecipe;
  storyboard: StoryboardState;
  montage: MontageState;
  final: {
    subtitleColor: string;
    accentColor: string;
    videosToGenerate: number;
    idempotencyKey: string;
  };
}

/** Explicit migration of browser and server drafts from the old sound family. */
export function migrateHooks(raw: Record<string, any>): WizardStateData['hooks'] {
  const configs = { ...(raw.configs ?? {}) };
  if (configs.sound) {
    configs.warmup = configs.warmup ?? { ...configs.sound, warmupKind: 'audio' };
    delete configs.sound;
  }
  for (const [kind, value] of Object.entries(configs)) {
    const config = { ...((value as HookConfig | undefined) ?? {}) };
    if (!config.effectStyles?.length && config.effectStyle) config.effectStyles = [config.effectStyle];
    configs[kind] = config;
  }
  return { ...raw, kind: raw.kind === 'sound' ? 'warmup' : raw.kind, configs };
}

/** Пилюли фона — производные от настроенных разделов */
export interface BackgroundPill {
  /** Stable UI key; mode alone is not unique when own videos and library footage coexist. */
  key: string;
  mode: BackgroundMode;
  label: string;
  count: number;
}

export function backgroundPills(bg: WizardStateData['background']): BackgroundPill[] {
  const pills: BackgroundPill[] = [];
  if (bg.sourceVideos.length) pills.push({ key: 'uploads', mode: 'footage', label: 'Свои видео', count: bg.sourceVideos.length });
  if (bg.footage.length) pills.push({ key: 'footage', mode: 'footage', label: 'Футажи', count: bg.footage.length });
  if (bg.photo.length) pills.push({ key: 'photo', mode: 'photo', label: 'Фото', count: bg.photo.length });
  if (bg.color) pills.push({ key: 'color', mode: 'color', label: bg.strobe ? 'Строб' : 'Цвет', count: 1 });
  return pills;
}

export function backgroundVariations(bg: WizardStateData['background']): number {
  return bg.sourceVideos.length + bg.footage.length + bg.photo.length + (bg.color ? 1 : 0);
}

/**
 * План подборки конкретного футажа. Черновики до footagePlanes плана не помнят: 16:9 —
 * это коллекции, остальное — подборка, открытая в черновике (так считалось и раньше).
 */
export function footagePlaneOf(bg: WizardStateData['background'], group: string): string {
  const recorded = bg.footagePlanes?.[group];
  if (recorded) return recorded;
  if (bg.footageFormats?.[group] === '16:9') return 'cine16x9';
  return footageTypePlane(bg.footageType);
}

const FOOTAGE_UNIT_LABEL: Record<string, string> = {
  vibes: 'wizard.pool.vibeUnit',
  films: 'wizard.track.poolFilmUnit',
  cine16x9: 'wizard.track.poolCollectionUnit'
};

/** Ключ юнита — стабильный (идёт в allocation), подпись собирается через i18n при рендере. */
export function backgroundUnits(bg: WizardStateData['background']): { key: string; labelKey: string; name: string; icon: 'tag' | 'photo'; noHook: boolean; plane?: string }[] {
  return [
    ...bg.sourceVideos.map((plan, index) => ({ key: `upload:${plan.id}`, labelKey: 'wizard.pool.ownVideoUnit', name: `${index + 1} · ${plan.format}`, icon: 'tag' as const, noHook: plan.format === '16:9' })),
    ...bg.footage.map((vibe) => {
      const plane = footagePlaneOf(bg, vibe);
      return {
        key: `footage:${vibe}`,
        // подпись — по подборке самого футажа: фильм не «Вайб»
        labelKey: FOOTAGE_UNIT_LABEL[plane] ?? 'wizard.pool.vibeUnit',
        name: vibe,
        icon: 'tag' as const,
        noHook: (bg.footageFormats?.[vibe] ?? (bg.footageType === 'cine16x9' ? '16:9' : '9:16')) === '16:9',
        plane
      };
    }),
    ...bg.photo.map((vibe) => ({ key: `photo:${vibe}`, labelKey: 'wizard.pool.photoUnit', name: vibe, icon: 'photo' as const, noHook: true }))
  ];
}

export function combinationAt(
  index: number,
  bg: [string, number][],
  subs: [string, number][],
  hooks: [string, number][],
  styles: [string, number][],
  units: ReturnType<typeof backgroundUnits>,
  hasColor: boolean,
  colorStyle?: string
): { bg?: string; sub?: string; hook?: string; style?: string } {
  const expand = (pairs: [string, number][]) => pairs.flatMap(([key, count]) => Array.from({ length: count }, () => key));
  const bgList = expand(bg);
  if (hasColor) bgList.push('__color__');
  const subList = expand(subs);
  const hookList = expand(hooks);
  const styleList = expand(styles);
  const bgKey = bgList[index];
  const unit = units.find((candidate) => candidate.key === bgKey);
  const hookAllowed = Boolean(unit && !unit.noHook);
  const nonColorIndex = bgList.slice(0, index).filter((key) => key !== '__color__').length;
  const hookIndex = bgList.slice(0, index).filter((key) => {
    const previous = units.find((candidate) => candidate.key === key);
    return previous && !previous.noHook;
  }).length;
  return {
    bg: bgKey,
    sub: bgKey === '__color__' ? colorStyle : subList[nonColorIndex],
    hook: hookAllowed ? hookList[hookIndex] : undefined,
    style: hookAllowed ? styleList[hookIndex] : undefined
  };
}

/* ── ролики батча: номер, фон, стиль субтитров, вариант FX — общая раскладка «Пула» и стола ── */
export interface Combo {
  index: number;
  /** номер видео в раскадровке и в рендере (index + 1) */
  slotIndex: number;
  /** вайб футажа — у фото, цвета и своих видео его нет */
  group?: string;
  /** ключ фона из распределения «Пула»: footage:…, photo:…, upload:…, __color__ */
  bgKey?: string;
  bgLabel: string;
  sub?: string;
  variant?: FxVariant;
  /** хук возможен: вертикальное видео (не фото, не цвет, не 16:9) — как hook_allowed рендера */
  hookAllowed: boolean;
  /** выход 9:16 (всё, кроме 16:9-футажа и своего видео 16:9) — на нём встаёт рамка */
  vertical: boolean;
  /** комбинация целиком — под неё сделаны правки стола (см. renderSigOf) */
  sig: string;
}

/** Стабильная строка значения: ключи объектов по алфавиту — порядок полей не меняет хэш. */
function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value as Record<string, unknown>).sort()
      .filter((key) => (value as Record<string, unknown>)[key] !== undefined)
      .map((key) => `${JSON.stringify(key)}:${stableJson((value as Record<string, unknown>)[key])}`).join(',')}}`;
  }
  return JSON.stringify(value ?? null);
}

/** Хэш настройки варианта FX (FNV-1a): правки стола сделаны под конкретные хук/склейку/стиль. */
export function variantConfigHash(variant: Pick<FxVariant, 'kind' | 'config'>): string {
  const text = stableJson({ kind: variant.kind, config: variant.config });
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(36);
}

/**
 * Подпись для рендера: бэк (render_job → montage.py) сверяет только фон · субтитры · id
 * варианта. Хэш настройки варианта — фронтовый: по нему правки, сделанные под старую
 * настройку, не уезжают в генерацию.
 */
export function renderSigOf(sig: string): string {
  return sig.replace(/#[^|#]*$/, '');
}

/** Ролики батча по распределению — та же раскладка, что у «Комбинаций» и рендера. */
export function combosOf(state: Pick<WizardStateData, 'background' | 'allocation' | 'fxVariants' | 'subtitles'>): Combo[] {
  const { background, allocation: alloc, fxVariants, subtitles } = state;
  const units = backgroundUnits(background);
  const live = fxVariants.filter((v) => !v.draft);
  const hookEntries: [string, number][] = live.map((v) => [v.id, alloc.variants?.[v.id] ?? 0]);
  const colorStyle = background.color ? (background.strobe ? alloc.strobeFont : alloc.colorFont) ?? subtitles.pool[0] : undefined;
  const total = Math.max(1, alloc.total);
  return Array.from({ length: total }, (_, i) => {
    const c = combinationAt(i, Object.entries(alloc.background), Object.entries(alloc.subtitles), hookEntries, [], units, Boolean(background.color), colorStyle);
    const unit = units.find((u) => u.key === c.bg);
    const variant = live.find((v) => v.id === c.hook);
    return {
      index: i,
      slotIndex: i + 1,
      group: c.bg?.startsWith('footage:') ? c.bg.slice('footage:'.length) : undefined,
      bgKey: c.bg,
      bgLabel: c.bg === '__color__' ? (background.strobe ? 'Строб' : 'Цвет') : unit?.name ?? c.bg?.split(':')[1] ?? '—',
      sub: c.sub,
      variant,
      // хук рендер ставит только на вертикальное видео (зеркало hook_allowed в render_job)
      hookAllowed: Boolean(unit && !unit.noHook),
      vertical: c.bg === '__color__' || Boolean(c.bg?.startsWith('photo:')) || Boolean(unit && !unit.noHook),
      // поменяли вариант на FX (хук/склейка/стиль) — подпись другая, старые правки не про него
      sig: [c.bg ?? '', c.sub ?? '', variant?.id ?? ''].join('|') + (variant ? `#${variantConfigHash(variant)}` : '')
    };
  });
}

/** Номера (с нуля) правленых роликов, чьи правки больше не про их комбинацию. */
export function staleMontageEdits(state: Pick<WizardStateData, 'background' | 'allocation' | 'fxVariants' | 'subtitles' | 'montage'>): number[] {
  const combos = combosOf(state);
  return Object.entries(state.montage.videos)
    .filter(([index, video]) => video.edited && combos[Number(index)]?.sig !== video.sig)
    .map(([index]) => Number(index));
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
  if (kind === 'none') return Boolean(config.effectGlue);
  const own = kind === 'warmup' ? Boolean(config.sound && (config.warmupKind === 'video' ? config.videoUrl && config.videoWidth && config.videoHeight && config.videoDuration : config.soundUrl))
    : kind === 'object' ? Boolean(config.object)
      : kind === 'effects' ? Boolean(config.effectHook)
        : kind === 'motion' ? Boolean(config.motion)
          : Boolean(config.thought);
  return own && Boolean(config.effectGlue) && Boolean(config.effectStyles?.length || config.effectStyle);
}

/** Уникальный список стилизаций из всех настроенных типов хука, в порядке выбора. */
export function selectedEffectStyles(hooks: WizardStateData['hooks']): string[] {
  const result: string[] = [];
  for (const kind of Object.keys(HOOK_LABELS) as HookKind[]) {
    const config = hooks.configs[kind];
    for (const style of config?.effectStyles?.length ? config.effectStyles : (config?.effectStyle ? [config.effectStyle] : [])) {
      if (!result.includes(style)) result.push(style);
    }
  }
  return result;
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
  toggleVibe: (vibe: string, format?: string) => void;
  setHooks: (patch: Partial<Omit<WizardStateData['hooks'], 'configs'>> & { config?: Partial<HookConfig> }) => void;
  /** Снять хук: стереть его конфиг и, если он был открыт, закрыть рабочую зону. */
  clearHook: (kind: HookKind) => void;
  setSubtitles: (patch: Partial<WizardStateData['subtitles']>) => void;
  toggleSubtitleStyle: (style: string) => void;
  setAllocation: (patch: Partial<WizardStateData['allocation']>) => void;
  /** результат/статус примерки с бэка; правки для того же key сохраняются */
  setAsrResult: (asr: { key: string; jobId: string | null; status: AsrStatus; words: AsrWord[]; clipStart: number | null; clipEnd: number | null; error: string | null; notes?: string[]; workingEnd?: number | null }) => void;
  /** сдвинуть слово: новые тайминги (уже провалидированные таймлайном) */
  setAsrWord: (index: number, patch: Partial<Pick<AsrWord, 'tStart' | 'tEnd'>>) => void;
  toggleAsrFocus: (index: number) => void;
  /** вернуть слова из ASR как есть */
  resetAsrEdits: () => void;
  setTimeline: (patch: Partial<TimelineRecipe>) => void;
  setStoryboard: (next: StoryboardState) => void;
  setStoryboardVideo: (video: StoryboardVideo) => void;
  /** правки стола: заменить набор роликов целиком или поправить часть */
  setMontage: (patch: Partial<MontageState>) => void;
  patchMontageVideos: (indices: number[], fn: (video: MontageVideo, index: number) => MontageVideo) => void;
  reset: (projectId?: string | null) => void;
  /** Новый батч по тому же треку: сбрасывает только выбор, вводные трека остаются. */
  newBatch: (projectId?: string | null) => void;
  /** закрыть подсказку «вводные перенесены из прошлого батча» */
  ackCarriedOver: () => void;
  restoreSession: (projectId: string | null | undefined, stage: number, data: Record<string, unknown>) => void;
  /**
   * «Докрутить на сайте»: черновик целиком из ролика бота (stores/wizardImport.ts) — сразу
   * на «Пуле» со всеми пройденными шагами, монтажный стол откроется сам (openTableOnLoad).
   */
  importEdit: (data: Partial<WizardStateData>) => void;
  /**
   * «Открыть таймлайн» у готового батча: черновик целиком из job.stageData (то, что визард
   * отправил в сабмит) — сразу на «Пуле» с открытым монтажным столом. Раскадровку
   * дособирает stores/reopenJob.ts.
   */
  reopenFromJob: (projectId: string, data: Record<string, unknown>) => void;
  /** разовый флаг: WizardPage открывает монтажный стол и сбрасывает его; в localStorage не едет */
  openTableOnLoad: boolean;
  consumeOpenTable: () => void;
  stageData: () => Record<string, unknown>;
}

/**
 * Порядок прохождения визарда: Трек → Фон → Текст → Хук → Пул. Живёт здесь, а не в
 * странице: по нему считают и «Продолжить/Назад», и доступность табов.
 */
export const STAGE_ORDER: number[] = [1, 2, 4, 3, 5];

export function stageIndex(stage: number): number {
  const index = STAGE_ORDER.indexOf(stage);
  return index < 0 ? 0 : index;
}

/** Вводные трека заполнены — без них генерировать нечего (это lyric-video). */
export function hasTrackInput(state: Pick<WizardStateData, 'track' | 'lyrics'>): boolean {
  return Boolean(state.track && state.lyrics.trim());
}

export const emptyAsr = (): AsrPreviewState => ({
  key: '', jobId: null, status: 'IDLE', words: [], source: [], clipStart: null, clipEnd: null, edited: false, error: null, notes: [], workingEnd: null
});

const initialData = (projectId?: string | null): WizardStateData => ({
  projectId,
  track: null,
  lyrics: '',
  fragmentEnabled: false,
  fragmentLyrics: '',
  timingMode: 'manual',
  timingFrom: '',
  timingTo: '',
  carriedOverInputs: false,
  // Докуда человек уже дошёл (индекс в STAGE_ORDER). Настройки этапов и так лежат в
  // сторе целиком, но без этой метки таб-бар считал пройденными только этапы ЛЕВЕЕ
  // текущего: вернувшись из Пула в Фон, обратно приходилось идти «Продолжить» через
  // Текст и FX, подтверждая каждый уже настроенный шаг заново.
  reachedIndex: 0,
  background: { mode: 'footage', footage: [], footageType: DEFAULT_FOOTAGE_TYPE, uploads: [], sourceVideos: [], photo: [], photoEffects: false, photoStyle: undefined, color: undefined, strobe: false, glue: undefined },
  hooks: { dropTime: undefined, kind: undefined, configs: {} },
  subtitles: { color: '#f6f5fd', pool: [], textByStyle: {}, textTab: null },
  fxVariants: [],
  allocation: { total: 0, background: {}, subtitles: {}, hooks: {}, styles: {}, variants: {}, strobeFont: undefined, colorFont: undefined, seeded: false },
  asr: emptyAsr(),
  timeline: emptyTimeline(),
  storyboard: emptyStoryboard(),
  montage: emptyMontage(),
  final: { subtitleColor: '#ffffff', accentColor: '#8b6fe6', videosToGenerate: 1, idempotencyKey: crypto.randomUUID() }
});

/**
 * Черновик визарда из формата stageData() — серверная копия сессии или job.stageData
 * готового батча. Раскадровка сюда не входит: в stageData у неё только планы без превью.
 */
export function dataFromStageData(projectId: string | null | undefined, raw: Record<string, unknown>): WizardStateData {
  const fresh = initialData(projectId);
  const timing = (raw.timing ?? {}) as Record<string, unknown>;
  const lyrics = typeof raw.lyrics === 'string' ? raw.lyrics : '';
  const fragment = typeof raw.fragment === 'string' ? raw.fragment : '';
  return {
    ...fresh,
    projectId,
    track: (raw.track as SavedTrack | null | undefined) ?? null,
    lyrics,
    // stageData() кладёт в fragment сам текст, когда отдельного отрывка нет: такой
    // fragment — не отдельный отрывок, иначе шаг «Трек» показал бы его включённым.
    fragmentEnabled: Boolean(fragment.trim()) && fragment !== lyrics,
    fragmentLyrics: fragment !== lyrics ? fragment : '',
    timingMode: 'manual',
    timingFrom: typeof timing.from === 'string' ? timing.from : '',
    timingTo: typeof timing.to === 'string' ? timing.to : '',
    background: (() => {
      const merged = { ...fresh.background, ...((raw.background as Partial<WizardStateData['background']>) ?? {}) };
      // Черновик мог быть сохранён на прошлой версии реестра типов футажей
      // (standard/persons/movies). Приводим здесь, иначе id уедет в render_job
      // как есть и подбор не найдёт такой план.
      merged.footageType = normalizeFootageType(merged.footageType);
      if (!merged.sourceVideos.length && merged.uploads.length) {
        merged.sourceVideos = [{ id: 'source-video-legacy', format: merged.sourceFormat === '16:9' ? '16:9' : '9:16', sourceIds: [...merged.uploads] }];
      }
      return merged;
    })(),
    hooks: migrateHooks({ ...fresh.hooks, ...((raw.hooks as Partial<WizardStateData['hooks']>) ?? {}) }),
    subtitles: (() => {
      const saved = (raw.subtitles as Partial<WizardStateData['subtitles']>) ?? {};
      const { text: _legacy, ...rest } = saved as Partial<WizardStateData['subtitles']> & { text?: unknown };
      return { ...fresh.subtitles, ...rest, textByStyle: normalizeTextByStyle(rest.textByStyle) };
    })(),
    // Серверная копия едет без цвета (он только для экрана) — раздаём заново.
    fxVariants: Array.isArray(raw.fxVariants)
      ? (raw.fxVariants as FxVariant[]).map((v, i) => ({ ...v, color: v.color ?? FX_VARIANT_PALETTE[i % FX_VARIANT_PALETTE.length] }))
      : [],
    allocation: { ...fresh.allocation, ...((raw.allocation as Partial<WizardStateData['allocation']>) ?? {}) },
    timeline: { ...fresh.timeline, ...((raw.timeline as Partial<TimelineRecipe>) ?? {}) },
    // stageData() отправляет только правленые ролики и без флага edited — возвращаем его,
    // иначе следующий сабмит отфильтровал бы эти правки как нетронутые.
    montage: {
      ...fresh.montage,
      // серверная копия хранит подпись рендера в sig, полную (с хэшем варианта) — в uiSig
      videos: Object.fromEntries(Object.entries(((raw.montage as { videos?: Record<number, MontageVideo & { uiSig?: string }> } | undefined)?.videos) ?? {})
        .map(([index, { uiSig, ...video }]) => [index, { ...video, sig: uiSig ?? video.sig, transitions: video.transitions ?? {}, styles: video.styles ?? [], edited: true }]))
    },
    asr: (() => {
      const saved = raw.asr as Partial<AsrPreviewState> | null | undefined;
      if (!saved || !saved.jobId || !Array.isArray(saved.words)) return fresh.asr;
      // Сессия хранит только правки; source дотянется поллингом по тому же key
      return { ...fresh.asr, key: String(saved.key ?? ''), jobId: saved.jobId, status: 'COMPLETED', words: saved.words, source: saved.words, edited: Boolean(saved.edited) };
    })(),
    final: { ...fresh.final, ...((raw.final as Partial<WizardStateData['final']>) ?? {}) }
  };
}

export const useWizardStore = create<WizardStore>()(
  persist(
    (set, get) => ({
      ...initialData(),
      stage: 1,
      openTableOnLoad: false,
      setStage: (stage) => set((state) => {
        const next = Math.max(1, Math.min(5, stage));
        return { stage: next, reachedIndex: Math.max(state.reachedIndex, stageIndex(next)) };
      }),
      // Смена проекта = смена черновика. Стор персистится и чистится только успешным
      // сабмитом, поэтому без сброса трек и выбор проекта A утекали в проект B.
      setProjectId: (projectId) => set((state) => (
        state.projectId && state.projectId !== projectId
          ? { ...initialData(projectId), stage: 1 }
          : { projectId }
      )),
      setTrack: (track) => set((state) => (
        // Другой трек = другой дроп: хуки сбрасываются, поэтому и пройденность
        // откатывается к «Треку». Повторная установка того же трека ничего не трогает.
        state.track?.id && track?.id === state.track.id
          ? { track }
          : { track, hooks: initialData().hooks, reachedIndex: 0 }
      )),
      setField: (key, value) => set({ [key]: value } as Partial<WizardStore>),
      setBackground: (patch) => set((state) => ({ background: { ...state.background, ...patch } })),
      toggleVibe: (vibe, format) => set((state) => {
        const bg = state.background;
        if (bg.mode === 'color') return state;
        const list = bg.mode === 'footage' ? bg.footage : bg.photo;
        const next = list.includes(vibe) ? list.filter((item) => item !== vibe) : [...list, vibe];
        // план — у подборки, открытой в момент выбора: потом список типа могут переключить
        const footagePlanes = bg.mode === 'footage' ? { ...bg.footagePlanes } : bg.footagePlanes;
        if (bg.mode === 'footage' && footagePlanes) {
          if (next.includes(vibe)) footagePlanes[vibe] = footageTypePlane(bg.footageType);
          else delete footagePlanes[vibe];
        }
        return { background: { ...bg, [bg.mode]: next, footageFormats: bg.mode === 'footage' ? { ...bg.footageFormats, [vibe]: format ?? '9:16' } : bg.footageFormats, footagePlanes }, allocation: { ...state.allocation, seeded: false, background: {} } };
      }),
      setHooks: (patch) => set((state) => {
        const { config, ...rest } = patch;
        const kind = rest.kind ?? state.hooks.kind;
        const configs = config && kind
          ? { ...state.hooks.configs, [kind]: { ...state.hooks.configs[kind], ...config } }
          : state.hooks.configs;
        return {
          hooks: { ...state.hooks, ...rest, configs },
          allocation: config ? { ...state.allocation, seeded: false } : state.allocation
        };
      }),
      clearHook: (kind) => set((state) => {
        const { [kind]: _dropped, ...configs } = state.hooks.configs;
        return { hooks: { ...state.hooks, kind: state.hooks.kind === kind ? undefined : state.hooks.kind, configs } };
      }),
      setSubtitles: (patch) => set((state) => ({ subtitles: { ...state.subtitles, ...patch } })),
      setAsrResult: (asr) => set((state) => {
        const same = state.asr.key === asr.key;
        // Слова с бэка обновляют source; рабочая копия живёт своей жизнью, пока это та же
        // примерка и в ней есть правки. Новая примерка (другой key) — всё с чистого листа.
        const keepEdits = same && state.asr.edited && asr.status === 'COMPLETED' && state.asr.words.length === asr.words.length;
        return {
          asr: {
            key: asr.key,
            jobId: asr.jobId,
            status: asr.status,
            source: asr.words,
            words: keepEdits ? state.asr.words : asr.words.map((w) => ({ ...w })),
            clipStart: asr.clipStart,
            clipEnd: asr.clipEnd,
            edited: keepEdits ? state.asr.edited : false,
            error: asr.error,
            notes: asr.notes ?? [],
            workingEnd: asr.workingEnd ?? null
          }
        };
      }),
      setAsrWord: (index, patch) => set((state) => {
        const words = state.asr.words.map((w, i) => (i === index ? { ...w, ...patch } : w));
        return { asr: { ...state.asr, words, edited: true } };
      }),
      toggleAsrFocus: (index) => set((state) => {
        const words = state.asr.words.map((w, i) => (i === index ? { ...w, focus: !w.focus } : w));
        // фокус — тоже правка: уезжает в генерацию вместе с таймингами
        return { asr: { ...state.asr, words, edited: true } };
      }),
      resetAsrEdits: () => set((state) => ({ asr: { ...state.asr, words: state.asr.source.map((w) => ({ ...w })), edited: false } })),
      toggleSubtitleStyle: (style) => set((state) => {
        const pool = state.subtitles.pool.includes(style)
          ? state.subtitles.pool.filter((item) => item !== style)
          : [...state.subtitles.pool, style];
        return { subtitles: { ...state.subtitles, pool } };
      }),
      setAllocation: (patch) => set((state) => ({ allocation: { ...state.allocation, ...patch } })),
      setTimeline: (patch) => set((state) => ({ timeline: { ...state.timeline, ...patch } })),
      setStoryboard: (next) => set({ storyboard: next }),
      setStoryboardVideo: (video) => set((state) => ({ storyboard: { ...state.storyboard, videos: { ...state.storyboard.videos, [video.index]: video } } })),
      setMontage: (patch) => set((state) => ({ montage: { ...state.montage, ...patch } })),
      patchMontageVideos: (indices, fn) => set((state) => {
        const videos = { ...state.montage.videos };
        for (const index of indices) if (videos[index]) videos[index] = fn(videos[index], index);
        return { montage: { ...state.montage, videos } };
      }),
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
          // Примерка субтитров — тоже вводная трека: те же слова и правки
          asr: state.asr,
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
        return {
          ...dataFromStageData(projectId, raw),
          stage: Math.max(1, Math.min(5, Number(stage) || 1)),
          reachedIndex: stageIndex(Math.max(1, Math.min(5, Number(stage) || 1)))
        };
      }),
      importEdit: (data) => set(() => ({
        ...initialData(data.projectId),
        ...data,
        carriedOverInputs: false,
        stage: 5,
        reachedIndex: STAGE_ORDER.length - 1,
        openTableOnLoad: true
      })),
      reopenFromJob: (projectId, raw) => set(() => {
        // В отличие от restoreSession — без проверки «в браузере свежее»: человек сам
        // попросил открыть этот батч, текущий черновик заменяется целиком.
        const data = dataFromStageData(projectId, raw);
        // Склейки батча — ровно те, что ушли в рендер: ключ рецепта считаем от тех же
        // вводных, иначе useRecipeCuts принял бы их за чужие и пересчитал. cuts === null
        // (на сабмите они были от других вводных) — пусть посчитаются заново.
        const timeline = data.timeline.cuts ? { ...data.timeline, key: recipeKeyOf(data) } : { ...data.timeline, key: '', cuts: null };
        return {
          ...data,
          timeline,
          // раскадровку собирает reopenJob.ts: ей нужна раскладка роликов «Пула» (combosOf)
          storyboard: emptyStoryboard(),
          // Новый ключ: это новый батч, а не повтор старого сабмита (бэк отдал бы старую джобу).
          final: { ...data.final, idempotencyKey: crypto.randomUUID() },
          carriedOverInputs: false,
          stage: 5,
          reachedIndex: STAGE_ORDER.length - 1,
          openTableOnLoad: true
        };
      }),
      consumeOpenTable: () => set({ openTableOnLoad: false }),
      stageData: () => {
        const state = get();
        return {
          track: state.track,
          lyrics: state.lyrics,
          // The track-step textarea contains the exact lyrics heard inside the
          // selected window. Production local CTC needs that value explicitly
          // as target_fragment; legacy selection mode can still override it.
          fragment: state.fragmentEnabled ? state.fragmentLyrics : state.lyrics,
          // The web UI always asks for an explicit window. Persisted drafts used
          // to keep timingMode='ai' even after both fields were filled, which
          // dropped the visible values from the production payload.
          timing: state.timingFrom && state.timingTo
            ? { from: state.timingFrom, to: state.timingTo }
            : { mode: state.timingMode },
          background: state.background,
          // Режим вариантов FX: хуки едут списком вариантов, а распределение — по их id.
          // Классические hooks.configs/allocation.hooks/styles при этом пустые: иначе бэк
          // развернул бы ещё и их (два источника одного и того же). Черновики (без хука)
          // не едут — их не видно ни в пуле, ни в рендере.
          ...(fxVariantsMode()
            ? {
              hooks: { dropTime: state.hooks.dropTime, configs: {} },
              fxVariants: state.fxVariants.filter((v) => !v.draft).map(({ id, kind, config }) => ({ id, kind, config })),
              allocation: { ...state.allocation, hooks: {}, styles: {} }
            }
            : { hooks: state.hooks, allocation: (({ variants: _variants, ...rest }) => rest)(state.allocation) }),
          subtitles: state.subtitles,
          // Примерка субтитров: бэк сверит key со своими вводными и, если совпало,
          // отдаст правки в оркестратор и запустит рендер от этой ASR-джобы.
          asr: state.asr.jobId
            ? { key: state.asr.key, jobId: state.asr.jobId, edited: state.asr.edited, words: state.asr.words }
            : null,
          timeline: { ...state.timeline, cuts: state.timeline.key === recipeKeyOf(state) ? state.timeline.cuts : null },
          // Раскадровка «Пула»: бэк приклеит план каждого видео к его вариации и
          // сверит фон и окно — устаревшая раскадровка = явная ошибка, а не тихий перебор.
          storyboard: Object.keys(state.storyboard.videos).length
            ? {
              key: state.storyboard.key,
              videos: Object.values(state.storyboard.videos).map((video) => ({ index: video.index, group: video.group, plan: video.plan }))
            }
            : null,
          // Правки стола — только ролики, которые правили руками: остальные рендер
          // собирает по варианту FX, как раньше. Правки под другую комбинацию (поменяли
          // раздачу «Пула» или настройку варианта на FX) не шлём намеренно: бэк ответил бы
          // 422 «устарели», а «Пул» пишет, у скольких видео они сброшены (staleMontageEdits).
          montage: (() => {
            const combos = combosOf(state);
            return {
              // Кадров столько, сколько склеек + 1: окна стилей, торчащие за конец (склейки
              // пересчитались при закрытом столе), подрезаем — рендер их иначе отклонит.
              videos: Object.fromEntries(Object.entries(state.montage.videos)
                .filter(([index, video]) => video.edited && combos[Number(index)]?.sig === video.sig)
                .map(([index, { sig, kind, config, transitions, styles, sub, frame }]) => {
                  const shots = Array.isArray(state.timeline.cuts) ? state.timeline.cuts.length + 1 : null;
                  const fit = shots === null ? styles : styles.filter((st) => st.a < shots).map((st) => ({ ...st, b: Math.min(st.b, shots) }));
                  // uiSig — полная подпись для восстановления черновика с сервера (рендер её не читает)
                  return [index, { sig: renderSigOf(sig), uiSig: sig, kind, config, transitions, styles: fit, sub, frame: frame ?? null }];
                }))
            };
          })(),
          final: state.final
        };
      }
    }),
    {
      name: 'blast-wizard-v4',
      version: 9,
      migrate: (raw: any, version: number) => {
        const background = { ...raw.background };
        if (!background.sourceVideos?.length && background.uploads?.length) background.sourceVideos = [{
          id: 'source-video-legacy', format: background.sourceFormat === '16:9' ? '16:9' : '9:16', sourceIds: [...background.uploads]
        }];
        background.sourceVideos ??= [];
        // v9: настройки текста — по стилю (textByStyle). Прежний общий `text` (v6–v8)
        // был только CSS-превью (рендер его не получал) — не переносим, стили
        // стартуют со стандартных настроек.
        const { text: _legacyText, ...subtitlesRest } = raw.subtitles ?? {};
        void version;
        return { ...raw, background, subtitles: { color: '#f6f5fd', pool: [], textTab: null, ...subtitlesRest, textByStyle: normalizeTextByStyle(subtitlesRest.textByStyle) }, fxVariants: Array.isArray(raw.fxVariants) ? raw.fxVariants : [], timeline: { ...emptyTimeline(), ...(raw.timeline ?? {}) }, storyboard: raw.storyboard ?? emptyStoryboard(), montage: raw.montage ?? emptyMontage(), hooks: migrateHooks(raw.hooks ?? {}), allocation: { ...raw.allocation, styles: raw.allocation?.styles ?? {}, variants: raw.allocation?.variants ?? {},
          hooks: Object.fromEntries(Object.entries(raw.allocation?.hooks ?? {}).map(([key, value]) => [key === 'sound' ? 'warmup' : key, value])) } };
      },
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
        fxVariants: state.fxVariants,
        subtitles: state.subtitles,
        allocation: state.allocation,
        asr: state.asr,
        timeline: state.timeline,
        storyboard: state.storyboard,
        montage: state.montage,
        final: state.final,
        stage: state.stage,
        reachedIndex: state.reachedIndex
      })
    }
  )
);

/*
 * Общая точка входа в «ещё один батч по этому проекту»: «+» на странице проекта и
 * «Сделать ещё» на странице проектов вели в разные места — вторая просто открывала тот же
 * батч. Если трек и текст проекта уже в сторе, начинаем сразу с этапа «Фон», иначе с «Трека».
 * Возвращает адрес визарда.
 */
export function startNextBatch(projectId: string): string {
  const state = useWizardStore.getState();
  const sameProject = state.projectId === projectId;
  if (sameProject && hasTrackInput(state)) {
    state.newBatch(projectId);
    state.setStage(2);
  } else if (sameProject && state.track) {
    // трек загружен, но текста отрывка ещё нет — без него генерировать нечего
    state.newBatch(projectId);
    state.setStage(1);
  } else {
    state.reset(projectId);
    state.setStage(1);
  }
  return `/app/generate?project=${projectId}`;
}
