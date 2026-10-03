import { api } from '../lib/api';
import type { GenerationJob, SavedTrack } from '../lib/types';
import { footageTypePlane } from '../data/footageTypes';
import { combosOf } from '../components/wizard/SlicePanel';
import { seedKeyFor } from '../components/wizard/storyboardData';
import { hasTrackInput, startNextBatch, useWizardStore, type StoryboardClip, type StoryboardVideo } from './wizardStore';

/*
 * «Открыть таймлайн» у готового батча (оценка ролика, шаг «Что докрутить?» воронки):
 * визард открывается с настройками ИМЕННО этого батча — job.stageData, то, что визард
 * отправил в сабмит, — сразу на «Пуле» с открытым монтажным столом.
 *
 * Раскадровка в stageData — только планы клипов, без превью. Чтобы «Пул» не подобрал
 * клипы заново, она восстанавливается синхронно под тем же ключом, что считает
 * PoolStoryboard (клипы из плана, превью пока пустые), а превью дотягиваются тем же
 * подбором «Пула» с закреплёнными клипами плана на каждом кадре (как ремикс из бота,
 * main.py::_remix_storyboard_views). Не дотянулись — кадры остаются без превью, но
 * в рендер всё равно уходит план батча.
 */

interface SavedStoryboardVideo {
  index: number;
  group: string;
  plan: Record<string, unknown>;
}

interface PlanClip {
  file_name?: unknown;
  in_point?: unknown;
  out_point?: unknown;
}

function planClips(plan: Record<string, unknown>): PlanClip[] {
  return Array.isArray(plan.clips) ? (plan.clips as PlanClip[]) : [];
}

/** Адрес визарда этого батча. Без трека в stageData — обычный «ещё один батч» по проекту. */
export function openJobOnTable(job: Pick<GenerationJob, 'projectId' | 'stageData'>): string {
  const raw = (job.stageData ?? {}) as Record<string, unknown>;
  const track = raw.track as SavedTrack | null | undefined;
  const lyrics = typeof raw.lyrics === 'string' ? raw.lyrics : '';
  // Без трека и текста настраивать нечего (такой сабмит бэк не принял бы) — обычный новый батч.
  if (!track?.id || !hasTrackInput({ track, lyrics })) return startNextBatch(job.projectId);

  useWizardStore.getState().reopenFromJob(job.projectId, raw);
  restoreStoryboard(raw);
  return `/app/generate?project=${encodeURIComponent(job.projectId)}`;
}

function restoreStoryboard(raw: Record<string, unknown>): void {
  const saved = (raw.storyboard as { videos?: SavedStoryboardVideo[] } | null | undefined)?.videos;
  const state = useWizardStore.getState();
  const cuts = state.timeline.cuts;
  if (!Array.isArray(saved) || !saved.length || !cuts) return;

  // Раскладка роликов — та же, что у «Пула» (SlicePanel → PoolStoryboard): по ней ключ.
  const combos = combosOf(state);
  const plane = footageTypePlane(state.background.footageType);
  const footageSlots = plane === 'vibes' ? combos.filter((c) => c.group).map((c) => ({ index: c.slotIndex, group: String(c.group) })) : [];
  const shots = cuts.length + 1;
  const batchKey = state.final.idempotencyKey;
  const videos: Record<number, StoryboardVideo> = {};
  for (const entry of saved) {
    const slot = footageSlots.find((s) => s.index === entry.index);
    const clips = planClips(entry.plan ?? {});
    // План не про этот ролик или не под эти склейки — не подсовываем: «Пул» подберёт заново.
    if (!slot || slot.group !== entry.group || clips.length !== shots) return;
    videos[entry.index] = {
      index: entry.index,
      group: entry.group,
      seedKey: seedKeyFor(batchKey, entry.index, 0),
      clips: clips.map((clip): StoryboardClip => ({
        fileName: String(clip.file_name ?? ''),
        inPoint: Number(clip.in_point ?? 0),
        outPoint: Number(clip.out_point ?? 0),
        previewUrl: null,
        previewOffset: 0,
        tags: []
      })),
      repeats: [],
      pins: {},
      plan: entry.plan
    };
  }
  // Ключ — ровно как в PoolStoryboard: иначе раскадровка выбросила бы клипы батча.
  const key = JSON.stringify([state.timingFrom, state.timingTo, cuts, footageSlots.map((s) => [s.index, s.group])]);
  state.setStoryboard({ key, videos });
  void hydratePreviews(key, state.timingFrom, state.timingTo, cuts, Object.values(videos));
}

/** Превью клипов плана: подбор «Пула» с закреплёнными клипами на каждом кадре. */
async function hydratePreviews(key: string, clipFrom: string, clipTo: string, cuts: number[], videos: StoryboardVideo[]): Promise<void> {
  try {
    const res = await api.storyboardPick({
      clipFrom,
      clipTo,
      cuts,
      videos: videos.map((v) => ({
        index: v.index,
        group: v.group,
        seedKey: v.seedKey,
        pins: Object.fromEntries(v.clips.map((c, k) => [k, c.fileName]))
      }))
    });
    const sent = Object.fromEntries(videos.map((v) => [v.index, v.clips.map((c) => c.fileName)]));
    for (const picked of res.videos) {
      const current = useWizardStore.getState().storyboard;
      // пока шёл запрос, вводные поменялись — эти превью уже не про ту раскадровку
      if (current.key !== key) return;
      const video = current.videos[picked.index];
      const names = sent[picked.index];
      if (!video || !names) continue;
      // подбор обязан вернуть закреплённые клипы; другие — не наши превью
      if (picked.clips.length !== names.length || picked.clips.some((c, k) => c.fileName !== names[k])) continue;
      // план остаётся батчевым — из подбора берём только то, что нужно экрану
      useWizardStore.getState().setStoryboardVideo({
        ...video,
        clips: video.clips.map((clip, k) => {
          const view = picked.clips[k];
          // замену кадра, сделанную, пока шли превью, не трогаем
          return clip.fileName === view.fileName && !clip.previewUrl
            ? { ...clip, previewUrl: view.previewUrl, previewOffset: view.previewOffset, tags: view.tags }
            : clip;
        })
      });
    }
  } catch {
    // Без превью кадры просто пустые: план батча на месте и уйдёт в рендер как есть.
  }
}
