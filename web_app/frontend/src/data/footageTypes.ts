import registry from './footage-types.json';

/*
 * Типы футажей (Figma W12) — дерива из единого реестра, как EFFECT_HOOKS из effects-registry.json.
 * Добавить тип = одна запись в footage-types.json + строка i18n `footage.type.<id>` в оба локаля;
 * степпер, стор и render_job подхватят её сами.
 */

export interface FootageType {
  id: string;
  /** тег группы в video DB; null — вся библиотека (тип «стандартные») */
  vibeTag: string | null;
}

export const FOOTAGE_TYPES: FootageType[] = registry.types;

export const DEFAULT_FOOTAGE_TYPE: string =
  registry.default && registry.types.some((type) => type.id === registry.default)
    ? registry.default
    : registry.types[0].id;

/** i18n-ключ отображения шага степпера */
export const footageTypeKey = (id: string) => `footage.type.${id}`;

export function footageTypeIndex(id: string): number {
  const index = FOOTAGE_TYPES.findIndex((type) => type.id === id);
  return index === -1 ? 0 : index;
}

/** Шаг степпера по кругу (Figma: стрелки ‹ ›) */
export function stepFootageType(id: string, delta: number): string {
  const next = (footageTypeIndex(id) + delta + FOOTAGE_TYPES.length) % FOOTAGE_TYPES.length;
  return FOOTAGE_TYPES[next].id;
}
