import { useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { cssZoom } from '../../lib/zoom';

/*
 * Лимиты (Figma W19+W46 — Пул; W36+W47 — батч): кружок-индикатор рядом со счётчиком,
 * по ховеру — поповер со шкалами «Треки» и «Видео», карточка под ним затемняется.
 *
 * Слои по макету: затемнение rgba(20,14,36,.4) накрывает ВСЮ карточку (включая строку
 * со счётчиком), а кружок и поповер лежат ПОВЕРХ него. В DOM кружок вложен в строку со
 * своим z-контекстом, поэтому поднять его над затемнением на месте нельзя — затемнение,
 * копия кружка и поповер портируются в карточку-хост `[data-limits-dim]` (ей нужен `relative`).
 *
 * Геометрия: кружок 25×25; поповер 360 r15 grad-soft-20 backdrop-blur-50, правый край =
 * правый край кружка. Строка лимита — подпись и «n из m» сверху, тонкая шкала под ними.
 */

/** Донат-индикатор (Figma 758:584): кольцо whitey + дуга grad-main от 12 часов по часовой */
function LimitRing({ pct }: { pct: number }) {
  // r=10.625 — середина кольца толщиной 3.75 при внешнем радиусе 12.5 (viewBox 25)
  const R = 10.625;
  const C = 2 * Math.PI * R;
  const filled = Math.max(0, Math.min(1, pct)) * C;
  return (
    // overflow visible: внешний край кольца ровно на границе viewBox, и при дробном масштабе
    // холста сглаживание с одной стороны срезалось — кольцо казалось обрезанным справа
    <svg viewBox="0 0 25 25" width="25" height="25" overflow="visible" aria-hidden="true" className="block shrink-0 overflow-visible max-md:h-[26px] max-md:w-[26px]">
      <defs>
        <linearGradient id="limitRingArc" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#8b6fe6" />
          <stop offset="1" stopColor="#5f42b9" />
        </linearGradient>
      </defs>
      <circle cx="12.5" cy="12.5" r={R} fill="none" stroke="#f6f5fd" strokeOpacity="0.95" strokeWidth="3.75" />
      <circle
        cx="12.5"
        cy="12.5"
        r={R}
        fill="none"
        stroke="url(#limitRingArc)"
        strokeWidth="3.75"
        strokeDasharray={`${filled} ${C - filled}`}
        transform="rotate(-90 12.5 12.5)"
      />
    </svg>
  );
}

/**
 * Строка лимита: подпись и «n из m» в одну строку, под ними шкала на всю ширину.
 * Безлимит заливается целиком «текущим» градиентом — как в «Лимитах» профиля, иначе пустая
 * шкала читается как «ничего не доступно»; исчерпанный лимит подсвечивается словом.
 */
function LimitBar({ label, used, total }: { label: string; used: number; total: number | null }) {
  const { t } = useTranslation();
  const unlimited = total === null;
  const pct = total ? Math.max(0, Math.min(1, used / total)) : 0;
  const out = !unlimited && total !== null && used >= total;
  return (
    <span className="flex flex-col gap-[10px]">
      <span className="flex items-baseline justify-between gap-space-4 text-ui-14">
        <span className="text-text-60">{label}</span>
        <span className="tabular-nums text-text">
          {unlimited ? t('limits.noLimit') : t('limits.of', { used, total })}
          {out && <span className="ml-[8px] text-accent-light">{t('limits.out')}</span>}
        </span>
      </span>
      <span className="relative h-[6px] w-full overflow-hidden rounded-full bg-white/10">
        <span
          className={`absolute inset-y-0 left-0 rounded-full ${unlimited ? 'limit-unlimited' : 'bg-grad-main'}`}
          style={{ width: unlimited ? '100%' : `${pct * 100}%` }}
        />
      </span>
    </span>
  );
}

/**
 * Кружок лимита + поповер по ховеру.
 * Хост затемнения — ближайший предок с `data-limits-dim` (ему нужен `relative` и r25).
 * @param offsetY отступ поповера от низа кружка (Figma W46: 13, W47: 28)
 */
export function LimitsIndicator({ offsetY = 13 }: { offsetY?: number }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<{ host: HTMLElement; x: number; y: number } | null>(null);
  const ringRef = useRef<HTMLSpanElement>(null);
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me });

  // позиция кружка внутри карточки-хоста — по ней ставим копию кружка и поповер
  useLayoutEffect(() => {
    if (!open || !ringRef.current) { setAnchor(null); return; }
    const host = ringRef.current.closest('[data-limits-dim]') as HTMLElement | null;
    if (!host) return;
    const ring = ringRef.current.getBoundingClientRect();
    const box = host.getBoundingClientRect();
    // getBoundingClientRect() — визуальные пиксели, а absolute left/top внутри host — его
    // собственные CSS-пиксели. Делим на суммарный zoom хоста: в визарде это zoom корня ×
    // холст WizardCanvas. Отношение box.width / offsetWidth при вложенном zoom давало ~1,
    // и поповер уезжал вверх-влево от кружка.
    const zoom = cssZoom(host);
    setAnchor({ host, x: (ring.left - box.left) / zoom, y: (ring.top - box.top) / zoom });
  }, [open]);

  const sub = meQuery.data?.subscription;
  const videosTotal = sub?.creditsTotal ?? null;
  const videosUsed = sub?.creditsUsed ?? 0;
  const tracksTotal = sub?.tracksTotal ?? null;
  const tracksUsed = sub?.tracksUsed ?? 0;

  // Кружок заполняется по лимиту ВИДЕО (решение заказчика); безлимит — пустое кольцо
  const pct = videosTotal ? videosUsed / videosTotal : 0;

  return (
    <span
      ref={ringRef}
      className="relative z-[8] inline-flex max-md:translate-y-[1px]"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-label={t('limits.title')}
        aria-expanded={open}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        className="block"
      >
        <LimitRing pct={pct} />
      </button>

      {open && anchor && createPortal(
        <>
          <span aria-hidden="true" className="pointer-events-none absolute inset-0 z-[6] rounded-r25 bg-[rgba(20,14,36,0.4)]" />
          <span className="pointer-events-none absolute z-[8] h-[25px] w-[25px] max-md:!left-[20px] max-md:right-[20px] max-md:w-auto" style={{ left: anchor.x, top: anchor.y }}>
            <span
              role="tooltip"
              className="absolute right-0 flex w-[360px] flex-col gap-[20px] rounded-r15 bg-grad-soft-20 p-[24px] shadow-[0_24px_60px_rgba(5,1,15,0.45)] backdrop-blur-[50px] max-md:left-0 max-md:w-auto max-md:p-[16px]"
              style={{ top: 25 + offsetY }}
            >
              <span className="text-ui-20 text-text">{t('limits.title')}</span>
              <LimitBar label={t('limits.tracks')} used={tracksUsed} total={tracksTotal} />
              <LimitBar label={t('limits.videos')} used={videosUsed} total={videosTotal} />
            </span>
          </span>
        </>,
        anchor.host
      )}
    </span>
  );
}
