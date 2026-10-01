import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { cn } from '../lib/cn';
import { LEGAL_LINKS } from '../lib/legal';
import { LanguageSwitcher } from '../components/layout/LanguageSwitcher';
import { Modal } from '../components/ui/Modal';
import { NotchedInput } from '../components/ui/NotchedInput';
import { FigIcon } from '../components/ui/FigIcon';
import { useToast } from '../contexts/ToastContext';
import './AuthPage.css';

type Mode = 'login' | 'register';

/** passwordless: и логин, и регистрация выдают token + deep-link в бота; вход завершает верификация */
type VerifyResult = { token: string; deepLink: string; viaTelegram?: boolean };

/*
 * Живые примеры — настоящие ролики сервиса (те же, что на лендинге), лежат в сборке сайта:
 * экран входа видят без авторизации, и каталог превью визарда ему недоступен.
 */
const REELS = ['hero', 'tape', 'jakson', 'tunnel', 'billie'] as const;
const reelSrc = (name: string) => `/media/auth/${name}.mp4`;
const reelPoster = (name: string) => `/media/auth/${name}.jpg`;

function Reel({ name }: { name: string }) {
  return <video className="auth-media" src={reelSrc(name)} poster={reelPoster(name)} muted loop playsInline autoPlay preload="metadata" />;
}

/*
 * Левая витрина (десктоп): три живых ролика веером и подпись о том, что делает сервис. Раньше здесь была
 * плоская фиолетовая фигура на пол-экрана — палитрой и характером из другого продукта.
 */
function AuthVisual() {
  const { t } = useTranslation();
  return (
    <aside className="auth-visual auth-rise hidden basis-[732px] grow lg:flex xl:max-w-[880px]" aria-hidden="true">
      <div className="flex items-center gap-[10px]">
        <img src="/assets/figma/logo-star.svg" width="28" height="28" alt="" />
        <span className="text-ui-20 text-text">Blast</span>
      </div>
      <div className="auth-reel">
        <div className="auth-frame auth-frame--l"><Reel name="tape" /></div>
        <div className="auth-frame auth-frame--c"><Reel name="hero" /></div>
        <div className="auth-frame auth-frame--r"><Reel name="tunnel" /></div>
      </div>
      <div className="auth-caption">
        <p className="text-ui-32 text-text [text-wrap:balance]">{t('auth.showTitle')}</p>
        <p className="mt-[12px] text-ui-16 text-text-60 [text-wrap:pretty]">{t('auth.showText')}</p>
      </div>
    </aside>
  );
}

/** Телефон: витрины слева нет — живые примеры бегущей лентой над формой. */
function MobileReel() {
  const loop = [...REELS, ...REELS];
  return (
    <div className="auth-strip auth-rise mb-[28px] lg:hidden" aria-hidden="true">
      <div className="auth-strip-track">
        {loop.map((name, index) => <div key={`${name}-${index}`} className="auth-strip-item"><Reel name={name} /></div>)}
      </div>
    </div>
  );
}

/** Что даёт сервис — три пункта под кнопками входа, вместо одной подписи «войди в аккаунт». */
function AuthPoints({ className }: { className?: string }) {
  const { t } = useTranslation();
  const points = [t('auth.point1'), t('auth.point2'), t('auth.point3')];
  return (
    <ul className={cn('mt-[28px] flex flex-col gap-[12px]', className)}>
      {points.map((text) => (
        <li key={text} className="flex items-start gap-[12px] text-ui-16 text-text-80">
          {/* ячейка высотой в строку текста — галочка по центру первой строки без подгонок */}
          <span className="flex h-ctl-xs shrink-0 items-center" aria-hidden="true">
            <span className="flex h-[20px] w-[20px] items-center justify-center rounded-full bg-accent-soft text-accent-light">
              <svg viewBox="0 0 16 16" width="12" height="12"><path d="M4 8.4 6.6 11 12 5.4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </span>
          </span>
          <span>{text}</span>
        </li>
      ))}
    </ul>
  );
}

/** TTL одноразового токена на бэке (auth_store.TOKEN_TTL_SEC) — модалка не должна сдаваться раньше. */
const TOKEN_TTL_MS = 10 * 60 * 1000;

