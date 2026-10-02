import type { SavedTrack, StoryboardPickedClip } from '../lib/types';
import { footageTypePlane, normalizeFootageType } from '../data/footageTypes';
import { combosOf } from '../components/wizard/SlicePanel';
import { NO_GLUE } from '../components/wizard/hookCatalog';
import { seedKeyFor } from '../components/wizard/storyboardData';
import {
  DEFAULT_SUBTITLE_TEXT_SETTINGS,
  FX_VARIANT_PALETTE,
  emptyAsr,
  emptyMontage,
  recipeKeyOf,
  useWizardStore,
  type FxVariant,
  type HookConfig,
  type HookKind,
  type MontageVideo,
  type StoryboardVideo,
  type SubtitleTextSettings,
  type WizardStateData
} from './wizardStore';

/*
 * «Докрутить на сайте»: ролики батча из бота → черновик визарда на монтажном столе.
 *
 * Бэк (`web_app/backend/app/bot_import.py`) уже перевёл ролики бота в термины визарда:
 * отрывок, текст, дроп, фон, стиль субтитров, вариант FX, распределение «Пула», склейки,
 * планы раскадровки с превью клипов, рамки и готовую примерку субтитров. Здесь остаётся
 * собрать из этого стор так, чтобы каждый экран счёл его своим: ключ рецепта и ключ
 * раскадровки считаются ровно как в useRecipeCuts / PoolStoryboard (иначе они бы
 * пересчитали склейки и клипы заново), правки стола — с подписью комбинации, как в
 * combosOf. Генерация отсюда идёт обычным путём: footage_plan + склейки таймлайна.
 */

export interface WizardImportStoryboardVideo {
  index: number;
  group: string;
  plan: Record<string, unknown>;
  clips: StoryboardPickedClip[];
  repeats: number[];
}

export interface WizardImport {
  timing: { from: string; to: string };
  window: { start: number; end: number };
  lyrics: string;
  dropTime: string | null;
  background: Partial<WizardStateData['background']>;
  subtitles: { pool: string[]; textTab: string | null; color?: string; textByStyle: Record<string, Partial<SubtitleTextSettings>> };
  fxVariants: { id: string; kind: HookKind; config: HookConfig }[];
  allocation: Partial<WizardStateData['allocation']>;
  timeline: { pace: 'auto'; cuts: number[] };
  storyboard: WizardImportStoryboardVideo[];
  /** номер ролика (с нуля) → id рамки */
  frames: Record<string, string>;
  asr: {
    key: string; jobId: string | null; status: string; words: { text: string; tStart: number; tEnd: number; weak?: boolean }[];
    clipStart: number | null; clipEnd: number | null; notes?: string[]; workingEnd?: number | null;
  } | null;
  /** что перенести точно не вышло — показывается человеку */
  notes: string[];
}

const NO_STYLE = 'Без стилизации';

