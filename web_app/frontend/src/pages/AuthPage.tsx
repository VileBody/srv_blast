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

type Mode = 'login' | 'register';

/** Светлый градиент-заливка заголовка (Figma 712:1038), как на W35–W37 */
const gradLight = {
  backgroundImage: 'linear-gradient(183deg, #f6f5fd 8.5%, rgba(246,245,253,.8) 94.6%)',
  WebkitBackgroundClip: 'text',
  backgroundClip: 'text'
} as const;

/** passwordless: и логин, и регистрация выдают token + deep-link в бота; вход завершает верификация */
type VerifyResult = { token: string; deepLink: string; viaTelegram?: boolean };

/*
 * Левый визуал-контейнер (Figma W38, 712:1031): клип-фрейм r15 на подложке #140e24 с
 * фирменной фиолетовой фигурой. Растёт по ширине страницы (flex-1 от базы 732), зазоры
 * до краёв и до формы одинаковые (60). Фигура preserveAspectRatio="none" — задаём И
 * width, И height из viewBox. Фото артиста убрано.
 *
 * `max-w` обязателен: рост был ничем не ограничен, и на широком мониторе визуал
 * растягивался на всю свободную ширину, а форма фиксированных 528 улетала к правому
 * краю — страница переставала читаться как макет. Излишек ширины теперь уходит во
 * ВНЕШНИЕ поля (`justify-center` у main), то есть форма подтягивается к центру.
 */
function AuthVisual() {
  return (
    <aside className="relative hidden basis-[732px] grow overflow-hidden rounded-r15 bg-card-2 lg:block xl:max-w-[880px]">
      <span aria-hidden="true" className="absolute left-0 top-0 h-[980px] w-[980px] rounded-r15 bg-card-2" />
      <img
        aria-hidden="true"
        src="/assets/figma/auth-shape.svg"
        width="1413"
        height="1044"
        className="absolute left-[calc(50%+25.57px)] top-[calc(50%+60.78px)] h-[1044.289px] w-[1413.144px] max-w-none -translate-x-1/2 -translate-y-1/2"
      />
    </aside>
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
      <span className="flex h-[40px] w-[40px] shrink-0 items-center justify-center rounded-full bg-[rgba(5,1,15,0.06)]">
        {kind === 'telegram' ? <TelegramMark /> : <GoogleMark />}
      </span>
      <span className="min-w-0 text-left">
        <span className="block text-[20px] font-[400] leading-none">{label}</span>
        <span className={cn('mt-[6px] block text-[14px] leading-[18px]', primary ? 'text-[rgba(5,1,15,0.55)]' : 'text-text-60')}>{benefit}</span>
      </span>
    </>
  );
  const shell = cn(
    'flex h-[76px] w-full items-center gap-[16px] rounded-[38px] px-[24px] transition disabled:opacity-60',
    primary
      ? 'text-[#1b1035] hover:brightness-95'
      : 'border border-[rgba(246,245,253,0.22)] text-text-80 hover:border-accent-light hover:text-text'
  );
  const style = primary
    ? { backgroundImage: 'linear-gradient(154deg, #f6f5fd 8.6%, rgba(246,245,253,0.9) 95.4%)' }
    : undefined;

  return href ? (
    <a href={href} className={shell} style={style}>{inner}</a>
  ) : (
    <button type="button" onClick={onClick} disabled={disabled} className={shell} style={style}>{inner}</button>
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
    <main className="flex min-h-dvh items-stretch justify-center gap-[60px] bg-bg p-[60px] max-lg:p-space-5">
      <AuthVisual />
      <section className="flex min-w-0 flex-1 items-center justify-center lg:flex-none lg:basis-[528px]">
        <form className="w-full max-w-[528px]" onSubmit={onSubmit} noValidate>
          {/* Язык переключается ДО входа: раньше переключатель жил только в сайдбаре, и
              англоязычный человек упирался в русский экран без единого способа это изменить. */}
          <div className="mb-[24px] flex justify-end">
            <LanguageSwitcher />
          </div>
          {/* h=77 по Figma (712:1038): базовый line-height 1.5 дал бы 96 и увёл бы колонку вверх */}
          <h1 className="text-[64px] font-[600] leading-[77px] text-transparent" style={gradLight}>
            {mode === 'register' ? t('auth.registerTitle') : t('auth.loginTitle')}
          </h1>
          <p className="mt-[25px] max-w-[380px] text-[24px] font-[350] leading-[29px] text-text-80">
            {mode === 'register' ? t('auth.registerSubtitle') : t('auth.loginSubtitle')}
          </p>

          {/*
           * Развязка способов входа. Пароля и почты нет ни у одного: Telegram даёт личность
           * через chat_id, Google — через подтверждённую почту.
           *
           * ФИО спрашиваем ТОЛЬКО на регистрации и ТОЛЬКО ради телеграм-пути: Google отдаёт
           * имя и фамилию сам. Поэтому поля стоят под своей кнопкой и подписаны — иначе
           * человек, выбравший Google, пытался бы заполнить ненужную форму.
           */}
          <p className="mt-[48px] text-[16px] leading-none text-text-40">{t('auth.pickProvider')}</p>

          <div className="mt-[20px] flex flex-col gap-[16px]">
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
                  <span className="h-px flex-1 bg-[rgba(246,245,253,0.12)]" />
                  <span className="text-[14px] leading-none text-text-40">{t('auth.orDivider')}</span>
                  <span className="h-px flex-1 bg-[rgba(246,245,253,0.12)]" />
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
          <p className="mx-auto mt-[24px] max-w-[400px] text-balance text-center text-[14px] leading-[19px] text-text-40">
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

          <p className="mt-[25px] text-center text-[16px] text-text-60">
            {mode === 'register' ? t('auth.haveAccount') : t('auth.noAccount')}{' '}
            <Link className="text-accent-light underline underline-offset-2" to={mode === 'register' ? '/login' : '/register'}>
              {mode === 'register' ? t('auth.loginCta') : t('auth.registerCta')}
            </Link>
          </p>
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