function TgVerifyModal({ verify, onDone, onClose, onRetry, retrying }: {
  verify: VerifyResult | null;
  onDone: () => void;
  onClose: () => void;
  onRetry: () => void;
  retrying: boolean;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [checking, setChecking] = useState(false);
  const [opened, setOpened] = useState(false);
  const [expired, setExpired] = useState(false);
  // Вошёл по «Войти», а аккаунта с этим Telegram нет: раньше он молча создавался, и человек
  // возвращался из бота на обязательный экран «представься» — это читалось как сбой.
  const [noAccount, setNoAccount] = useState(false);
  const { push } = useToast();
  const botName = verify ? (verify.deepLink.split('t.me/')[1]?.split('?')[0] ?? 'bot') : '';

  // авто-поллинг: как только бот получит /start — сами заводим в приложение (клик «проверить» опционален)
  useEffect(() => {
    if (!verify) return;
    setOpened(false);
    setExpired(false);
    setNoAccount(false);
    const startedAt = Date.now();
    const timer = window.setInterval(async () => {
      if (Date.now() - startedAt > TOKEN_TTL_MS) {
        setExpired(true);
        window.clearInterval(timer);
        return;
      }
      try {
        const status = await api.tgVerify(verify.token);
        if (status.noAccount) {
          window.clearInterval(timer);
          setNoAccount(true);
          return;
        }
        if (status.verified) {
          window.clearInterval(timer);
          push({ variant: 'success', title: t('auth.tgOk'), text: t('auth.tgOkText') });
          onDone();
        }
      } catch {
        // polling не должен падать из-за одного сбоя сети.
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [onDone, push, verify, t]);

  const checkNow = async () => {
    if (!verify) return;
    setChecking(true);
    try {
      const status = await api.tgVerify(verify.token);
      if (status.noAccount) {
        setNoAccount(true);
      } else if (status.verified) {
        push({ variant: 'success', title: t('auth.tgOk'), text: t('auth.tgOkText') });
        onDone();
      } else {
        push({ variant: 'info', title: t('auth.tgNotYet') });
      }
    } finally {
      setChecking(false);
    }
  };

  const openBot = () => {
    if (verify) window.open(verify.deepLink, '_blank', 'noopener');
    setOpened(true);
  };

  return (
    <Modal open={Boolean(verify)} onClose={onClose}>
      <div className="flex flex-col items-center gap-[24px] text-center">
        <span className="flex h-[64px] w-[64px] items-center justify-center rounded-full bg-grad-soft-20">
          <FigIcon name="icon-bolt.svg" h={30} />
        </span>
        <div>
          <h3 className="text-[24px] font-[600] leading-[29px] text-text">{noAccount ? t('auth.noAccountHeading') : t('auth.tgHeading')}</h3>
          <p className="mx-auto mt-[12px] max-w-[420px] text-[16px] leading-[21px] text-text-60">
            {noAccount ? t('auth.noAccountText') : t('auth.tgText')}
          </p>
        </div>
        {/* одна кнопка: сперва открыть бота, потом ей же проверить (авто-поллинг идёт параллельно).
            По истечении TTL та же кнопка перезапрашивает ссылку — иначе из тупика был только выход.
            Аккаунта нет — та же кнопка уводит на регистрацию. */}
        <button
          type="button"
          onClick={noAccount ? () => navigate('/register') : expired ? onRetry : opened ? checkNow : openBot}
          disabled={checking || retrying}
          className="flex h-[60px] w-full items-center justify-center rounded-r15 bg-grad-main text-[18px] font-[400] leading-none text-text transition hover:brightness-110 disabled:opacity-60"
        >
          {checking || retrying
            ? t('common.loading')
            : noAccount
              ? t('auth.registerCta')
              : expired
                ? t('auth.tgRetry')
                : opened
                  ? t('auth.tgCheck')
                  : t('auth.tgOpenBot', { bot: '@' + botName })}
        </button>
        {expired && !noAccount && <p className="text-[14px] leading-[18px] text-warning">{t('auth.tgExpired')}</p>}
      </div>
    </Modal>
  );
}

/** Значок Telegram — тот же вес, что у буквы G, чтобы кнопки читались парой. */
function TelegramMark() {
  return (
    <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true">
      <circle cx="10" cy="10" r="10" fill="#29A9EB" />
      <path d="M4.6 9.9l9-3.5c.5-.2.9.1.7.7l-1.5 7.2c-.1.5-.5.6-.9.4l-2.4-1.8-1.2 1.1c-.1.1-.3.2-.5.2l.2-2.5 4.4-4c.2-.2 0-.3-.3-.1l-5.4 3.4-2.3-.7c-.5-.2-.5-.5.2-.8Z" fill="#fff" />
    </svg>
  );
}

/**
 * Кнопка способа входа. Обе одного размера и с подписью-выгодой — это развязка, а не
 * «главная кнопка и запасная»: раньше Telegram был крупной белой пилюлей, а Google —
 * тонкой обводкой снизу, и выбор читался как навязанный.
 */
function ProviderButton({ kind, label, benefit, primary, disabled, onClick, href }: {
  kind: 'telegram' | 'google';
  label: string;
  benefit: string;
  primary?: boolean;
  disabled?: boolean;
  onClick?: () => void;
  href?: string;
}) {
  const inner = (
    <>
      <span className={cn('flex h-[40px] w-[40px] shrink-0 items-center justify-center rounded-r10', primary ? 'bg-[rgba(246,245,253,0.14)]' : 'bg-panel')}>
        {kind === 'telegram' ? <TelegramMark /> : <GoogleMark />}
      </span>
      <span className="min-w-0 text-left">
        <span className="block text-ui-20 text-text">{label}</span>
        <span className={cn('block text-ui-14', primary ? 'text-text-80' : 'text-text-60')}>{benefit}</span>
      </span>
      <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" className="ml-auto shrink-0 opacity-70"><path d="M7.5 4.5 13 10l-5.5 5.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
    </>
  );
  // Основной способ — заливка акцентом, как главные кнопки визарда; второй — поверхность field.
  const shell = cn(
    'auth-provider flex min-h-[72px] w-full items-center gap-[16px] rounded-r15 px-[16px] py-[12px] text-text disabled:cursor-wait disabled:opacity-60',
    primary
      ? 'bg-accent-strong hover:brightness-110'
      : 'border border-line bg-field hover:bg-field-hover'
  );

  return href ? (
    <a href={href} className={shell}>{inner}</a>
  ) : (
    <button type="button" onClick={onClick} disabled={disabled} className={shell}>{inner}</button>
  );
}

/** Логотип Google — фирменная буква G. Кнопку без неё Google в гайдлайнах не принимает. */
function GoogleMark() {
  return (
    <svg viewBox="0 0 18 18" width="20" height="20" aria-hidden="true">
      <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62Z" />
      <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.81.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18Z" />
      <path fill="#FBBC05" d="M3.97 10.72a5.41 5.41 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33Z" />
      <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.9 11.43 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58Z" />
    </svg>
  );
}

export function AuthPage({ mode }: { mode: Mode }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { push } = useToast();
  const [verify, setVerify] = useState<VerifyResult | null>(null);
  const [form, setForm] = useState({ name: '', surname: '' });
  const [submitted, setSubmitted] = useState(false);
  const [params, setParams] = useSearchParams();

  // Кнопку показываем только если ключи Google реально заданы — мёртвая кнопка хуже, чем её отсутствие
  const providersQuery = useQuery({ queryKey: ['auth-providers'], queryFn: api.authProviders, staleTime: 5 * 60_000 });

  /*
   * Возврат с Google: бэк редиректит сюда с ?auth=<исход>. Показываем причину и чистим
   * query, иначе тост всплывал бы на каждом рендере.
   */
  const authResult = params.get('auth');
  const shownAuthResult = useRef<string | null>(null);
  useEffect(() => {
    if (!authResult || shownAuthResult.current === authResult) return;
    shownAuthResult.current = authResult;
    const title = authResult === 'denied' ? t('auth.googleDenied')
      : authResult === 'state' ? t('auth.googleState')
        : authResult === 'google_unavailable' ? t('auth.googleUnavailable')
          : t('auth.googleError');
    push({ variant: authResult === 'denied' ? 'info' : 'error', title });
    params.delete('auth');
    setParams(params, { replace: true });
  }, [authResult, params, push, setParams, t]);

  // На регистрации ФИО обязательны: без них в ЛК будет пустой профиль
  const errors = useMemo(() => {
    const next: Record<string, string> = {};
    if (mode === 'register' && !form.name.trim()) next.name = t('auth.errName');
    if (mode === 'register' && !form.surname.trim()) next.surname = t('auth.errSurname');
    return next;
  }, [form, mode, t]);

  const tgStartMutation = useMutation({
    mutationFn: api.tgStart,
    onSuccess: (data) => setVerify({ token: data.token, deepLink: data.deepLink, viaTelegram: true }),
    onError: (error) => push({ variant: 'error', title: error instanceof Error ? error.message : t('auth.toastLoginFail') })
  });

  // Вызывается и сабмитом формы (Enter в поле), и кликом по кнопке Telegram
  const onSubmit = (event?: FormEvent) => {
    event?.preventDefault();
    setSubmitted(true);
    if (Object.keys(errors).length) return;
    // и вход, и регистрация — один жест в бота; на регистрации с ним уезжает ФИО
    tgStartMutation.mutate(mode === 'register'
      ? { mode: 'register', name: form.name.trim(), surname: form.surname.trim() }
      : { mode: 'login' });
  };

  const onVerified = async () => {
    await queryClient.invalidateQueries({ queryKey: ['me'] });
    navigate('/app');
  };

  const busy = tgStartMutation.isPending;

  return (
    // Figma W38: паддинг 60, слева визуал-контейнер (растёт по ширине), зазор 60, колонка формы 528.
    /*
      `justify-center`: излишек ширины сверх (визуал 880 + 60 + форма 528) уходит в равные
      внешние поля, а не в бесконечный рост левой колонки. На 1440 картина ровно как в
      макете (визуал 732, поля и зазор по 60), на 1920+ форма подтягивается к центру.
    */
    <main className="flex min-h-dvh items-stretch justify-center gap-[40px] bg-bg p-[40px] max-lg:p-space-5">
      <AuthVisual />
      <section className="flex min-w-0 flex-1 items-center justify-center lg:flex-none lg:basis-[528px]">
        <form className="w-full max-w-[528px]" onSubmit={onSubmit} noValidate>
          {/* Язык переключается ДО входа: раньше переключатель жил только в сайдбаре, и
              англоязычный человек упирался в русский экран без единого способа это изменить. */}
          <div className="auth-rise mb-[40px] flex items-center justify-between max-lg:mb-[24px]">
            {/* на телефоне витрины нет — бренд виден здесь */}
            <span className="flex items-center gap-[10px] lg:invisible">
              <img src="/assets/figma/logo-star.svg" width="28" height="28" alt="" />
              <span className="text-ui-20 text-text">Blast</span>
            </span>
            <LanguageSwitcher />
          </div>
          <MobileReel />
          <span className="auth-rise inline-flex h-ctl-xs items-center gap-[8px] rounded-full border border-accent-line bg-accent-soft px-[10px] text-ui-12 text-text-80">
            <span className="h-[6px] w-[6px] rounded-full bg-accent-light" aria-hidden="true" />
            {t('auth.eyebrow')}
          </span>
          {/* заголовок — сплошной цвет и плотный трекинг, без градиентной заливки текста */}
          {/* ui-allow: дисплейный заголовок экрана входа крупнее шкалы контента */}
          <h1 className="auth-rise auth-rise--2 mt-[16px] text-[52px] font-[400] leading-[56px] tracking-[-0.025em] text-text [text-wrap:balance] max-md:text-[36px] max-md:leading-[40px]">
            {mode === 'register' ? t('auth.registerHeadline') : t('auth.loginHeadline')}
          </h1>
          <p className="auth-rise auth-rise--2 mt-[16px] max-w-[460px] text-ui-20 text-text-60 [text-wrap:pretty] max-md:text-ui-16">
            {mode === 'register' ? t('auth.registerLead') : t('auth.loginLead')}
          </p>

          {/*
           * Развязка способов входа. Пароля и почты нет ни у одного: Telegram даёт личность
           * через chat_id, Google — через подтверждённую почту.
           *
           * ФИО спрашиваем ТОЛЬКО на регистрации и ТОЛЬКО ради телеграм-пути: Google отдаёт
           * имя и фамилию сам. Поэтому поля стоят под своей кнопкой и подписаны — иначе
           * человек, выбравший Google, пытался бы заполнить ненужную форму.
           */}
          <div className="auth-rise auth-rise--3">
          <p className="mt-[32px] text-ui-14 text-text-40">{t('auth.pickProvider')}</p>

          <div className="mt-[12px] flex flex-col gap-[12px]">
            <ProviderButton
              kind="telegram"
              label={t('auth.telegramCta')}
              benefit={busy ? t('common.loading') : t('auth.tgBenefit')}
              primary
              disabled={busy}
              onClick={() => onSubmit()}
            />

            {mode === 'register' && (
              <div className="mt-[12px] flex flex-col gap-[43px]">
                <NotchedInput
                  label={t('auth.name')}
                  value={form.name}
                  onChange={(e) => setForm((current) => ({ ...current, name: e.target.value }))}
                  error={submitted && errors.name}
                />
                <NotchedInput
                  label={t('auth.surname')}
                  value={form.surname}
                  onChange={(e) => setForm((current) => ({ ...current, surname: e.target.value }))}
                  error={submitted && errors.surname}
                />
                <p className="-mt-[26px] text-[14px] leading-[18px] text-text-40">{t('auth.namesForTelegram')}</p>
              </div>
            )}

            {providersQuery.data?.google && (
              <>
                <div className="flex items-center gap-[16px]" aria-hidden="true">
                  <span className="h-px flex-1 bg-line" />
                  <span className="text-ui-12 text-text-40">{t('auth.orDivider')}</span>
                  <span className="h-px flex-1 bg-line" />
                </div>
                <ProviderButton
                  kind="google"
                  label={t('auth.googleCta')}
                  benefit={t('auth.googleBenefit')}
                  href={api.googleAuthUrl()}
                />
              </>
            )}
          </div>

          {/*
           * Согласие с документами. Обязательно именно здесь: акцепт оферты по её же
           * разделу 3 происходит в момент регистрации, а Google и TikTok при ревью
           * проверяют, что ссылки на политику и оферту доступны с экрана входа.
           */}
          {/* Ширина ограничена и строки балансируются: иначе длинная «Политика
              конфиденциальности» уезжала за колонку формы и ломала строку пополам. */}
          {/* на телефоне в регистрации пункты прячем: там главное — поля и кнопка */}
          <AuthPoints className={mode === 'register' ? 'max-lg:hidden' : undefined} />

          <p className="mt-[28px] max-w-[440px] text-balance text-ui-12 text-text-40">
            {/* Названия документов здесь в винительном падеже (auth.legal*), а не заголовками
                из legal.*: строка читалась «принимаешь Оферта и Политика конфиденциальности». */}
            {t('auth.legalPrefix')}{' '}
            <a className="whitespace-nowrap text-text-60 underline underline-offset-2 transition hover:text-text" href={LEGAL_LINKS.offer} target="_blank" rel="noreferrer">
              {t('auth.legalOffer')}
            </a>{' '}
            {t('auth.legalAnd')}{' '}
            <a className="text-text-60 underline underline-offset-2 transition hover:text-text" href={LEGAL_LINKS.policy} target="_blank" rel="noreferrer">
              {t('auth.legalPolicy')}
            </a>
          </p>

          <p className="mt-[32px] border-t border-line pt-[20px] text-ui-16 text-text-60">
            {mode === 'register' ? t('auth.haveAccount') : t('auth.noAccount')}{' '}
            <Link className="text-accent-light underline-offset-4 transition hover:underline" to={mode === 'register' ? '/login' : '/register'}>
              {mode === 'register' ? t('auth.loginCta') : t('auth.registerCta')}
            </Link>
          </p>
          </div>
        </form>
      </section>
      <TgVerifyModal
        verify={verify}
        onDone={onVerified}
        onClose={() => setVerify(null)}
        // перезапрос ссылки идёт тем же путём, каким модалка была открыта
        onRetry={() => tgStartMutation.mutate(mode === 'register'
      ? { mode: 'register', name: form.name.trim(), surname: form.surname.trim() }
      : { mode: 'login' })}
        retrying={busy}
      />
    </main>
  );
}
