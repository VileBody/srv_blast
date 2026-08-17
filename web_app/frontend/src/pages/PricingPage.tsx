import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { PackageType } from '../lib/types';
import { cn } from '../lib/cn';
import { LEGAL_LINKS } from '../lib/legal';
import { useToast } from '../contexts/ToastContext';
import { FigIcon } from '../components/ui/FigIcon';

/*
 * Тарифы (Figma W45; W50 — ховер на кнопку цены).
 * Карточка-фрейм 1192×904 поверх зоны контента: шапка «Расширить возможности» + 3 карты
 * 357×736 с шагом 20 (40 / 417 / 795). Верх карты — «пиксельное» число под шумом частиц
 * и фейд в тело карточки; ниже бейджи, заголовок, буллеты, согласие и кнопка цены.
 */

const gradLight = {
  backgroundImage: 'linear-gradient(187deg, #f6f5fd 8.5%, rgba(246,245,253,0.8) 94.6%)',
  WebkitBackgroundClip: 'text',
  backgroundClip: 'text'
} as const;

const gradWhitey = {
  backgroundImage: 'var(--grad-whitey)',
  WebkitBackgroundClip: 'text',
  backgroundClip: 'text'
} as const;

interface Bullet {
  icon: 'scissors' | 'note' | 'check';
  text: string;
  /** «?»-подсказка справа от строки (Figma 752:448) */
  hint?: string;
}

interface Plan {
  type: PackageType;
  /** крупное число сверху карты. Основной путь — hi-res PNG из Figma (несёт сложный эффект
      Texture, который не собрать на CSS). Если PNG нет/не загрузился — фолбэк на текст с зерном. */
  number: string;
  /** имя PNG-ассета числа в /assets/figma (прозрачный фон, hi-res). Пусто → рендер текстом. */
  numberImg?: string;
  /** шейп-логотип нижним слоем (Rectangle 482/588/Star 10) — создаёт глубину, скейл от центра */
  shape: { asset: string; left: number; top: number; w: number; h: number };
  badge: string;
  /** логотип тарифа и его размер — в Figma у каждой карты свои (25×25 / 28×21 / 18.8×18) */
  logo: string;
  logoH: number;
  /** ширина заголовка из макета: Blast 208, Glow 257, Impulse 225 */
  titleW: number;
  kind: 'subscription' | 'product';
  kindAsset: string;
  title: string;
  bullets: Bullet[];
  price: string;
  perMonth?: boolean;
}

/** «?» рядом с буллетом (Figma 752:449 + 752:450) */
function Hint({ text }: { text: string }) {
  return (
    <span className="relative ml-[10px] inline-flex h-[20px] w-[20px] shrink-0 items-center justify-center" title={text}>
      <FigIcon name="pr-hint.svg" h={20} className="absolute inset-0" />
      <span className="relative text-[12px] font-[350] leading-none text-transparent" style={gradWhitey}>?</span>
    </span>
  );
}

