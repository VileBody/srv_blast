import { useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';

/*
 * Лимиты (Figma W19+W46 — Пул; W36+W47 — батч): кружок-индикатор рядом со счётчиком,
 * по ховеру — поповер со шкалами «Треки» и «Видео», карточка под ним затемняется.
 *
 * Слои по макету: затемнение rgba(20,14,36,.4) накрывает ВСЮ карточку (включая строку
 * со счётчиком), а кружок и поповер лежат ПОВЕРХ него. В DOM кружок вложен в строку со
 * своим z-контекстом, поэтому поднять его над затемнением на месте нельзя — затемнение,
 * копия кружка и поповер портируются в карточку-хост `[data-limits-dim]` (ей нужен `relative`).
 *
 * Геометрия W46/W47: кружок 25×25; поповер 522×210 r15 grad-soft-20 backdrop-blur-50,
 * правый край = правый край кружка, паддинги 28/29/25; шкалы 161×20 r20.
 */

/** Донат-индикатор (Figma 758:584): кольцо whitey + дуга grad-main от 12 часов по часовой */
function LimitRing({ pct }: { pct: number }) {
  // r=10.625 — середина кольца толщиной 3.75 при внешнем радиусе 12.5 (viewBox 25)
  const R = 10.625;
  const C = 2 * Math.PI * R;
  const filled = Math.max(0, Math.min(1, pct)) * C;
  return (
    <svg viewBox="0 0 25 25" width="25" height="25" aria-hidden="true" className="block shrink-0">
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

const SOFT_TEXT: React.CSSProperties = {
  // Figma 758:597: вертикальный градиент по тексту .8 → .64
  backgroundImage: 'linear-gradient(185deg, rgba(246,245,253,0.8) 8.5%, rgba(246,245,253,0.64) 94.6%)',
  WebkitBackgroundClip: 'text',
  backgroundClip: 'text'
};

/**
 * Строка шкалы (Figma 758:597–606): лейбл / бар 161×20 r20 / «n/m использовано» справа.
 *
 * Лейбл был жёстко 88px без переноса: «Генерации» (и англ. «Generations») в него не влезали
 * и наезжали на бар. Теперь колонка лейбла тянется по тексту с минимумом 88 и зазором,
 * а бар остаётся фигмовских 161. Безлимит заливается целиком «текущим» градиентом — так же,
 * как в «Лимитах» профиля, иначе пустая шкала читается как «ничего не доступно».
 */
function LimitBar({ label, used, total }: { label: string; used: number; total: number | null }) {
  const { t } = useTranslation();
  const unlimited = total === null;
  const pct = total ? Math.max(0, Math.min(1, used / total)) : 0;
  return (
    <span className="flex items-center gap-[16px]">
      <span className="min-w-[88px] shrink-0 whitespace-nowrap text-[16px] font-[400] leading-[19px] text-transparent" style={SOFT_TEXT}>{label}</span>
      <span className="relative h-[20px] w-[161px] shrink-0 overflow-hidden rounded-[20px] bg-grad-soft-20">
        <span
          className={`absolute inset-y-0 left-0 rounded-[20px] ${unlimited ? 'limit-unlimited' : 'bg-grad-main'}`}
          style={{ width: unlimited ? '100%' : `${pct * 100}%` }}
        />
      </span>
      <span className="ml-auto whitespace-nowrap text-right text-[16px] font-[400] leading-[19px] text-transparent" style={SOFT_TEXT}>
        {unlimited ? t('limits.noLimit') : t('limits.used', { used, total })}
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
    setAnchor({ host, x: ring.left - box.left, y: ring.top - box.top });
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
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-label={t('limits.title')}
        aria-expanded={open}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        className={open ? 'block opacity-0' : 'block'}
      >
        <LimitRing pct={pct} />
      </button>

      {open && anchor && createPortal(
        <>
          <span aria-hidden="true" className="pointer-events-none absolute inset-0 z-[6] rounded-r25 bg-[rgba(20,14,36,0.4)]" />
          <span className="pointer-events-none absolute z-[8]" style={{ left: anchor.x, top: anchor.y }}>
            <LimitRing pct={pct} />
            <span
              role="tooltip"
              className="absolute right-0 block w-[522px] rounded-r15 bg-grad-soft-20 px-[28px] pb-[25px] pt-[29px] backdrop-blur-[50px]"
              style={{ top: 25 + offsetY }}
            >
              <span className="flex items-center gap-[16px]">
                <img src="/assets/figma/icon-note.svg" width="12" height="17" alt="" aria-hidden="true" />
                <span className="text-[24px] font-[400] leading-[29px] text-text">{t('limits.title')}</span>
              </span>

              <span className="mt-[28px] block">
                <LimitBar label={t('limits.tracks')} used={tracksUsed} total={tracksTotal} />
              </span>
              <span aria-hidden="true" className="my-[28px] block h-px w-full bg-[rgba(246,245,253,0.2)]" />
              <LimitBar label={t('limits.videos')} used={videosUsed} total={videosTotal} />
            </span>
          </span>
        </>,
        anchor.host
      )}
    </span>
  );
}
