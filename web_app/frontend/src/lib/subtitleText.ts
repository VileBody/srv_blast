import type { SubtitleFontCatalog, SubtitleFontEntry } from './types';

/**
 * Правила блока «Настройки текста» — отражение каталога рендера (сам каталог и
 * проверки живут в app/subtitle_font_layout.py; бэк сверяет настройки на отправке).
 * Здесь только то, что нужно, чтобы не предлагать человеку невозможного.
 */

export type SubtitleStyleId = 'jakson' | 'impulse' | 'tape' | 'trendy' | 'brat';

const STYLE_IDS: SubtitleStyleId[] = ['jakson', 'impulse', 'tape', 'trendy', 'brat'];

/** Имя стиля из пула («Jakson», «Brat»…) → id стиля каталога. */
export function styleIdOf(name: string): SubtitleStyleId | null {
  const id = name.trim().toLowerCase();
  return (STYLE_IDS as string[]).includes(id) ? (id as SubtitleStyleId) : null;
}

export function poolStyles(pool: string[]): SubtitleStyleId[] {
  return [...new Set(pool.map(styleIdOf).filter((id): id is SubtitleStyleId => id !== null))];
}

/** Стили, где шрифт выбирается (у brat он зафиксирован). */
export function fontStyles(styles: SubtitleStyleId[], catalog: SubtitleFontCatalog | undefined): SubtitleStyleId[] {
  const locked = new Set(catalog?.lockedFontStyles ?? ['brat']);
  return styles.filter((style) => !locked.has(style));
}

/** Для каких из выбранных стилей шрифт недоступен (пусто — подходит всем). */
export function fontBlockedFor(font: SubtitleFontEntry, styles: SubtitleStyleId[], role: 'base' | 'accent' = 'base'): SubtitleStyleId[] {
  return styles.filter((style) =>
    font.excludedStyles.includes(style)
    // jakson набирает капсом: рукописный ОСНОВНЫМ там пока не собран (акцентом — можно)
    || (role === 'base' && style === 'jakson' && font.category === 'script'));
}

export function baseFonts(catalog: SubtitleFontCatalog | undefined): SubtitleFontEntry[] {
  return (catalog?.fonts ?? []).filter((font) => font.roles.includes('base'));
}

export function findFont(catalog: SubtitleFontCatalog | undefined, ps: string | null | undefined): SubtitleFontEntry | undefined {
  return ps ? catalog?.fonts.find((font) => font.ps === ps) : undefined;
}

/**
 * Акценты пары для основного: из каталога и не запрещённые стилю. base не задан —
 * «стандартный для стиля» шрифт: его пары отдаёт каталог (defaultAccents).
 */
export function accentFontsFor(catalog: SubtitleFontCatalog | undefined, base: SubtitleFontEntry | undefined,
  styles: SubtitleStyleId[]): SubtitleFontEntry[] {
  const pool = base ? base.accents : styles.length === 1 ? (catalog?.defaultAccents?.[styles[0]] ?? []) : [];
  return pool
    .map((ps) => findFont(catalog, ps))
    .filter((font): font is SubtitleFontEntry => !!font && fontBlockedFor(font, styles, 'accent').length === 0);
}

const GENERIC: Record<SubtitleFontEntry['category'], string> = {
  sans_system: 'Arial, sans-serif',
  display: 'Impact, "Arial Narrow", sans-serif',
  editorial: 'Georgia, serif',
  script: 'cursive',
};

/** CSS-семейство превью: установленный у человека шрифт (local()) или близкий по жанру. */
export function cssFamily(font: SubtitleFontEntry | undefined, fallbackPs?: string): string {
  const ps = font?.ps ?? fallbackPs;
  if (!ps) return 'Point, Arial, sans-serif';
  // Point есть на сайте (self-hosted): без установленного начертания превью берёт его, а не Arial
  const site = ps.startsWith('Point') ? 'Point, ' : '';
  return `"blast-${ps}", ${site}${font ? GENERIC[font.category] : 'Arial, sans-serif'}`;
}

const injectedFaces = new Set<string>();
/**
 * @font-face на каждый шрифт, файл которого сайт раздаёт сам (lib/useSubtitleFonts: бандл по
 * манифесту + сервер). Превью субтитров рисует только ими; добавляются по мере прихода файлов.
 * Образцы в списках шрифтов без файла показываются запасным жанром — это подпись, не превью.
 */
export function injectFontFaces(files: Record<string, string> | undefined): void {
  if (!files || typeof document === 'undefined') return;
  const fresh = Object.entries(files).filter(([ps]) => !injectedFaces.has(ps));
  if (!fresh.length) return;
  fresh.forEach(([ps]) => injectedFaces.add(ps));
  const style = document.createElement('style');
  style.dataset.blastSubtitleFonts = '1';
  style.textContent = fresh
    .map(([ps, url]) => `@font-face { font-family: "blast-${ps}"; src: url("${url}") format("woff2"); font-display: block; }`)
    .join('\n');
  document.head.appendChild(style);
}

/** Те же пресеты, что у рендера (SIZE_PRESETS / HEIGHT_PRESETS / POSITION_PRESETS). */
export const SIZE_SCALE = { large: 1, medium: 0.9, small: 0.8 } as const;
export const HEIGHT_SCALE = { compact: 0.8, normal: 1, tall: 1.3 } as const;
/** Прод-цвет акцента стиля из каталога рендера; нет — фокус как основной текст. */
export function styleAccentColor(catalog: SubtitleFontCatalog | undefined, style: SubtitleStyleId | null, textColor: string): string {
  return (style && catalog?.accentColors?.[style]) || textColor;
}

export const POSITION_CENTER_Y = { center: 0.5, left: 0.5, right: 0.5, down: 0.64 } as const;