/** Карта тарифа 357×736 r15 (Figma 752:338). `current` — этот тариф уже куплен. */
function PlanCard({ plan, agreed, onAgree, recurrentAgreed, onRecurrentAgree, onBuy, busy, current }: {
  plan: Plan;
  agreed: boolean;
  onAgree: (v: boolean) => void;
  recurrentAgreed: boolean;
  onRecurrentAgree: (v: boolean) => void;
  onBuy: () => void;
  busy?: boolean;
  /** тариф уже куплен: карта помечена, купить его повторно нельзя */
  current?: boolean;
}) {
  const { t } = useTranslation();
  const [hoverPrice, setHoverPrice] = useState(false);
  const [attention, setAttention] = useState(false);
  const [numImgBroken, setNumImgBroken] = useState(false);
  const purchaseAllowed = agreed && (plan.kind !== 'subscription' || recurrentAgreed);

  return (
    <div className="relative h-[736px] min-w-[357px] overflow-hidden rounded-r15 bg-grad-soft-20">
      {/* нижний слой — шейп-логотип (глубина). Скейл от центра: transformOrigin center,
          позиция/размер из Figma. left задаётся от центра карты, чтобы фигура не «уползала». */}
      <img
        src={`/assets/figma/${plan.shape.asset}`}
        alt=""
        aria-hidden
        className="pointer-events-none absolute left-1/2 max-w-none -translate-x-1/2 select-none"
        style={{ top: plan.shape.top, width: plan.shape.w, height: plan.shape.h, transformOrigin: 'center' }}
      />
      {/* Rectangle 788: затемнение под цифрой, чтобы она читалась на шейпе */}
      <span aria-hidden="true" className="pointer-events-none absolute left-[-22px] top-[34px] h-[359px] w-[904px]" style={{ backgroundImage: 'linear-gradient(rgba(16,9,34,0) 0%, rgba(16,9,34,0.9) 82.5%, rgba(16,9,34,0.95) 91.2%, #100922 100%)' }} />
      {/* цифра. Основной путь — hi-res PNG из Figma (несёт сложный эффект Texture). Центрируется
          в зоне 240px, прозрачный фон → нет проплешины, hi-res → нет мути. Если PNG нет/битый —
          фолбэк: текст 200px с плёночным зерном (bg-clip:text). */}
      <span className="pointer-events-none absolute left-0 top-[125px] flex h-[240px] w-full -translate-y-1/2 select-none items-center justify-center">
        {plan.numberImg && !numImgBroken ? (
          <img
            src={`/assets/figma/${plan.numberImg}`}
            alt={plan.number}
            aria-hidden
            onError={() => setNumImgBroken(true)}
            className="max-h-[210px] w-auto max-w-[calc(100%-40px)] object-contain"
          />
        ) : (
          <span
            aria-hidden="true"
            className="text-[200px] font-[400] leading-none text-transparent"
            style={{
              backgroundImage: 'url(/assets/figma/pr-texture.svg), linear-gradient(187deg, #f6f5fd 8.5%, rgba(246,245,253,0.85) 94.6%)',
              backgroundSize: '160px 160px, cover',
              backgroundRepeat: 'repeat, no-repeat',
              WebkitBackgroundClip: 'text',
              backgroundClip: 'text'
            }}
          >
            {plan.number}
          </span>
        )}
      </span>
      <span aria-hidden="true" className="pointer-events-none absolute left-0 top-[80px] h-[243px] w-full" style={{ backgroundImage: 'linear-gradient(rgba(39,28,70,0) 0%, rgba(39,28,70,0.9) 82.5%, rgba(39,28,70,0.95) 91.2%, #271c46 100%)' }} />
      <span aria-hidden="true" className="pointer-events-none absolute left-0 top-[323px] h-[413px] w-full bg-gradient-to-b from-[#271c46] to-[#271d46]" />

      {/* чип «Роликов» 150×60 */}
      <span className="absolute right-[28px] top-[100px] flex h-[60px] w-[150px] items-center justify-center gap-[12px] rounded-r15 border border-accent-light backdrop-blur-[50px]" style={{ backgroundImage: 'linear-gradient(175deg, rgba(21,15,37,0.78) 8.4%, rgba(17,13,29,0.78) 97.9%)' }}>
        <FigIcon name="pr-tag.svg" w={20} className="rotate-[-22.23deg]" />
        <span className="text-[20px] font-[400] leading-none text-transparent" style={gradWhitey}>{t('pricing.clips')}</span>
      </span>

      {/* бейдж тарифа 140×60 + тип 150×60 */}
      <span className="absolute left-[28px] top-[249px] flex h-[60px] w-[140px] items-center justify-center gap-[8px] rounded-r15 bg-grad-soft-10 backdrop-blur-[25px]">
        <FigIcon name={plan.logo} h={plan.logoH} className="shrink-0" />
        <span className="text-[24px] font-[400] leading-none tracking-[-0.5px] text-text">{plan.badge}</span>
      </span>
      <span className="absolute left-[179px] top-[249px] flex h-[60px] w-[150px] items-center justify-center overflow-hidden rounded-r15">
        <FigIcon name={plan.kindAsset} w={150} className="absolute inset-0" />
        <span className="relative text-[24px] font-[400] leading-[30px] tracking-[-0.5px] text-text-80">
          {plan.kind === 'subscription' ? t('pricing.subscription') : t('pricing.product')}
        </span>
      </span>

      {/* заголовок 24 / 208 (2 строки) */}
      <h2 className="absolute left-[28px] top-[337px] text-[24px] font-[400] leading-normal text-text" style={{ width: plan.titleW }}>{plan.title}</h2>

      {/* буллеты: шаг 42, иконка ~x=31, текст x=60 */}
      <div className="absolute left-[28px] top-[423px] w-[301px]">
        {plan.bullets.map((b, i) => (
          <span key={b.text} className="flex h-[30px] items-center" style={{ marginTop: i ? 12 : 0 }}>
            <span className="flex w-[32px] shrink-0 items-center justify-center">
              <FigIcon
                name={`pr-${b.icon}.svg`}
                h={b.icon === 'note' ? 16 : b.icon === 'scissors' ? 15 : 15.5}
                className={cn(
                  b.icon === 'scissors' && '-rotate-[5deg]',
                  b.icon === 'note' && '-translate-x-px',
                  b.icon === 'check' && '-translate-x-[4px] -translate-y-[3px] rotate-45'
                )}
              />
            </span>
            <span className="whitespace-nowrap text-[16px] font-[400] leading-[30px] text-text">{b.text}</span>
            {b.hint && <Hint text={b.hint} />}
          </span>
        ))}
      </div>

      {/* Тариф уже куплен: вместо согласия и цены — статус. Раньше купленный план ничем
          не отличался от остальных, и было непонятно, за что уже заплачено. */}
      {current ? (
        <div className="absolute left-[28px] top-[560px] flex w-[calc(100%-56px)] items-start gap-[12px]">
          <span className="mt-[5px] flex h-[20px] w-[20px] shrink-0 items-center justify-center rounded-[5px] bg-accent-light" aria-hidden="true">
            <svg viewBox="0 0 12 10" width="11" height="9" fill="none"><path d="M1 5l3.2 3.2L11 1.4" stroke="#05010f" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
          </span>
          <span className="text-[16px] font-[400] leading-[30px] text-text-80">{t('pricing.yourPlanNote')}</span>
        </div>
      ) : (
      <label className={cn('absolute left-[28px] top-[560px] flex w-[calc(100%-56px)] cursor-pointer items-start gap-[12px] rounded-r10 transition', attention && 'bg-[rgba(139,111,230,.14)] shadow-[0_0_0_8px_rgba(139,111,230,.14)]')}>
        <input type="checkbox" className="sr-only" checked={agreed} onChange={(e) => onAgree(e.target.checked)} />
        <span className={cn('mt-[5px] flex h-[20px] w-[20px] shrink-0 items-center justify-center rounded-[5px] border border-text transition-all', agreed && 'bg-text', attention && !agreed && 'border-accent-light shadow-[0_0_14px_rgba(139,111,230,.9)]')} aria-hidden="true">
          {agreed && (
            <svg viewBox="0 0 12 10" width="11" height="9" fill="none" aria-hidden="true">
              <path d="M1 5l3.2 3.2L11 1.4" stroke="#05010f" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          )}
        </span>
        {/* Документы — настоящие ссылки: соглашаться с тем, что нельзя открыть, нельзя.
            stopPropagation, иначе клик по ссылке ещё и переключал бы чекбокс. */}
        <span className={cn('text-[16px] font-[400] leading-[30px] text-text-80 transition-colors', attention && !agreed && 'text-text')}>
          {t('pricing.agreePrefix')}{' '}
          <a
            href={LEGAL_LINKS.policy}
            target="_blank"
            rel="noreferrer"
            onClick={(event) => event.stopPropagation()}
            className="text-text underline underline-offset-2 transition hover:text-accent-light"
          >
            {t('pricing.policy')}
          </a>{' '}
          {t('pricing.and')}{' '}
          <a
            href={LEGAL_LINKS.offer}
            target="_blank"
            rel="noreferrer"
            onClick={(event) => event.stopPropagation()}
            className="text-text underline underline-offset-2 transition hover:text-accent-light"
          >
            {t('pricing.offer')}
          </a>
        </span>
      </label>
      )}

      {!current && plan.kind === 'subscription' && (
        <label className="absolute left-[28px] top-[620px] flex w-[calc(100%-56px)] cursor-pointer items-center gap-[12px]">
          <input type="checkbox" className="sr-only" checked={recurrentAgreed} onChange={(event) => onRecurrentAgree(event.target.checked)} />
          <span className={cn('flex h-[20px] w-[20px] shrink-0 items-center justify-center rounded-[5px] border border-text transition-all', recurrentAgreed && 'bg-text', attention && !recurrentAgreed && 'border-accent-light shadow-[0_0_14px_rgba(139,111,230,.9)]')} aria-hidden="true">
            {recurrentAgreed && (
              <svg viewBox="0 0 12 10" width="11" height="9" fill="none" aria-hidden="true"><path d="M1 5l3.2 3.2L11 1.4" stroke="#05010f" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
            )}
          </span>
          <span className="text-[13px] leading-[16px] text-text-80">{t('pricing.recurrentConsent')}</span>
        </label>
      )}

      {/* кнопка цены 301×60; W50: по ховеру цена → «Купить», фон = --grad-main.
          На купленном тарифе кнопка становится статусом и не покупает повторно. */}
      {current ? (
        <span className="absolute inset-x-[28px] bottom-[28px] flex h-[60px] items-center justify-center gap-[10px] rounded-r15 bg-grad-main">
          <FigIcon name="pr-check.svg" h={15.5} className="-translate-y-[3px] rotate-45" />
          <span className="text-[24px] font-[400] leading-none text-transparent" style={gradLight}>{t('pricing.yourPlan')}</span>
        </span>
      ) : (
      <button
        type="button"
        disabled={busy}
        aria-disabled={!purchaseAllowed || busy}
        onClick={() => {
          if (!purchaseAllowed) {
            setAttention(true);
            window.setTimeout(() => setAttention(false), 1600);
            return;
          }
          onBuy();
        }}
        onMouseEnter={() => setHoverPrice(true)}
        onMouseLeave={() => setHoverPrice(false)}
        title={purchaseAllowed ? undefined : t('pricing.agreeFirst')}
        /*
         * Ховер работает ВСЕГДА, даже без галочки: раньше без неё кнопка вообще не отвечала,
         * и человек не понимал, что она вообще покупает. Теперь «Купить» показывается, но
         * без согласия выглядит неактивной и клик уводит внимание на чекбокс, а не покупает.
         */
        className={cn(
          'absolute inset-x-[28px] bottom-[28px] flex h-[60px] items-center justify-center rounded-r15 border border-accent-light transition disabled:cursor-wait disabled:opacity-60',
          hoverPrice ? (purchaseAllowed ? 'bg-grad-main' : 'cursor-not-allowed bg-grad-soft-20') : 'bg-grad-soft-10'
        )}
      >
        {hoverPrice ? (
          <span className={cn('flex items-center gap-[10px] text-[24px] font-[400] leading-none', purchaseAllowed ? 'text-transparent' : 'text-text-40')} style={purchaseAllowed ? gradLight : undefined}>
            {t('pricing.buy')}
            {!purchaseAllowed && <span className="text-[14px] leading-none text-text-40">{t('pricing.agreeFirst')}</span>}
          </span>
        ) : (
          <span className="flex translate-y-px items-center gap-[8px]">
            <span className="text-[32px] font-[400] leading-[38px] text-transparent" style={gradLight}>{plan.price}</span>
            {plan.perMonth && <span className="text-[16px] font-[400] leading-[30px] tracking-[-0.5px] text-text-80">{t('pricing.perMonth')}</span>}
          </span>
        )}
      </button>
      )}

      {/* Обводка купленного тарифа — ОТДЕЛЬНЫМ слоем поверх всего. Инсет-тень на самой карте
          перекрывалась непрозрачными градиент-слоями, и от рамки оставались куски по краям. */}
      {current && (
        <span aria-hidden="true" className="pointer-events-none absolute inset-0 z-[4] rounded-r15 shadow-[inset_0_0_0_2px_var(--accent-light)]" />
      )}
    </div>
  );
}

