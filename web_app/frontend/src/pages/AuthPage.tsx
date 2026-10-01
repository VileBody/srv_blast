import { FormEvent, useEffect, useId, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { cn } from '../lib/cn';
import { LEGAL_LINKS } from '../lib/legal';
import { LanguageSwitcher } from '../components/layout/LanguageSwitcher';
import { Modal } from '../components/ui/Modal';
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
const reelSrc = (name: string) => `/media/auth/${name}.mp4`;
const reelPoster = (name: string) => `/media/auth/${name}.jpg`;
/* колонки стены: у каждой свой порядок, направление и скорость — стена не выглядит копией */
const WALL: { clips: string[]; mod: string }[] = [
  { clips: ['hero', 'jakson', 'billie'], mod: '' },
  { clips: ['impulse', 'tunnel', 'alter'], mod: 'auth-col--down' },
  { clips: ['tape', 'alter', 'hero'], mod: 'auth-col--slow' }
];

/** Стена живых роликов: справа на десктопе, фоном всего экрана на телефоне. */
function ReelWall() {
  return (
    <div className="auth-wall" aria-hidden="true">
      <div className="auth-wall-tilt">
        {WALL.map((col, ci) => (
          // третья колонка только на широком экране — на телефоне хватает двух
          <div key={ci} className={cn('auth-col', col.mod, ci === 2 && 'max-sm:hidden')}>
            <div className="auth-col-track">
              {[...col.clips, ...col.clips].map((name, i) => (
                <div key={`${name}-${i}`} className="auth-tile">
                  <video src={reelSrc(name)} poster={reelPoster(name)} muted loop playsInline autoPlay preload="metadata" />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Заголовок ведёт себя как сам продукт — lyric-субтитры: слова проявляются по одному в такт,
 * последнее («клип») загорается акцентом. Строки — из перевода (`auth.title1/2`, `auth.titleHot`).
 */
function LyricTitle() {
  const { t } = useTranslation();
  const lines = [t('auth.title1'), t('auth.title2')];
  const hot = t('auth.titleHot');
  let index = 0;
  return (
    <h1 className="auth-title">
      {lines.map((line, li) => (
        <span key={li} className="auth-line">
          {line.split(' ').map((word, wi, all) => {
            const delay = 0.15 + index++ * 0.32;
            const isHot = li === lines.length - 1 && wi === all.length - 1 && word.replace(/[.,!]/g, '').toLowerCase() === hot.toLowerCase();
            return (
              <span key={wi}>
                <span className={cn('auth-word', isHot && 'auth-word--hot')} style={{ animationDelay: `${delay}s` }}>{word}</span>
                {wi < all.length - 1 ? ' ' : ''}
              </span>
            );
          })}
        </span>
      ))}
    </h1>
  );
}

/** Компактное поле регистрации: подпись над полем, ошибка под ним (имя и фамилия — в один ряд). */
function AuthField({ label, value, onChange, error }: { label: string; value: string; onChange: (value: string) => void; error?: string | false }) {
  const id = useId();
  return (
    <label htmlFor={id} className="flex min-w-0 flex-1 flex-col gap-[8px]">
      <span className="auth-small">{label}</span>
      <input
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={error ? true : undefined}
        autoComplete={label}
        className="auth-input auth-control auth-body-text w-full rounded-r15 border border-line bg-field px-[20px] text-text outline-none"
      />
      {error && <span className="auth-small !text-error">{error}</span>}
    </label>
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

/**
 * Кнопка способа входа — та же, что главная кнопка лендинга («Загрузить трек»): заливка
 * accent-strong, подпись по центру, после неё шеврон. Второй способ (Google) —
 * той же формы на поверхности field.
 */
function ProviderButton({ kind, label, primary, disabled, busy, onClick, href }: {
  kind: 'telegram' | 'google';
  label: string;
  primary?: boolean;
  disabled?: boolean;
  busy?: boolean;
  onClick?: () => void;
  href?: string;
}) {
  const inner = (
    <>
      {kind === 'google' && <span className="auth-cta-icon" aria-hidden="true"><GoogleMark /></span>}
      <span className="auth-cta-label">{label}</span>
      {/* шеврон без стебля — только «галочка» направления; на загрузке вместо него спиннер */}
      {kind === 'telegram' && (
        <span className="auth-cta-icon" aria-hidden="true">
          {busy ? <span className="spinner" /> : (
            <svg viewBox="0 0 24 24" width="20" height="20"><path d="M9 5.5 15.5 12 9 18.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
          )}
        </span>
      )}
    </>
  );
  const shell = cn('auth-cta auth-control auth-body-text', primary ? 'auth-cta--primary' : 'auth-cta--secondary');
  return href ? (
    <a href={href} className={shell}>{inner}</a>
  ) : (
    <button type="button" onClick={onClick} disabled={disabled} aria-busy={busy || undefined} className={shell}>{inner}</button>
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
    <main className="auth-scene">
      <ReelWall />
      <section className="auth-panel">
        <div className="auth-rise flex items-center justify-between">
          <span className="flex items-center gap-[10px]">
            <img src="/assets/figma/logo-star.svg" width="28" height="28" alt="" />
            <span className="text-ui-20 text-text">Blast</span>
          </span>
          <LanguageSwitcher />
        </div>

        <form className="auth-body" onSubmit={onSubmit} noValidate>
          <LyricTitle />
          <p className="auth-rise auth-lead auth-body-text" style={{ animationDelay: '1.4s' }}>
            {mode === 'register' ? t('auth.registerLead') : t('auth.loginLead')}
          </p>

          {/*
           * Способы входа. Пароля и почты нет ни у одного: Telegram даёт личность через
           * chat_id, Google — через подтверждённую почту. ФИО спрашиваем только на регистрации
           * и только ради телеграм-пути (Google отдаёт имя сам) — поэтому поля стоят над кнопкой.
           */}
          <div className="auth-rise auth-actions" style={{ animationDelay: '1.55s' }}>
            {mode === 'register' && (
              <div className="flex gap-[12px]">
                <AuthField label={t('auth.name')} value={form.name} onChange={(name) => setForm((current) => ({ ...current, name }))} error={submitted && errors.name} />
                <AuthField label={t('auth.surname')} value={form.surname} onChange={(surname) => setForm((current) => ({ ...current, surname }))} error={submitted && errors.surname} />
              </div>
            )}
            <ProviderButton
              kind="telegram"
              label={mode === 'register' ? t('auth.tgCtaRegister') : t('auth.tgCtaLogin')}
              primary
              disabled={busy}
              busy={busy}
              onClick={() => onSubmit()}
            />
            {providersQuery.data?.google && (
              <ProviderButton kind="google" label={mode === 'register' ? t('auth.googleCtaRegister') : t('auth.googleCtaLogin')} href={api.googleAuthUrl()} />
            )}

          </div>
        </form>

        {/*
         * Согласие с документами — обязательно на экране входа: акцепт оферты по её разделу 3
         * происходит в момент регистрации, а Google и TikTok при ревью проверяют ссылки здесь.
         * Названия в винительном падеже (auth.legal*).
         */}
        <div className="auth-rise auth-foot" style={{ animationDelay: '1.7s' }}>
          <p className="auth-small">
            {t('auth.legalPrefix')}{' '}
            <a className="auth-link whitespace-nowrap" href={LEGAL_LINKS.offer} target="_blank" rel="noreferrer">{t('auth.legalOffer')}</a>{' '}
            {t('auth.legalAnd')}{' '}
            <a className="auth-link whitespace-nowrap" href={LEGAL_LINKS.policy} target="_blank" rel="noreferrer">{t('auth.legalPolicy')}</a>
          </p>
          <p className="auth-small">
            {mode === 'register' ? t('auth.haveAccount') : t('auth.noAccount')}{' '}
            <Link className="auth-link" to={mode === 'register' ? '/login' : '/register'}>
              {mode === 'register' ? t('auth.loginCta') : t('auth.registerCta')}
            </Link>
          </p>
        </div>
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
