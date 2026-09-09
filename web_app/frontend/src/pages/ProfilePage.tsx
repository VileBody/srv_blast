import { ChangeEvent, KeyboardEvent, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../lib/api';
import { cn } from '../lib/cn';
import { Skeleton } from '../components/ui/Skeleton';
import { QueryError, queryDown } from '../components/ui/ErrorState';
import { TiktokButton } from '../components/ui/TiktokButton';
import { FigIcon } from '../components/ui/FigIcon';
import { BillingCard } from '../components/billing/BillingCard';
import { useToast } from '../contexts/ToastContext';
import { SvgMaskIcon } from '../components/layout/SvgMaskIcon';
import { Modal } from '../components/ui/Modal';

/*
 * Личный кабинет: Figma W43 (фришник) и W44 (подписчик Blast) — одна страница, два состояния.
 * Три карточки 1192 с шагом 20: шапка 202, «Лимиты» 296, «Тариф» 366 (60..964 = fill-height).
 * Различие состояний — только в карточке тарифа: у фришника промо-блок, у подписчика
 * состав пакета + шкала месяцев.
 */

const gradLight = {
  backgroundImage: 'linear-gradient(184deg, #f6f5fd 8%, rgba(246,245,253,.8) 95%)',
  WebkitBackgroundClip: 'text',
  backgroundClip: 'text'
} as const;

const gradSoft = {
  backgroundImage: 'linear-gradient(184deg, rgba(246,245,253,0.8) 8.5%, rgba(246,245,253,0.64) 94.6%)',
  WebkitBackgroundClip: 'text',
  backgroundClip: 'text'
} as const;

/** Строка лимита (Figma 747:1609): лейбл 161 / бар 720×20 r20 / «n/m использовано» справа (231) */
function LimitRow({ label, used, total }: { label: string; used: number; total: number | null }) {
  const { t } = useTranslation();
  // total === null — безлимит: заливаем полосу целиком «текущим» градиентом (см. .limit-unlimited),
  // потому что нулевая заливка на безлимите читалась как сломанный бар.
  const unlimited = total === null;
  const pct = total ? Math.max(0, Math.min(1, used / total)) : 0;
  return (
    <div className="flex h-[29px] items-center">
      <span className="w-[161px] shrink-0 text-[24px] font-[350] leading-[29px] text-transparent" style={gradSoft}>{label}</span>
      <span
        className="relative h-[20px] min-w-0 flex-1 translate-y-[1px] overflow-hidden rounded-[20px] bg-grad-soft-20"
        role="img"
        aria-label={unlimited ? `${label}: ${t('limits.noLimit')}` : `${label}: ${t('limits.used', { used, total })}`}
      >
        <span
          className={cn('absolute inset-y-0 left-0 rounded-[20px]', unlimited ? 'limit-unlimited' : 'bg-grad-main')}
          style={{ width: unlimited ? '100%' : `${pct * 100}%` }}
        />
      </span>
      <span className="flex w-[231px] shrink-0 items-center justify-end text-right text-[16px] font-[400] leading-[19px] text-transparent" style={gradSoft}>
        {total === null ? (
          <><img src="/assets/figma/pf-infinity.svg" width="23" height="12" alt="" aria-hidden className="mr-[8px] max-w-none shrink-0" /><span>{t('limits.unlimited').replace(/^∞\s*/, '')}</span></>
        ) : t('limits.used', { used, total })}
      </span>
    </div>
  );
}

/** Пункт состава пакета (Figma W44 752:163) / буллет промо (W43 755:201) */
function Bullet({ icon, children, muted }: { icon: string; children: React.ReactNode; muted?: boolean }) {
  const renderedIcon = muted
    ? (icon === 'pf-note.svg' ? 'pr-note.svg' : icon === 'pf-scissors.svg' ? 'pf-promo-scissors.svg' : 'pr-check.svg')
    : icon;
  const iconHeight = muted
    ? (icon === 'pf-note.svg' ? 16 : icon === 'pf-scissors.svg' ? 12.216 : 15.5)
    : (icon === 'pf-note.svg' ? 24 : icon === 'pf-scissors.svg' ? 21 : 21.75);
  return (
    <span className="flex h-[30px] items-center">
      <span className={cn('flex shrink-0 items-center justify-start', muted ? 'w-[28px]' : 'w-[40px]')}>
        <FigIcon
          name={renderedIcon}
          h={iconHeight}
          className={cn(
            muted && icon === 'pf-note.svg' && 'translate-x-[2px]',
            muted && icon === 'pf-scissors.svg' && 'translate-x-px -rotate-[5deg]',
            icon === 'pf-check.svg' && 'translate-x-[4px] -translate-y-[3px] rotate-45'
          )}
        />
      </span>
      <span className={cn('font-[400] leading-[30px]', muted ? 'text-[16px] text-text-80' : 'text-[24px] text-text')}>{children}</span>
    </span>
  );
}

/** Тариф фришника (Figma W43): промо-блок 1114×220 с линиями и кнопкой «Расширить доступ ›» */
function FreeTariff() {
  const { t } = useTranslation();
  return (
    <div className="relative mt-[28px] h-[220px] overflow-hidden rounded-r15">
      <img src="/assets/figma/pf-promo-bg.svg" alt="" aria-hidden="true" className="pointer-events-none absolute inset-0 h-full w-full max-w-none select-none" />
      <p className="absolute left-[28px] top-[28px] text-[24px] font-[400] leading-[29px] text-text">{t('profile.promoTitle')}</p>
      <p className="absolute left-[28px] top-[73px] text-[16px] font-[400] leading-[19px] text-text-80">{t('profile.promoSubtitle')}</p>
      <div className="absolute left-[28px] top-[104px] flex flex-col">
        <Bullet icon="pf-note.svg" muted>{t('profile.promoTracks')}</Bullet>
        <Bullet icon="pf-scissors.svg" muted>{t('profile.promoVideos')}</Bullet>
        <Bullet icon="pf-check.svg" muted>{t('profile.promoTemplates')}</Bullet>
      </div>
      <Link
        to="/app/pricing"
        className="group absolute bottom-[28px] right-[28px] flex h-[60px] w-[320px] items-center justify-center gap-[16px] rounded-r15 border border-accent bg-grad-soft-20 text-[24px] font-[400] leading-none text-transparent backdrop-blur-[80px] transition hover:brightness-125"
      >
        <span style={gradSoft}>{t('profile.expandAccess')}</span>
        <FigIcon name="home-arrow.svg" h={15.464} className="transition-transform duration-150 group-hover:translate-x-[2px]" />
      </Link>
    </div>
  );
}

/**
 * Прогресс подписки Blast (Figma Wireframe-44 → 62): полоса на квартал, месяцы-трети от месяца
 * старта подписки. Бонус доступен ТОЛЬКО после полного месяца пользования:
 *  - пройденные месяцы → «получено», яркий градиент;
 *  - текущий (идёт) месяц → ЧУТЬ ТЕМНЕЕ градиент, бонус ещё не получен;
 *  - будущие → пусто.
 * Текст меняется на ховере (wf44 → wf62), причём только у ТОГО сегмента, на который наведён.
 */
function BlastProgress({ startedAt, claimed, onClaim, claiming }: {
  startedAt?: string;
  claimed: number;
  onClaim: () => void;
  claiming?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language.startsWith('en') ? 'en-US' : 'ru-RU';
  const now = new Date();
  const start = startedAt ? new Date(startedAt) : now;
  // сколько ПОЛНЫХ месяцев прошло с начала подписки = столько бонусов ЗАРАБОТАНО
  let earned = (now.getFullYear() - start.getFullYear()) * 12 + (now.getMonth() - start.getMonth());
  if (now.getDate() < start.getDate()) earned -= 1;
  earned = Math.max(0, Math.min(3, earned));
  const monthName = (offset: number) =>
    new Date(start.getFullYear(), start.getMonth() + offset, 1).toLocaleString(locale, { month: 'short' }).replace('.', '');
  const months = [0, 1, 2].map(monthName);
  /*
   * Бонус за месяц открывается, когда месяц ЗАКОНЧИЛСЯ, то есть в начале следующего.
   * Поэтому подпись «доступно в …» у сегмента i — это месяц i+1, и для третьего сегмента
   * нужен четвёртый месяц. Раньше у текущего сегмента брался месяц i+1, а у будущих — i,
   * и первые два сегмента показывали один и тот же месяц.
   */
  const availableIn = (index: number) => monthName(index + 1);
  const rewards = [t('profile.bonusTrack'), t('profile.bonusTrack'), t('profile.bonusUnlimited')];
  const CURRENT_BG = 'bg-[linear-gradient(179deg,#6b52c4_0%,#463086_100%)]'; // чуть темнее grad-main
  /*
   * Три состояния сегмента. Раньше «Получить» была просто подписью по ховеру и ничего
   * не делала — бонус нельзя было забрать в принципе. Теперь заработанный, но не забранный
   * месяц — настоящая кнопка, она уходит на бэк и поднимает лимит треков.
   */
  const segs = months.map((_, i) => {
    if (i < claimed) return { def: t('profile.claimed'), hov: t('profile.claimed'), bg: 'bg-grad-main', claimable: false };
    if (i < earned) return { def: rewards[i], hov: t('profile.claim'), bg: CURRENT_BG, claimable: true };
    if (i === earned) return { def: rewards[i], hov: t('profile.availableIn', { month: availableIn(i) }), bg: CURRENT_BG, claimable: false };
    return { def: rewards[i], hov: t('profile.availableIn', { month: availableIn(i) }), bg: '', claimable: false };
  });
  const labelCls = 'text-[24px] font-[400] leading-none text-transparent';
  return (
    <>
      {/* пилюли месяцев над шкалой */}
      <div className="relative mt-[40px] grid grid-cols-3">
        {months.map((m, index) => (
          <span key={index} className={cn(index > 0 && 'ml-[41px]')}>
            <span className="inline-flex h-[35px] w-[80px] items-center justify-center rounded-r15 border border-accent bg-grad-soft-20 text-[24px] font-[400] leading-none text-transparent backdrop-blur-[15px]" style={gradSoft}>
              {m}
            </span>
          </span>
        ))}
      </div>

      {/* шкала: текст меняется на ховере ТОЛЬКО у наведённого сегмента (group/seg) */}
      <div className="relative mt-[25px]">
        <div className="flex h-[60px] overflow-hidden rounded-[20px] bg-grad-soft-20">
          {segs.map((s, i) => {
            const content = (
              <>
                <span className={cn(labelCls, 'transition-opacity duration-150 group-hover/seg:opacity-0')} style={gradSoft}>{s.def}</span>
                <span className={cn(labelCls, 'pointer-events-none absolute inset-0 flex items-center justify-center opacity-0 transition-opacity duration-150 group-hover/seg:opacity-100')} style={gradSoft}>
                  {claiming && s.claimable ? t('common.loading') : s.hov}
                </span>
              </>
            );
            return s.claimable ? (
              <button
                key={i}
                type="button"
                onClick={onClaim}
                disabled={claiming}
                aria-label={t('profile.claim')}
                className={cn('group/seg relative flex flex-1 items-center justify-center transition hover:brightness-125 disabled:cursor-wait', s.bg)}
              >
                {content}
              </button>
            ) : (
              <div key={i} className={cn('group/seg relative flex flex-1 items-center justify-center', s.bg)}>
                {content}
              </div>
            );
          })}
        </div>
        <span aria-hidden="true" className="absolute -top-[57px] bottom-0 left-1/3 w-px bg-text" />
        <span aria-hidden="true" className="absolute -top-[57px] bottom-0 left-2/3 w-px bg-text" />
      </div>
    </>
  );
}

/**
 * Impulse (продукт на год): срок действия.
 *
 * Полосы «сколько года прошло» здесь больше нет: она заполнялась захардкоженными 12% и
 * ничего не отражала — человек видел прогресс-бар, значение которого невозможно объяснить.
 *
 * Карточки персонального менеджера тоже нет (правка владельца): менеджера за ней пока не
 * стоит, кнопка «Написать» вела в никуда, а обещание в ЛК — это обязательство. Вернуть =
 * вернуть этот блок и дать `VITE_MANAGER_NAME` / `VITE_MANAGER_URL` живые значения;
 * ключи `profile.yourManager|managerName|writeManager` в локалях оставлены на месте.
 */
function ImpulseValidity({ expiresAt }: { expiresAt?: string | null }) {
  const { t, i18n } = useTranslation();
  const end = expiresAt ? new Date(expiresAt) : (() => {
    const fallback = new Date();
    fallback.setFullYear(fallback.getFullYear() + 1);
    return fallback;
  })();
  const dateStr = end.toLocaleDateString(i18n.language.startsWith('en') ? 'en-GB' : 'ru-RU');
  return (
    <div className="mt-[40px] flex flex-col gap-[16px]">
      <p className="text-[20px] font-[400] leading-none text-transparent" style={gradSoft}>{t('profile.validUntil', { date: dateStr })}</p>
    </div>
  );
}

/** Нижняя зона тарифа зависит от продукта: Blast — прогресс подписки, Glow/Impulse — своё */
function PaidTariff({ tier, videosTotal, tracksTotal, startedAt, expiresAt, claimed, onClaim, claiming, showBonuses }: {
  tier: string;
  videosTotal: number | null;
  tracksTotal: number | null;
  startedAt?: string;
  expiresAt?: string | null;
  claimed: number;
  onClaim: () => void;
  claiming?: boolean;
  showBonuses: boolean;
}) {
  const { t } = useTranslation();
  // 3-й пункт состава зависит от плана: Blast — безлимит роликов, Glow — CapCut-шаблон
  // (безлимита роликов у Glow нет), Impulse — Менеджмент (иначе дублировал бы «безлимит видео»).
  const thirdPerk = tier === 'GLOW' ? t('pricing.glowB3') : tier === 'IMPULSE' ? t('profile.perkManagement') : t('profile.packUnlimited');
  return (
    <>
      {/* состав пакета: три пункта через «|», распределены по ширине (растяжка) */}
      <div className="mt-[28px] flex h-[60px] items-center justify-between rounded-r15 bg-grad-soft-20 px-[30px]">
        <Bullet icon="pf-scissors.svg">{videosTotal === null ? t('profile.packVideosUnlimited') : t('profile.packVideos', { count: videosTotal })}</Bullet>
        <span className="text-[24px] leading-none text-text-60">|</span>
        <Bullet icon="pf-note.svg">{t('profile.packTracks', { count: tracksTotal ?? 4 })}</Bullet>
        <span className="text-[24px] leading-none text-text-60">|</span>
        <Bullet icon="pf-check.svg">{thirdPerk}</Bullet>
      </div>

      {/* Glow — продукт без прогресса: нижняя зона пустая; Impulse — срок+менеджер; Blast — прогресс */}
      {tier === 'IMPULSE' ? <ImpulseValidity expiresAt={expiresAt} /> : tier === 'GLOW' || !showBonuses ? null : (
        <BlastProgress startedAt={startedAt} claimed={claimed} onClaim={onClaim} claiming={claiming} />
      )}
    </>
  );
}

export function ProfilePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const queryClient = useQueryClient();
  const { push } = useToast();
  const logout = async () => {
    try { await api.logout(); } catch { /* всё равно чистим клиент */ }
    queryClient.clear();
    navigate('/login');
  };
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me });
  const [nick, setNick] = useState<string | null>(null);
  const [editingNick, setEditingNick] = useState(false);
  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const nickInputRef = useRef<HTMLInputElement>(null);

  /*
   * Возврат из OAuth: бэк редиректит сюда с ?tiktok=connected|mock|denied|error.
   * Перечитываем /api/me — от подключения зависит лимит роликов (5 → безлимит).
   */
  const tiktokResult = params.get('tiktok');
  const shownTiktokResult = useRef<string | null>(null);
  useEffect(() => {
    if (!tiktokResult) return;
    // Чистка query ниже асинхронная, а под StrictMode эффект успевает прогнаться дважды
    // с тем же ?tiktok= — без ref-гарда тост о подключении задваивался.
    if (shownTiktokResult.current === tiktokResult) return;
    shownTiktokResult.current = tiktokResult;
    if (tiktokResult === 'connected' || tiktokResult === 'mock') {
      void queryClient.invalidateQueries({ queryKey: ['me'] });
      push({
        variant: 'success',
        title: t('profile.tiktokConnected'),
        text: tiktokResult === 'mock' ? t('profile.tiktokMock') : undefined
      });
    } else if (tiktokResult === 'denied') {
      push({ variant: 'error', title: t('profile.tiktokDenied') });
    } else if (tiktokResult === 'guard_error') {
      // Проверка «аккаунт TikTok уже использовался» не смогла отработать — подключение
      // не состоялось намеренно (fail-closed), и человеку надо сказать именно это.
      push({ variant: 'error', title: t('profile.tiktokGuardError'), text: t('profile.tiktokGuardErrorText') });
    } else {
      push({ variant: 'error', title: t('profile.tiktokError') });
    }
    // убираем query, чтобы тост не всплывал при каждом рендере
    params.delete('tiktok');
    setParams(params, { replace: true });
  }, [tiktokResult, queryClient, push, t, params, setParams]);

  const avatarMutation = useMutation({
    mutationFn: api.uploadAvatar,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['me'] });
      push({ variant: 'success', title: t('profile.avatarUpdated') });
    }
  });

  const nickMutation = useMutation({
    mutationFn: (artistNick: string) => api.updateProfile({ artistNick }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['me'] });
      setEditingNick(false);
    },
    onError: () => push({ variant: 'error', title: t('simple.error') })
  });

  /*
   * Возврат с привязки Google: бэк редиректит сюда с ?auth=<исход>. Тот же приём, что и
   * у TikTok выше — ref-гард против дубля тоста под StrictMode.
   */
  const authResult = params.get('auth');
  const shownAuthResult = useRef<string | null>(null);
  useEffect(() => {
    if (!authResult || shownAuthResult.current === authResult) return;
    shownAuthResult.current = authResult;
    if (authResult === 'google_linked') {
      void queryClient.invalidateQueries({ queryKey: ['me'] });
      push({ variant: 'success', title: t('profile.googleLinked') });
    } else if (authResult === 'google_taken') {
      push({ variant: 'error', title: t('profile.googleTaken') });
    } else if (authResult === 'google_blocked') {
      push({ variant: 'error', title: t('profile.googleBlocked') });
    } else if (authResult !== 'denied') {
      push({ variant: 'error', title: t('auth.googleError') });
    }
    params.delete('auth');
    setParams(params, { replace: true });
  }, [authResult, params, push, queryClient, setParams, t]);

  const providersQuery = useQuery({ queryKey: ['auth-providers'], queryFn: api.authProviders, staleTime: 5 * 60_000 });

  const unlinkMutation = useMutation({
    mutationFn: api.unlinkGoogle,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['me'] });
      push({ variant: 'info', title: t('profile.googleUnlinked') });
    },
    // 409 — это единственный способ войти, отвязывать нельзя
    onError: (error) => push({
      variant: 'error',
      title: error instanceof ApiError && error.status === 409 ? t('profile.googleLastProvider') : t('simple.error')
    })
  });

  const claimMutation = useMutation({
    mutationFn: api.claimBonus,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['me'] });
      push({ variant: 'success', title: t('profile.claimOk') });
    },
    onError: () => push({ variant: 'error', title: t('profile.claimFail') })
  });

  const disconnectMutation = useMutation({
    mutationFn: api.disconnectTiktok,
    onSuccess: async () => {
      setDisconnectOpen(false);
      await queryClient.invalidateQueries({ queryKey: ['me'] });
      push({ variant: 'info', title: t('profile.tiktokDisconnected') });
    },
    onError: () => push({ variant: 'error', title: t('simple.error') })
  });

  const deleteMutation = useMutation({
    mutationFn: api.deleteAccount,
    onSuccess: () => {
      queryClient.clear();
      navigate('/register', { replace: true });
    },
    onError: () => push({ variant: 'error', title: t('profile.deleteFailed') })
  });

  const onAvatar = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) avatarMutation.mutate(file);
  };

  /*
   * Фокус ставился через requestAnimationFrame сразу после setEditingNick(true). React 18
   * коммитит стейт своим планировщиком, и к моменту rAF <input> ещё не смонтирован — ref пуст,
   * фокус не приходит. Поле открывалось «мёртвым»: печатать некуда, focusout не срабатывает,
   * ник не сохраняется. Фокусируем из эффекта — он гарантированно идёт после коммита.
   * Эффект стоит выше ранних return'ов: порядок хуков не должен зависеть от состояния запроса.
   */
  useEffect(() => {
    if (editingNick) nickInputRef.current?.focus();
  }, [editingNick]);

  if (queryDown(meQuery)) return <QueryError query={meQuery} className="min-h-[620px]" />;
  if (meQuery.isLoading || !meQuery.data) return <Skeleton className="h-full min-h-[620px]" />;

  const { user, subscription, tiktok } = meQuery.data;
  const googleLinked = Boolean(user.googleEmail);
  const googleAvailable = Boolean(providersQuery.data?.google);
  const googleBlocked = Boolean(providersQuery.data?.googleBlocked);
  // Бесплатный тариф в типах — TRIAL (Figma W43); остальное — платные пакеты.
  // Превью платного состояния (dev): ?plan=blast|glow|impulse (или ?state=paid = Blast) —
  // показывает подписку с числами конкретного плана без реальной покупки.
  // tracks — базовый лимит плана (в составе «до N треков»); trackLimit — эффективный лимит
  // в «Лимитах» с учётом бонуса месяца (Blast: 4 базовых + 1 полученный = 5, как в Figma).
  const PLAN_SPECS: Record<string, { videos: number | null; tracks: number; trackLimit: number }> = {
    BLAST: { videos: 100, tracks: 4, trackLimit: 5 },
    GLOW: { videos: 400, tracks: 10, trackLimit: 10 },
    IMPULSE: { videos: null, tracks: 24, trackLimit: 24 }
  };
  const previewTier = import.meta.env.DEV
    ? (params.get('plan')?.toUpperCase() || (params.get('state') === 'paid' ? 'BLAST' : ''))
    : '';
  const previewSpec = PLAN_SPECS[previewTier] ?? null;
  const paidPreview = previewSpec !== null;
  const paid = paidPreview || (subscription.isActive && subscription.tier !== 'TRIAL');
  const paidTier = paidPreview ? previewTier : subscription.tier;
  const videosTotal = previewSpec ? previewSpec.videos : subscription.creditsTotal;
  const tracksTotal = previewSpec ? previewSpec.tracks : subscription.tracksTotal;
  const tracksLimit = previewSpec ? previewSpec.trackLimit : subscription.tracksTotal;
  // без фолбэка на имя: пустой ник — это пустой ник, в поле показываем подсказку
  const artistNick = nick ?? user.artistNick ?? '';
  const fullName = [user.name, user.surname].filter(Boolean).join(' ').trim();
  // Аватар из TikTok подтягивается автоматически, пока юзер не загрузил свой
  const avatarSrc = user.avatarUrl || tiktok?.avatarUrl || null;
  const initial = (fullName || artistNick || '?').slice(0, 1).toUpperCase();

  const startNickEdit = () => {
    if (editingNick) return;
    setNick(artistNick);
    setEditingNick(true);
  };
  const saveNick = () => {
    const value = artistNick.trim();
    // пустой ник не сохраняем, но и поле не запираем — возвращаем прежнее значение
    if (!value) {
      setNick(null);
      setEditingNick(false);
      return;
    }
    if (value === (user.artistNick ?? '')) setEditingNick(false);
    else nickMutation.mutate(value);
  };
  const onNickKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') event.currentTarget.blur();
    if (event.key === 'Escape') {
      setNick(null);
      setEditingNick(false);
    }
  };

  return (
    /*
     * Раньше страница была прибита к высоте окна (h: 100dvh − поля) + overflow-hidden:
     * карточка оплаты просто обрезалась и доскроллить до неё было нельзя. Теперь колонка
     * растёт по контенту и скроллится страницей, как в админке.
     */
    <div className="flex min-h-0 flex-1 flex-col gap-[20px] pb-space-6 md:pt-[calc(var(--rail-pad-y)_-_var(--space-6))]">
      {/* шапка 1192×202: аватар 120 в кольце, имя 24, ник 32 с карандашом, справа TikTok */}
      <section className="card-2 flex h-[202px] shrink-0 items-center gap-[40px] px-[40px]">
        <label className="relative h-[120px] w-[120px] shrink-0 cursor-pointer">
          <span className="absolute inset-0 rounded-full border-2 border-accent-light" aria-hidden="true" />
          <span className="absolute inset-[8px] overflow-hidden rounded-full bg-accent-20">
            {/* свой аватар важнее подтянутого из TikTok; инициал — последний фолбэк */}
            {avatarSrc
              ? <img src={avatarSrc} alt="" className="h-full w-full object-cover" />
              : <span className="flex h-full w-full items-center justify-center text-[32px] font-[400] text-text">{initial}</span>}
          </span>
          <input type="file" accept="image/*" className="sr-only" onChange={onAvatar} />
        </label>

        <div className="min-w-0 flex-1">
          {/* Имени может не быть: вход через Telegram не спрашивает ФИО. Тогда вместо
              пустой строки — прямое приглашение заполнить профиль. */}
          <p className="truncate text-[24px] font-[350] leading-[29px] text-transparent" style={gradSoft}>
            {fullName || t('profile.noName')}
          </p>
          <div className="mt-[5px] flex items-center gap-[12px]">
            {editingNick ? (
              <span className="inline-grid min-w-[1ch] max-w-[calc(100%-36px)] rounded-r10 bg-accent-10 px-[8px] shadow-[inset_0_0_0_1px_var(--accent-light)]">
                <span aria-hidden="true" className="invisible col-start-1 row-start-1 whitespace-pre text-[32px] font-[400] leading-[38px]">{artistNick || ' '}</span>
                <input
                  ref={nickInputRef}
                  value={artistNick}
                  onChange={(e) => setNick(e.target.value)}
                  onBlur={saveNick}
                  onKeyDown={onNickKeyDown}
                  aria-label={t('profile.artistNick')}
                  placeholder={t('profile.nickLabel')}
                  className="col-start-1 row-start-1 min-w-0 w-full bg-transparent text-[32px] font-[400] leading-[38px] text-transparent outline-none placeholder:text-text-40 focus-visible:outline-none"
                  /*
                   * caretColor обязателен: текст залит градиентом через bg-clip-text,
                   * а значит color: transparent — вместе с текстом прозрачной становилась
                   * и каретка. Поле выглядело неактивным, пока не начнёшь печатать.
                   */
                  style={{ ...gradLight, caretColor: 'var(--accent-light)' }}
                />
              </span>
            ) : artistNick ? (
              <span className="whitespace-nowrap text-[32px] font-[400] leading-[38px] text-transparent" style={gradLight}>{artistNick}</span>
            ) : (
              /* псевдонима ещё нет — вместо него подсказка прямо в поле, у карандаша */
              <button
                type="button"
                onClick={startNickEdit}
                className="whitespace-nowrap text-[32px] font-[400] leading-[38px] text-text-40 transition hover:text-text-60 focus-visible:outline-none"
              >
                {t('profile.nickLabel')}
              </button>
            )}
            <button type="button" onClick={startNickEdit} aria-label={t('profile.artistNick')} className="flex h-[24px] w-[24px] shrink-0 items-center justify-center rounded-[6px] transition hover:bg-accent-10">
              <FigIcon name="pf-profile-pencil.svg" h={11} className="opacity-80" />
            </button>
          </div>
        </div>

        {/*
         * Порядок по важности: TikTok — ключевое действие и остаётся крупной кнопкой,
         * Google и выход — квадраты 60×60 только с иконкой. Раньше «Выйти» была текстовой
         * кнопкой той же ширины, что и подключение TikTok, и тянула на себя внимание.
         */}
        <div className="flex shrink-0 items-center gap-[12px]">
          <TiktokButton
            connected={Boolean(tiktok) || paidPreview}
            onClick={tiktok && !paidPreview ? () => setDisconnectOpen(true) : undefined}
          />

          {googleLinked ? (
            <button
              type="button"
              onClick={() => unlinkMutation.mutate()}
              disabled={unlinkMutation.isPending}
              aria-label={t('profile.googleDisconnect')}
              title={t('profile.googleConnected', { email: user.googleEmail })}
              className="flex h-[60px] w-[60px] shrink-0 items-center justify-center rounded-r15 border border-accent-light bg-accent-10 text-accent-light transition hover:brightness-125 disabled:opacity-50"
            >
              <SvgMaskIcon src="/assets/icon-google.svg" style={{ width: 22, height: 22, color: 'currentColor' }} />
            </button>
          ) : googleAvailable ? (
            <a
              href={api.googleLinkUrl()}
              aria-label={t('profile.googleConnect')}
              title={`${t('profile.googleConnect')} — ${t('profile.googleWhy')}`}
              /* Не подключён — серый: и лого, и обводка. Цветным становится только
                 подключённый, чтобы состояние читалось с одного взгляда. */
              className="flex h-[60px] w-[60px] shrink-0 items-center justify-center rounded-r15 border border-[rgba(246,245,253,0.14)] text-text-40 transition hover:border-accent-light hover:text-accent-light"
            >
              <SvgMaskIcon src="/assets/icon-google.svg" style={{ width: 22, height: 22, color: 'currentColor' }} />
            </a>
          ) : googleBlocked || import.meta.env.DEV ? (
            /* Регион под запретом — кнопку показываем неактивной, а не прячем:
               иначе «почему у меня нет Google» превращается в вопрос в поддержку.
               В деве тем же способом показываем и «ключей нет»: молча исчезнувшая кнопка
               неотличима от поломки, а так сразу видно, что дело в GOOGLE_* в backend/.env.
               В проде ненастроенный Google по-прежнему просто скрыт. */
            <span
              aria-disabled="true"
              title={googleBlocked ? t('profile.googleBlocked') : t('profile.googleNotConfigured')}
              className="flex h-[60px] w-[60px] shrink-0 cursor-not-allowed items-center justify-center rounded-r15 border border-[rgba(246,245,253,0.12)] text-text-40 opacity-50"
            >
              <SvgMaskIcon src="/assets/icon-google.svg" style={{ width: 22, height: 22, color: 'currentColor' }} />
            </span>
          ) : null}

          <button
            type="button"
            onClick={logout}
            aria-label={t('profile.logout')}
            title={t('profile.logout')}
            className="flex h-[60px] w-[60px] shrink-0 items-center justify-center rounded-r15 border border-[rgba(246,245,253,0.2)] text-text-60 transition hover:border-accent-light hover:text-text"
          >
            <SvgMaskIcon src="/assets/icon-logout.svg" style={{ width: 24, height: 24, color: 'currentColor' }} />
          </button>
        </div>
      </section>

      {/* «Лимиты» 1192×296 */}
      <section className="card-2 h-[296px] shrink-0 p-[40px]">
        <div className="flex items-center justify-between gap-space-4">
          <h2 className="flex items-center gap-[16px] text-[32px] font-[400] leading-[38px] text-text">
            <FigIcon name="pf-limit-note.svg" h={19} />
            {t('limits.title')}
          </h2>
          <Link to="/app/pricing" className="group flex items-center gap-[12px] text-[24px] font-[400] leading-[29px] text-transparent transition hover:brightness-125" style={{ backgroundImage: 'var(--grad-main)', WebkitBackgroundClip: 'text', backgroundClip: 'text' }}>
            {t('profile.update')}
            <SvgMaskIcon src="/assets/figma/home-arrow.svg" className="transition-transform duration-150 group-hover:translate-x-[2px]" style={{ width: 8.782, height: 15.464, background: 'var(--grad-main)' }} />
          </Link>
        </div>

        <div className="mt-[40px]">
          <LimitRow label={t('limits.tracks')} used={paidPreview ? 1 : subscription.tracksUsed} total={tracksLimit} />
        </div>
        <span aria-hidden="true" className="mb-[39px] mt-[40px] block h-px w-full bg-[rgba(246,245,253,0.2)]" />
        <LimitRow label={t('limits.videos')} used={paidPreview ? 50 : subscription.creditsUsed} total={videosTotal} />
      </section>

      {/* «Тариф» — здесь расходятся W43 и W44. Высота по контенту: у Glow нет подписочной
          шкалы, и фиксированные 366px оставляли под составом пакета пустую полосу. */}
      <section className="card-2 shrink-0 overflow-hidden p-[40px]">
        <h2 className="flex items-center gap-[14px] text-[32px] font-[400] leading-[38px]">
          <FigIcon name="pf-tariff-arrow.svg" h={20} />
          <span className="text-text">{t('profile.tariff')}</span>
          <span className="text-text-80">{t(`profile.tier.${paid ? paidTier : 'TRIAL'}`)}</span>
        </h2>
        {paid ? (
          <PaidTariff
            tier={paidTier}
            videosTotal={videosTotal}
            tracksTotal={tracksTotal}
            startedAt={subscription.startedAt}
            expiresAt={subscription.expiresAt}
            claimed={subscription.bonusesClaimed ?? 0}
            onClaim={() => claimMutation.mutate()}
            claiming={claimMutation.isPending}
            showBonuses={Boolean(meQuery.data?.capabilities?.subscriptionBonuses)}
          />
        ) : <FreeTariff />}
      </section>

      {/* Состояние оплаты и отмена подписки — на платном плане, сразу под тарифом */}
      <BillingCard subscription={subscription} />

      <section className="card-2 flex shrink-0 items-center justify-between gap-[24px] p-[40px]">
        <div>
          <h2 className="text-[24px] font-[400] text-text">{t('profile.deleteTitle')}</h2>
          <p className="mt-[8px] max-w-[720px] text-[16px] leading-[22px] text-text-60">{t('profile.deleteText')}</p>
        </div>
        <button type="button" className="soft-btn h-[52px] shrink-0 px-[22px] text-[16px] text-[var(--warning)]" onClick={() => setDeleteOpen(true)}>
          {t('profile.deleteAction')}
        </button>
      </section>

      <Modal open={disconnectOpen} title={t('profile.disconnectTiktokTitle')} onClose={() => setDisconnectOpen(false)}>
        <p className="text-[18px] leading-[25px] text-text-60">{t('profile.disconnectTiktokText')}</p>
        <div className="mt-[28px] flex justify-end gap-[12px]">
          <button type="button" className="soft-btn h-[52px] px-[22px]" onClick={() => setDisconnectOpen(false)}>{t('common.cancel')}</button>
          <button type="button" className="soft-btn h-[52px] px-[22px] text-[var(--warning)]" disabled={disconnectMutation.isPending} onClick={() => disconnectMutation.mutate()}>{t('profile.disconnectTiktokAction')}</button>
        </div>
      </Modal>

      <Modal open={deleteOpen} title={t('profile.deleteTitle')} onClose={() => setDeleteOpen(false)}>
        <p className="text-[18px] leading-[25px] text-text-60">{t('profile.deleteConfirm')}</p>
        <div className="mt-[28px] flex justify-end gap-[12px]">
          <button type="button" className="soft-btn h-[52px] px-[22px]" onClick={() => setDeleteOpen(false)}>{t('common.cancel')}</button>
          <button type="button" className="soft-btn h-[52px] px-[22px] text-[var(--warning)]" disabled={deleteMutation.isPending} onClick={() => deleteMutation.mutate()}>{t('profile.deleteAction')}</button>
        </div>
      </Modal>
    </div>
  );
}
