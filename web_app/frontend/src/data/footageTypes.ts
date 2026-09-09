import registry from './footage-types.json';

/*
 * Типы футажей (Figma W12) — дерива из единого реестра, как EFFECT_HOOKS из effects-registry.json.
 * Добавить тип = одна запись в footage-types.json + строка i18n `footage.type.<id>` в оба локаля;
 * степпер, стор и render_job подхватят её сами.
 *
 * Тип — это ПЛАН подбора, а не тег: у бота их ровно три (вертикальные вайбы 9:16,
 * коллекции 16:9, фильмы), и каждый ранжируется своим пулом. Раньше здесь лежали
 * standard/persons/movies с полем `vibeTag`, которого нет ни в одном контракте, —
 * поэтому степпер листался, а список примеров оставался прежним.
 */

export interface FootageType {
  id: string;
  /** план каталога превью и `pool` в RankBucketsRequest */
  plane: string;
  /** геометрия выдачи: у 16:9 — wide, иначе vertical */
  renderPreset: 'vertical' | 'wide' | 'square';
}

export const FOOTAGE_TYPES: FootageType[] = registry.types as FootageType[];

const ALIASES: Record<string, string> = (registry as { aliases?: Record<string, string> }).aliases ?? {};

export const DEFAULT_FOOTAGE_TYPE: string =
  registry.default && FOOTAGE_TYPES.some((type) => type.id === registry.default)
    ? registry.default
    : FOOTAGE_TYPES[0].id;

/**
 * id из стора → живой id реестра.
 *
 * В сохранённых черновиках лежат значения прошлой версии реестра. Без приведения
 * человек, вернувшийся в свой черновик, получил бы пустой список примеров и
 * степпер, застрявший на несуществующем шаге.
 */
export function normalizeFootageType(id: string | undefined): string {
  const raw = String(id ?? '');
  if (FOOTAGE_TYPES.some((type) => type.id === raw)) return raw;
  const alias = ALIASES[raw];
  return alias && FOOTAGE_TYPES.some((type) => type.id === alias) ? alias : DEFAULT_FOOTAGE_TYPE;
}

export function footageTypeOf(id: string | undefined): FootageType {
  const normalized = normalizeFootageType(id);
  return FOOTAGE_TYPES.find((type) => type.id === normalized) ?? FOOTAGE_TYPES[0];
}

/** План каталога для выбранного типа — им фильтруется список примеров. */
export const footageTypePlane = (id: string | undefined): string => footageTypeOf(id).plane;

/** i18n-ключ отображения шага степпера */
export const footageTypeKey = (id: string | undefined) => `footage.type.${normalizeFootageType(id)}`;

export function footageTypeIndex(id: string | undefined): number {
  const normalized = normalizeFootageType(id);
  const index = FOOTAGE_TYPES.findIndex((type) => type.id === normalized);
  return index === -1 ? 0 : index;
}

/** Шаг степпера по кругу (Figma: стрелки ‹ ›) */
export function stepFootageType(id: string | undefined, delta: number): string {
  const next = (footageTypeIndex(id) + delta + FOOTAGE_TYPES.length) % FOOTAGE_TYPES.length;
  return FOOTAGE_TYPES[next].id;
}