export function PricingPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const { push } = useToast();
  const [agreed, setAgreed] = useState<Record<string, boolean>>({});
  const [recurrentAgreed, setRecurrentAgreed] = useState<Record<string, boolean>>({});

  useEffect(() => {
    const payment = searchParams.get('payment');
    if (!payment) return;
    push({
      variant: payment === 'success' ? 'success' : 'error',
      title: t(payment === 'success' ? 'pricing.paymentSuccess' : 'pricing.paymentFailed')
    });
    if (payment === 'success') void queryClient.invalidateQueries({ queryKey: ['me'] });
    const next = new URLSearchParams(searchParams);
    next.delete('payment');
    setSearchParams(next, { replace: true });
  }, [push, queryClient, searchParams, setSearchParams, t]);

  // Просмотр тарифов — отдельный шаг воронки: без него не понять, доходят ли люди до
  // цены вообще, или отваливаются раньше. Ошибку трекинга глушим: она не должна ломать экран.
  useEffect(() => {
    void api.trackEvent('pricing_viewed').catch(() => {});
  }, []);

  // какой тариф уже куплен: TRIAL — «бесплатный», его на этой странице нет
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: 15_000 });
  const subscription = meQuery.data?.subscription;
  const currentTier = subscription && subscription.isActive && subscription.tier !== 'TRIAL' ? subscription.tier : null;
  const orderMutation = useMutation({
    mutationFn: (packageType: PackageType) => api.createOrder({
      packageType,
      recurrentAccepted: packageType === 'BLAST' ? Boolean(recurrentAgreed.BLAST) : false
    }),
    onSuccess: (data) => {
      window.location.assign(data.paymentUrl);
    },
    onError: () => push({ variant: 'error', title: t('pricing.orderFailed') })
  });

  const plans: Plan[] = [
    {
      // Blast: Rect 482 (Figma 875×406 @top -18) → +30% и выше (правка): 1137×528, поднят
      type: 'BLAST', number: '100', numberImg: 'pr-num-blast.png', shape: { asset: 'pr-shape-blast.svg', left: 0, top: -70, w: 1137, h: 528 }, badge: 'Blast', kind: 'subscription', kindAsset: 'pr-kind-blast.svg',
      logo: 'pr-logo-blast.svg', logoH: 25, titleW: 208,
      title: t('pricing.blastTitle'),
      bullets: [
        { icon: 'scissors', text: t('pricing.blastB1') },
        { icon: 'note', text: t('pricing.blastB2'), hint: t('pricing.hintTracks') },
        { icon: 'check', text: t('pricing.blastB3') }
      ],
      price: '1 990₽', perMonth: true
    },
    {
      // Glow: Rect 588 (Figma 479×352 @top 4) → +30% и выше: 623×458, поднят
      type: 'GLOW', number: '400', numberImg: 'pr-num-glow.png', shape: { asset: 'pr-shape-glow.svg', left: 0, top: -60, w: 623, h: 458 }, badge: 'Glow', kind: 'product', kindAsset: 'pr-kind-glow.svg',
      logo: 'pr-logo-glow.svg', logoH: 21, titleW: 257,
      title: t('pricing.glowTitle'),
      bullets: [
        { icon: 'scissors', text: t('pricing.glowB1') },
        { icon: 'note', text: t('pricing.glowB2'), hint: t('pricing.hintTracks') },
        { icon: 'check', text: t('pricing.glowB3'), hint: t('pricing.hintCapcut') }
      ],
      price: '7 990₽'
    },
    {
      // Impulse: Star 10 (Figma 1100×1070 @top -237) — скейл ок, не трогаем (правка); цифра ∞
      type: 'IMPULSE', number: '∞', numberImg: 'pr-num-impulse.png', shape: { asset: 'pr-shape-impulse.svg', left: 0, top: -237, w: 1100, h: 1071 }, badge: 'Impulse', kind: 'product', kindAsset: 'pr-kind-impulse.svg',
      logo: 'pr-logo-impulse.svg', logoH: 18, titleW: 225,
      title: t('pricing.impulseTitle'),
      bullets: [
        { icon: 'scissors', text: t('pricing.impulseB1') },
        { icon: 'note', text: t('pricing.impulseB2'), hint: t('pricing.hintTracks') },
        { icon: 'check', text: t('pricing.impulseB3'), hint: t('pricing.hintManager') }
      ],
      price: '29 990₽'
    }
  ];

  return (
    <div className="flex min-h-0 flex-1 flex-col md:h-[calc(100dvh_-_2*var(--space-6))] md:flex-none">
      <div className="card-2 relative flex min-h-0 flex-1 flex-col p-[40px]">
        <div className="flex shrink-0 items-center gap-[20px]">
          <button
            type="button"
            onClick={() => navigate(-1)}
            aria-label={t('common.back')}
            className="flex h-[60px] w-[60px] shrink-0 items-center justify-center rounded-r15 bg-grad-soft-20 transition hover:brightness-125"
          >
            <FigIcon name="pd-arrow-right.svg" w={25} className="rotate-180" />
          </button>
          <h1 className="text-[32px] font-[400] leading-none text-text">{t('pricing.title')}</h1>
        </div>

        <div className="mt-[28px] grid min-h-0 flex-1 grid-cols-[repeat(3,minmax(357px,1fr))] items-start gap-[20px] overflow-x-auto no-scrollbar">
          {plans.map((plan) => (
            <PlanCard
              key={plan.type}
              plan={plan}
              agreed={Boolean(agreed[plan.type])}
              onAgree={(v) => setAgreed((s) => ({ ...s, [plan.type]: v }))}
              recurrentAgreed={Boolean(recurrentAgreed[plan.type])}
              onRecurrentAgree={(v) => setRecurrentAgreed((state) => ({ ...state, [plan.type]: v }))}
              busy={orderMutation.isPending}
              current={currentTier === plan.type}
              onBuy={() => orderMutation.mutate(plan.type)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
