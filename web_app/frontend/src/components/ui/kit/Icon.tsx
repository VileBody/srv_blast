import type { CSSProperties, ReactNode } from 'react';
import { cn } from '../../../lib/cn';

/*
 * Иконка при тексте (UI_RULES.md → «Иконки»). Размер в em: глиф = 0,7 кегля соседнего текста,
 * тяжёлые (заливка или обводка толще 12% высоты) — 0,62. Поэтому одна и та же иконка сама
 * подстраивается под заголовок, таб или кнопку, и её не нужно двигать руками.
 *
 * Два источника: `src` — SVG-ассет из Figma, красится маской в currentColor (как SvgMaskIcon);
 * `children` — пути для viewBox 0 0 24 24, рисуются обводкой.
 *
 * Правило 0,7 — про видимый глиф, а не про коробку. Ассеты из Figma обрезаны по глифу,
 * а пути GLYPH занимают ~15 из 24 единиц viewBox: их коробку растягиваем на 24/15,
 * иначе стрелка в кнопке 60 px выходит в 8 px.
 */
const SVG_BOX = 24 / 15;

export type IconTone = 'current' | 'muted' | 'accent';

const TONE: Record<IconTone, string> = {
  current: '',
  muted: 'text-text-40',
  accent: 'text-accent-light'
};

export function Icon({
  src,
  children,
  ratio = 1,
  heavy = false,
  tone = 'current',
  className,
  style
}: {
  src?: string;
  children?: ReactNode;
  /** ширина / высота глифа — из viewBox ассета */
  ratio?: number;
  heavy?: boolean;
  tone?: IconTone;
  className?: string;
  style?: CSSProperties;
}) {
  const h = (heavy ? 0.62 : 0.7) * (src ? 1 : SVG_BOX);
  const size: CSSProperties = { height: `${h}em`, width: `${h * ratio}em`, ...style };
  if (src) {
    return (
      <span
        aria-hidden="true"
        className={cn('inline-block shrink-0 bg-current', TONE[tone], className)}
        style={{ ...size, WebkitMask: `url(${src}) center / contain no-repeat`, mask: `url(${src}) center / contain no-repeat` }}
      />
    );
  }
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden="true"
      className={cn('shrink-0 fill-none stroke-current', TONE[tone], className)}
      style={{ ...size, strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round' }}
    >
      {children}
    </svg>
  );
}

/* Частые глифы для кнопок и навигации: одна толщина линии на весь сайт */
export const GLYPH = {
  left: <path d="M14.5 6 8.5 12l6 6" />,
  right: <path d="M9.5 6l6 6-6 6" />,
  down: <path d="M6.5 9.5 12 15l5.5-5.5" />,
  arrowRight: <path d="M4 12h15M13.5 6.5 19 12l-5.5 5.5" />,
  arrowLeft: <path d="M20 12H5M10.5 6.5 5 12l5.5 5.5" />,
  plus: <path d="M12 5v14M5 12h14" />,
  check: <path d="M5 12.5 9.5 17 19 7.5" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  download: <path d="M12 4.5v11M7 10.5l5 5 5-5M5 19.5h14" />,
  upload: <path d="M12 15V4.5M7.5 9 12 4.5 16.5 9M5 15v3.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V15" />,
  external: <path d="M13.5 5H19v5.5M19 5l-8 8M17 14v4a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 5 18V8.5A1.5 1.5 0 0 1 6.5 7H10" />
};
