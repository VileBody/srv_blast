import registry from '../../data/figma-icon-box.json';

/*
 * Иконка из Figma с гарантированной пропорцией.
 *
 * ЗАЧЕМ: экспорт Figma ставит всем SVG `preserveAspectRatio="none"` — такой SVG молча
 * растягивается в любой заданный бокс. Задать «18×18» на глаз = раздавить глиф
 * (напр. pr-check реально 8.5×15.5, pf-scissors — 27.67×21). Размеры берём не из головы,
 * а из реальных viewBox: `figma-icon-box.json` (автоген, `npm run icons:box`).
 *
 * Задаём ОДНУ сторону (h или w) — вторая считается по viewBox. Если ассета нет в карте,
 * компонент это заметит в дев-режиме, а не нарисует растянутый мусор.
 */

const BOX: Record<string, number[]> = registry.box;

/** [width, height] из viewBox ассета, либо null если ассета нет в карте */
export function figIconBox(name: string): [number, number] | null {
  const box = BOX[name];
  return box && box.length === 2 ? [box[0], box[1]] : null;
}

export function FigIcon({
  name,
  h,
  w,
  className,
  alt = ''
}: {
  name: string;
  /** высота в px — ширина считается по пропорции viewBox */
  h?: number;
  /** ширина в px — высота считается по пропорции viewBox */
  w?: number;
  className?: string;
  alt?: string;
}) {
  const box = figIconBox(name);
  if (!box) {
    if (import.meta.env.DEV) console.warn(`[FigIcon] нет в figma-icon-box.json: ${name} — запусти npm run icons:box`);
    return null;
  }
  const [vw, vh] = box;
  const ratio = vw / vh;
  const height = h ?? (w !== undefined ? w / ratio : vh);
  const width = w ?? height * ratio;
  return (
    <img
      src={`/assets/figma/${name}`}
      width={width}
      height={height}
      alt={alt}
      aria-hidden={alt ? undefined : true}
      className={className}
      style={{ width, height, maxWidth: 'none' }}
    />
  );
}
