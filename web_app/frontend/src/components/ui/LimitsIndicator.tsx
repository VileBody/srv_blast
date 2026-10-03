import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { activeJobOptions } from '../../lib/activeJob';
import { isSubscriptionPlan } from '../../lib/types';
import { cssZoom } from '../../lib/zoom';
import { LimitsPopoutCard, TrackLimitBar, type PopoutVariant } from '../funnel/LimitsPopout';
import { isUnlimitedTrack, quotaLeft, useFunnelState, useTripwirePurchase } from '../funnel/useFunnel';
import { funnelSeen, markFunnelSeen, useFunnelUi } from '../../stores/funnelUi';

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
 *
 * Безлимит на трек (docs/BOT_TO_WEB_FLOW.md, раздел 5) живёт здесь же: третья строка
 * поповера и окно у кружка, которое всплывает само один раз на каждое исчерпание —
 * перезарядка с трипваером или «бесплатные ролики кончились» со входом в безлимит.
 */

/** Стрелка «→» ссылки «Обновить» — та же, что в «Лимитах» профиля (home-arrow, grad-main). */
function SvgArrow() {
  return (
    <span
      aria-hidden="true"
      className="inline-block shrink-0 transition-transform duration-150 group-hover:translate-x-[2px]"
      style={{
        width: 6.4,
        height: 11.2,
        background: 'var(--grad-main)',
        WebkitMask: 'url(/assets/figma/home-arrow.svg) center / contain no-repeat',
        mask: 'url(/assets/figma/home-arrow.svg) center / contain no-repeat'
      }}
    />
  );
}
/** Через сколько снова показать окно «бесплатные ролики кончились». */
const CREDITS_OUT_REPEAT_MS = 24 * 3600 * 1000;

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
export function LimitsIndicator({
  offsetY = 13,
  track
}: {
  offsetY?: number;
  /** трек страницы (визард — текущий, батч — трек батча): на него открывается безлимит */
  track?: { id?: string; audioHash?: string; title?: string; projectId?: string };
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  // Поповер кликабельный («Обновить»): закрываем с задержкой, чтобы курсор успел
  // перейти с кружка на поповер через зазор между ними.
  const closeTimer = useRef<number | null>(null);
  const show = () => {
    if (closeTimer.current) window.clearTimeout(closeTimer.current);
    setOpen(true);
  };
  const hideSoon = () => {
    if (closeTimer.current) window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(() => setOpen(false), 160);
  };
  useEffect(() => () => { if (closeTimer.current) window.clearTimeout(closeTimer.current); }, []);
  const [anchor, setAnchor] = useState<{ host: HTMLElement; x: number; y: number } | null>(null);
  const ringRef = useRef<HTMLSpanElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  // поповер живёт в портале — для «фокус был внутри?» нужен свой ref
  const popRef = useRef<HTMLSpanElement>(null);
  // фокус вернули на кружок после Escape — этот onFocus не должен снова открыть поповер
  const refocusing = useRef(false);
  const queryClient = useQueryClient();
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me });
  const funnelQuery = useFunnelState();
  const tripwire = useTripwirePurchase();
  const openUnlimited = useFunnelUi((state) => state.openUnlimited);
  const funnelOpen = useFunnelUi((state) => Boolean(state.open));
  const badge = useFunnelUi((state) => state.badge);
  // тот же запрос, что у шапки (AppShell): идёт ли сейчас батч
  const activeJobQuery = useQuery(activeJobOptions);
  const batchRunning = Boolean(activeJobQuery.data?.job);
  const [closedKey, setClosedKey] = useState<string | null>(null);
  const [shownKey, setShownKey] = useState<string | null>(null);

  const sub = meQuery.data?.subscription;
  const videosTotal = sub?.creditsTotal ?? null;
  const videosUsed = sub?.creditsUsed ?? 0;
  const tracksTotal = sub?.tracksTotal ?? null;
  const tracksUsed = sub?.tracksUsed ?? 0;
  const funnel = funnelQuery.data;
  const unlimited = funnel?.unlimited ?? null;
  const quota = unlimited?.quota ?? null;
  const creditsOut = videosTotal !== null && videosUsed >= videosTotal;

  // Окно у кружка: перезарядка безлимита или кончились бесплатные ролики (без безлимита).
  // Перезарядка — только у трека безлимита (или где трека страницы нет): в визарде другого
  // трека окно про «Нет любви» было бы чужим.
  const sameTrack = !track || isUnlimitedTrack(unlimited, track) !== false;
  // Платящим ни перезарядка, ни трипваер за 399 ₽ не предлагаются: воронка конверсионная,
  // а безлимит на трек мог остаться у них с бесплатного периода.
  const free = Boolean(funnel && !funnel.hasPaid);
  const popout: { variant: PopoutVariant; key: string } | null = free && quota && !quota.allowed && !quota.tripwire && sameTrack
    ? { variant: quota.reason === 'daily_limit' ? 'daily' : 'cooldown', key: `limit:${quota.availableAt}` }
    : free && creditsOut && (!unlimited || isUnlimitedTrack(unlimited, track) === false)
      ? { variant: 'creditsOut', key: unlimited ? `credits-out:${track?.audioHash ?? track?.id}` : 'credits-out' }
      : null;
  // «Ролики кончились» не перебивает воронку: пока батч собирается (бесплатные 5 роликов
  // уходят разом при запуске), пока открыта модалка воронки и пока в углу висит плашка
  // безлимита — призыв уже на экране.
  const quiet = funnelOpen || (popout?.variant === 'creditsOut' && (batchRunning || Boolean(badge)));
  // Один раз на исчерпание: «видел» ставим при первом показе, а не по крестику — иначе окно
  // всплывало бы на каждой странице с кружком. «Ролики кончились» — раз в сутки.
  const showPopout = Boolean(
    popout && !quiet && popout.key !== closedKey && (
      popout.key === shownKey || !funnelSeen(popout.key, popout.variant === 'creditsOut' ? CREDITS_OUT_REPEAT_MS : undefined)
    )
  );
  useEffect(() => {
    if (!showPopout || !popout || popout.key === shownKey) return;
    markFunnelSeen(popout.key);
    setShownKey(popout.key);
    // Первый упор в перезарядку открывает 24-часовое окно трипваера: показ окна — это и
    // есть упор. Не открылось — кнопка покупки ответит понятной ошибкой, окно не прячем.
    if (popout.variant !== 'creditsOut' && funnel && !funnel.hasPaid && !funnel.tripwireOffer) {
      api.funnelTripwireOffer(track?.id)
        .then(() => queryClient.invalidateQueries({ queryKey: ['funnel-state'] }))
        .catch(() => {});
    }
  }, [showPopout, popout, shownKey, funnel, track?.id, queryClient]);
  const closePopout = () => {
    if (!popout) return;
    markFunnelSeen(popout.key);
    setClosedKey(popout.key);
  };

  // Escape закрывает поповер (и окно у кружка) и возвращает фокус на кружок — раньше
  // поповер с клавиатуры было не закрыть, кроме как увести фокус Tab'ом.
  const closePopoutRef = useRef(closePopout);
  closePopoutRef.current = closePopout;
  useEffect(() => {
    if (!open && !showPopout) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (closeTimer.current) window.clearTimeout(closeTimer.current);
      if (open) setOpen(false);
      else closePopoutRef.current();
      // фокус возвращаем, только если он был в поповере: окно всплывает само, и Escape
      // в чужом поле не должен уводить фокус на кружок
      const trigger = triggerRef.current;
      const active = document.activeElement;
      const inside = Boolean(active && (popRef.current?.contains(active) || ringRef.current?.contains(active)));
      if (trigger && inside && active !== trigger) {
        refocusing.current = true;
        trigger.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, showPopout]);

  // позиция кружка внутри карточки-хоста — по ней ставим копию кружка и поповер
  useLayoutEffect(() => {
    if ((!open && !showPopout) || !ringRef.current) { setAnchor(null); return; }
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
  }, [open, showPopout]);

  // Кружок заполняется по лимиту ВИДЕО (решение заказчика); безлимит — пустое кольцо.
  // Ролики кончились, а безлимит на трек открыт — кружок показывает уже его окно.
  const window_ = quotaLeft(quota);
  const pct = creditsOut && quota && !quota.tripwire && window_.cap
    ? (window_.cap - window_.left) / window_.cap
    : videosTotal ? videosUsed / videosTotal : 0;

  return (
    <span
      ref={ringRef}
      className="relative z-[8] inline-flex max-md:translate-y-[1px]"
      onMouseEnter={show}
      onMouseLeave={hideSoon}
    >
      <button
        ref={triggerRef}
        type="button"
        aria-label={t('limits.title')}
        aria-expanded={open}
        onFocus={() => {
          if (refocusing.current) { refocusing.current = false; return; }
          setOpen(true);
        }}
        onBlur={() => setOpen(false)}
        className="block"
      >
        <LimitRing pct={pct} />
      </button>

      {showPopout && popout && anchor && funnel && createPortal(
        // ui-allow: якорь окна повторяет геометрию кружка 25×25, как у поповера ниже
        <span ref={popRef} className="pointer-events-none absolute z-[9] h-[25px] w-[25px] max-md:!left-[20px] max-md:right-[20px] max-md:w-auto" style={{ left: anchor.x, top: anchor.y }}>
          <span className="absolute right-0 max-md:left-0" style={{ top: 25 + offsetY }}>
            <LimitsPopoutCard
              variant={popout.variant}
              trackTitle={unlimited?.trackTitle}
              availableAt={quota?.availableAt}
              rules={funnel.rules}
              onBuy={!funnel.hasPaid && funnel.tripwireOffer && (track?.id || unlimited?.trackId)
                // трипваер — на любой трек: покупаем на трек этой страницы, иначе на трек безлимита
                ? () => tripwire.mutate((track?.id || unlimited?.trackId) as string)
                : undefined}
              offerExpiresAt={funnel.tripwireOffer?.expiresAt}
              buyPending={tripwire.isPending}
              onUnlock={track?.id ? () => {
                closePopout();
                openUnlimited({ source: 'gate', trackId: track.id, audioHash: track.audioHash, trackTitle: track.title, projectId: track.projectId });
              } : undefined}
              onClose={closePopout}
            />
          </span>
        </span>,
        anchor.host
      )}

      {open && !showPopout && anchor && createPortal(
        <>
          <span aria-hidden="true" className="pointer-events-none absolute inset-0 z-[6] rounded-r25 bg-[rgba(20,14,36,0.4)]" />
          <span ref={popRef} className="pointer-events-none absolute z-[8] h-[25px] w-[25px] max-md:!left-[20px] max-md:right-[20px] max-md:w-auto" style={{ left: anchor.x, top: anchor.y }}>
            <span
              role="tooltip"
              onMouseEnter={show}
              onMouseLeave={hideSoon}
              className="pointer-events-auto absolute right-0 flex w-[360px] flex-col gap-[20px] rounded-r15 bg-grad-soft-20 p-[24px] shadow-[0_24px_60px_rgba(5,1,15,0.45)] backdrop-blur-[50px] max-md:left-0 max-md:w-auto max-md:p-[16px]"
              style={{ top: 25 + offsetY }}
            >
              <span className="flex items-center justify-between gap-space-4">
                <span className="text-ui-20 text-text">{t('limits.title')}</span>
                {/* Платящим — «Обновить →» как в «Лимитах» профиля: питч они уже видели,
                    им нужен короткий путь продлить или расширить тариф. */}
                {funnel?.hasPaid && (
                  <Link
                    to="/app/pricing"
                    className="group flex items-center gap-[8px] text-ui-14 text-transparent transition hover:brightness-125"
                    style={{ backgroundImage: 'var(--grad-main)', WebkitBackgroundClip: 'text', backgroundClip: 'text' }}
                  >
                    {t('profile.update')}
                    <SvgArrow />
                  </Link>
                )}
              </span>
              <LimitBar label={t('limits.tracks')} used={tracksUsed} total={tracksTotal} />
              <LimitBar label={t('limits.videos')} used={videosUsed} total={videosTotal} />
              {quota && <TrackLimitBar trackTitle={unlimited?.trackTitle ?? null} quota={quota} />}
              {/* Платят, но не на подписке (пакет, трипваер) — напоминаем про Бласт */}
              {funnel?.hasPaid && sub && !isSubscriptionPlan(sub) && (
                <span className="border-t border-line pt-[14px] text-ui-14 text-text-60">{t('limits.blastNudge')}</span>
              )}
            </span>
          </span>
        </>,
        anchor.host
      )}
    </span>
  );
}