export function applyWizardImport(projectId: string, track: SavedTrack | null | undefined, imp: WizardImport): void {
  const base = useWizardStore.getState();
  const idempotencyKey = crypto.randomUUID();
  const timingFrom = imp.timing.from;
  const timingTo = imp.timing.to;
  const dropTime = imp.dropTime ?? undefined;

  const background: WizardStateData['background'] = {
    mode: 'footage', footage: [], footageType: 'vertical', uploads: [], sourceVideos: [], photo: [],
    photoEffects: false, photoStyle: undefined, color: undefined, strobe: false, glue: undefined,
    ...imp.background
  };
  background.footageType = normalizeFootageType(background.footageType);

  const subtitles: WizardStateData['subtitles'] = {
    // ui-allow: данные, а не стиль: тот же стандартный цвет субтитров, что в initialData стора
    color: imp.subtitles.color ?? '#f6f5fd',
    pool: imp.subtitles.pool,
    textTab: imp.subtitles.textTab,
    textByStyle: Object.fromEntries(Object.entries(imp.subtitles.textByStyle ?? {})
      .map(([style, value]) => [style, { ...DEFAULT_SUBTITLE_TEXT_SETTINGS, ...value }]))
  };

  const fxVariants: FxVariant[] = imp.fxVariants.map((v, i) => ({ ...v, color: FX_VARIANT_PALETTE[i % FX_VARIANT_PALETTE.length] }));
  const allocation: WizardStateData['allocation'] = {
    total: 0, background: {}, subtitles: {}, hooks: {}, styles: {}, variants: {}, seeded: true, ...imp.allocation
  };
  const cuts = [...imp.timeline.cuts];
  const timeline: WizardStateData['timeline'] = {
    key: recipeKeyOf({ track, timingFrom, timingTo, hooks: { dropTime } }),
    pace: 'auto',
    cuts,
    // «правлено руками»: склейки ролика уходят в рендер как есть, а не пересчитываются
    edited: true,
    transitions: {},
    styles: []
  };

  // Раскладка роликов — та же, что у «Пула» и стола: по ней и ключ раскадровки, и подписи правок.
  const combos = combosOf({ background, allocation, fxVariants, subtitles });
  const plane = footageTypePlane(background.footageType);
  const footageSlots = plane === 'vibes' ? combos.filter((c) => c.group).map((c) => ({ index: c.slotIndex, group: String(c.group) })) : [];
  const videos: Record<number, StoryboardVideo> = {};
  for (const entry of imp.storyboard) {
    const seedKey = seedKeyFor(idempotencyKey, entry.index, 0);
    videos[entry.index] = { index: entry.index, group: entry.group, seedKey, clips: entry.clips, repeats: entry.repeats ?? [], pins: {}, plan: entry.plan };
  }
  // Ключ — ровно как в PoolStoryboard: иначе раскадровка выбросила бы клипы бота и подобрала новые.
  const storyboard: WizardStateData['storyboard'] = imp.storyboard.length
    ? { key: JSON.stringify([timingFrom, timingTo, cuts, footageSlots.map((s) => [s.index, s.group])]), videos }
    : { key: '', videos: {} };

  // Рамка живёт на столе у ролика: правка с подписью его комбинации (как defaultsFor стола).
  const shots = cuts.length + 1;
  const montage = emptyMontage();
  for (const [key, frame] of Object.entries(imp.frames ?? {})) {
    const combo = combos[Number(key)];
    if (!combo || !combo.vertical) continue;
    const config: HookConfig = combo.variant && combo.hookAllowed
      ? { ...combo.variant.config }
      : { effectGlue: background.glue ?? NO_GLUE, ...(background.photoEffects && background.photoStyle ? { effectStyles: [background.photoStyle] } : {}) };
    const picked = (config.effectStyles?.length ? config.effectStyles : config.effectStyle ? [config.effectStyle] : []).filter((s) => s !== NO_STYLE);
    const video: MontageVideo = {
      sig: combo.sig,
      kind: combo.variant && combo.hookAllowed ? combo.variant.kind : 'none',
      config,
      transitions: {},
      // стиль бота всегда на весь ролик (effect_extra_full)
      styles: picked.slice(0, 2).map((style, k) => ({ uid: k + 1, style, lane: k as 0 | 1, a: 0, b: shots })),
      sub: combo.sub,
      frame,
      edited: true
    };
    montage.videos[combo.index] = video;
  }

  const asr = imp.asr && imp.asr.jobId
    ? {
      ...emptyAsr(),
      key: imp.asr.key,
      jobId: imp.asr.jobId,
      status: 'COMPLETED' as const,
      words: imp.asr.words.map((w) => ({ ...w })),
      source: imp.asr.words.map((w) => ({ ...w })),
      clipStart: imp.asr.clipStart,
      clipEnd: imp.asr.clipEnd,
      notes: imp.asr.notes ?? [],
      workingEnd: imp.asr.workingEnd ?? null
    }
    : emptyAsr();

  base.importEdit({
    projectId,
    track: track ?? null,
    lyrics: imp.lyrics,
    fragmentEnabled: false,
    fragmentLyrics: '',
    timingMode: 'manual',
    timingFrom,
    timingTo,
    background,
    hooks: { dropTime, kind: undefined, configs: {} },
    subtitles,
    fxVariants,
    allocation,
    asr,
    timeline,
    storyboard,
    montage,
    final: { ...base.final, idempotencyKey, videosToGenerate: Math.max(1, allocation.total) }
  });
}
