import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Button, ButtonLink, buttonClass } from '../components/ui/kit';
import { useToast } from '../contexts/ToastContext';
import { api, ApiError, type HandoffTelegram } from '../lib/api';
import { useWizardStore } from '../stores/wizardStore';
import { guardDraft } from '../stores/draftGuard';
import { clearUserState, rememberSessionUser, sessionUser } from '../stores/session';
import { DraftReplaceDialog } from '../components/layout/DraftReplaceDialog';
import { apiErrorCode } from '../components/funnel/useFunnel';

type Failure =
  | 'expired'
  | 'used'
  | 'error'
  | 'otherAccount'
  | 'linkAccount'
  | 'linkPending'
  | 'linkRejected'
  | 'linkExpired'
  | 'linkUndeliverable';
/** Ответ человека на вопрос про аккаунт в браузере (см. api.botHandoff) */
type Answer = { force?: boolean; link?: boolean };
type ErrorDetail = { email?: string; name?: string; telegram?: HandoffTelegram; botUrl?: string };

// Как часто спрашиваем, подтвердили ли привязку в Telegram (GET, лимит POST не тратит)
const LINK_POLL_MS = 2500;

function errorDetail(error: unknown): ErrorDetail {
  if (!(error instanceof ApiError)) return {};
  return (error.detail as { detail?: ErrorDetail } | null)?.detail ?? {};
}

/** «@lena_beats (Лена)» — какой Telegram войдёт или привяжется: ссылку могли подсунуть */
function telegramLabel(telegram: HandoffTelegram | undefined): string {
  const username = telegram?.username ?? '';
  const name = telegram?.name ?? '';
  if (username && name) return `${username} (${name})`;
  return username || name;
}

/** секунды → «мм:сс:сс» (формат полей тайминга визарда, см. timingToSeconds) */
function secondsToTiming(value: number): string {
  const cs = Math.round(Math.max(0, value) * 100);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(Math.floor(cs / 6000))}:${pad(Math.floor((cs % 6000) / 100))}:${pad(cs % 100)}`;
}

/**
 * Ссылка «на сайт» из публичного бота: `/go#t=<token>`.
 *
 * Бэк сам логинит человека по токену (его выпустил бот, chat_id уже известен) и,
 * если в ссылке трек, заводит проект с этим треком. Здесь остаётся положить трек
 * в черновик визарда и открыть его — дальше ведут обычные подсказки визарда.
 *
 * Ссылка одноразовая: Telegram сохраняет кнопки в пересланных сообщениях. Открытая
 * ссылка ведёт на экран «уже использована» с кнопкой «новая ссылка в боте»; привязка
 * Telegram к открытому аккаунту завершается только после «Привязать» в самом Telegram.
 */
export function HandoffPage() {
  const { t } = useTranslation();
  // Токен приходит во фрагменте (#t=…), чтобы не попадать в логи сервера; старый вид
  // /go/<token> тоже понимаем.
  const { token: pathToken = '' } = useParams();
  const token = new URLSearchParams(window.location.hash.replace(/^#/, '')).get('t') || pathToken;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { push } = useToast();
  // Голый /go без токена — сразу «ссылка устарела», без запроса: ходить не с чем.
  const [failure, setFailure] = useState<Failure | null>(() => (token ? null : 'expired'));
  // «Попробовать ещё раз» — повтор запроса, а не перезагрузка: один раз, дальше только вход
  const [retries, setRetries] = useState(0);
  // В браузере другой аккаунт или аккаунт без Telegram: входим по ссылке только после
  // ответа человека (force — сменить / войти отдельно, link — привязать Telegram).
  const [answer, setAnswer] = useState<Answer>({});
  // Повтор с тем же ответом (подтвердили в Telegram, «отправить ещё раз»)
  const [round, setRound] = useState(0);
  // Почта открытого аккаунта без Telegram — в вопросе «Привязать Telegram к …?»
  const [linkLabel, setLinkLabel] = useState('');
  // Какой Telegram войдёт/привяжется — в каждом вопросе
  const [telegram, setTelegram] = useState('');
  // «Получить новую ссылку в боте» (t.me/<бот>?start=site_login), бэк присылает с 410
  const [botUrl, setBotUrl] = useState('');
  // StrictMode в деве зовёт эффект дважды; сама ручка идемпотентна, но лишний
  // запрос съедал бы лимит /api/auth/*. Помним, с каким ответом уже ходили.
  const started = useRef<string | null>(null);

  useEffect(() => {
    if (!token) return;
    const attempt = JSON.stringify({ answer, retries, round });
    if (started.current === attempt) return;
    started.current = attempt;
    // чей черновик был в браузере до входа по ссылке (до входа /api/me спросить нельзя)
    const previousUser = sessionUser();
    api.botHandoff(token, answer)
      .then(async (res) => {
        if (res.pending) {
          // «Привязать»: бот спросил в Telegram, ждём ответа (опрос — эффект ниже)
          setTelegram(telegramLabel(res.telegram));
          setFailure('linkPending');
          return;
        }
        /*
         * Ссылка могла войти другим аккаунтом (force, привязка, протухшая сессия). Тогда черновик
         * визарда, окна воронки и кэш запросов принадлежат прежнему — стираем до того, как
         * класть трек из бота, иначе новый аккаунт увидел бы чужой трек и чужие лимиты.
         */
        const me = await api.me().catch(() => null);
        const switched = previousUser !== null && previousUser !== (me?.user.id ?? null);
        if (switched) {
          clearUserState();
          queryClient.clear();
        } else {
          queryClient.removeQueries({ queryKey: ['me'] });
        }
        if (me) {
          rememberSessionUser(me.user.id);
          queryClient.setQueryData(['me'], me);
        }

        const apply = async () => {
          let imported = false;
          let importFailed = false;
          // Повтор ссылки (тот же проект уже был): стор не трогаем — WizardPage сам
          // восстановит серверную сессию проекта, правки на сайте не затираются.
          if (res.projectId && !res.repeat) {
            const store = useWizardStore.getState();
            if (res.wizardImport) {
              // «Докрутить на сайте»: весь монтаж роликов бота сразу на столе
              try {
                const { applyWizardImport } = await import('../stores/wizardImport');
                applyWizardImport(res.projectId, res.track, res.wizardImport);
                imported = true;
              } catch (error) {
                // Черновик не лёг (битый импорт, не загрузился модуль): открываем проект хотя бы
                // с треком и прямо говорим, что настройка не переехала — пустой визард молча
                // выглядел бы как «потерялось всё».
                importFailed = true;
                console.error('handoff: wizard import failed', error);
                const fresh = useWizardStore.getState();
                fresh.reset(res.projectId);
                if (res.track) fresh.setTrack(res.track);
              }
            } else {
              if (store.projectId !== res.projectId) store.reset(res.projectId);
              if (res.track) store.setTrack(res.track);
              // Монтаж не переехал (или ссылка старого вида): отрезок и текст ролика — в черновик
              if (res.draft && res.draft.clipEnd > res.draft.clipStart) {
                store.setField('timingMode', 'manual');
                store.setField('timingFrom', secondsToTiming(res.draft.clipStart));
                store.setField('timingTo', secondsToTiming(res.draft.clipEnd));
                if (res.draft.lyrics) store.setField('lyrics', res.draft.lyrics);
              }
            }
          }
          if (res.trackError === 'tracks_limit') {
            push({ variant: 'error', title: t('handoff.tracksLimit'), text: t('handoff.tracksLimitText') });
          } else if (importFailed) {
            push({ variant: 'error', title: t('handoff.remixFailed'), text: t('handoff.importFailedText') });
          } else if (!res.repeat && res.wizardImportError) {
            push({ variant: 'error', title: t('handoff.remixFailed'), text: res.wizardImportError });
          } else if (imported) {
            const notes = res.wizardImport?.notes ?? [];
            push({ variant: notes.length ? 'info' : 'success', title: t('handoff.remixReady'), text: notes.length ? notes.join(' ') : t('handoff.remixReadyText') });
          } else if (res.track && !res.repeat) {
            push({ variant: 'success', title: t('handoff.trackReady'), text: t('handoff.trackReadyText') });
          }
          navigate(res.redirectTo, { replace: true });
        };

        // Свой недоделанный черновик другого проекта молча не подменяем: спрашиваем. Пустой
        // черновик или черновик прежнего аккаунта (уже стёрт выше) — кладём сразу.
        if (res.projectId && !res.repeat) {
          guardDraft({ projectId: res.projectId }, () => void apply(), () => {
            // Оставили своё: трек из бота уже в «Проектах», визард — с текущей настройкой
            const current = useWizardStore.getState().projectId;
            push({ variant: 'info', title: t('handoff.keptDraft'), text: t('handoff.keptDraftText') });
            navigate(current ? `/app/generate?project=${encodeURIComponent(current)}` : '/app/generate', { replace: true });
          });
          return;
        }
        await apply();
      })
      .catch((error: unknown) => {
        const code = apiErrorCode(error);
        const detail = errorDetail(error);
        setTelegram(telegramLabel(detail.telegram));
        setBotUrl(detail.botUrl ?? '');
        if (code === 'handoff_other_account') setFailure('otherAccount');
        else if (code === 'handoff_link_account') {
          setLinkLabel(detail.email || detail.name || '');
          setFailure('linkAccount');
        } else if (code === 'handoff_link_rejected') setFailure('linkRejected');
        else if (code === 'handoff_link_undeliverable') setFailure('linkUndeliverable');
        else if (code === 'handoff_used') setFailure('used');
        else if (error instanceof ApiError && [400, 404, 410, 422].includes(error.status)) {
          // протухшая или битая ссылка: повтор ответил бы тем же
          setFailure('expired');
        } else setFailure('error');
      });
  }, [token, answer, retries, round, navigate, queryClient, push, t]);

  // Ждём «Привязать» в Telegram. Подтвердили — тот же запрос ещё раз: бэк привяжет и войдёт.
  useEffect(() => {
    if (failure !== 'linkPending') return;
    let stopped = false;
    const timer = window.setInterval(() => {
      api.handoffLinkStatus()
        .then((res) => {
          if (stopped) return;
          if (res.status === 'confirmed' || res.status === 'completed') {
            setFailure(null);
            setRound((n) => n + 1);
          } else if (res.status === 'rejected') setFailure('linkRejected');
          else if (res.status === 'expired' || res.status === 'none') setFailure('linkExpired');
        })
        // сбой одного опроса не ответ человека: следующий тик спросит снова
        .catch((error: unknown) => console.warn('handoff: link status poll failed', error));
    }, LINK_POLL_MS);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [failure]);

  const reply = (next: Answer) => {
    setFailure(null);
    setAnswer(next);
    setRound((n) => n + 1);
  };

  const tg = telegram || t('handoff.thisTelegram');
  const toBot = botUrl ? (
    <ButtonLink variant="primary" size="lg" href={botUrl} target="_blank" rel="noreferrer">
      {t('handoff.newLinkInBot')}
    </ButtonLink>
  ) : null;

  let content: ReactNode;
  if (failure === null) {
    content = (
      <p className="mt-[32px] text-ui-16 font-[350] text-text-60" role="status">
        {t('handoff.loading')}
      </p>
    );
  } else if (failure === 'otherAccount') {
    content = (
      <Screen title={t('handoff.otherAccountAs', { telegram: tg })} text={t('handoff.otherAccountText', { telegram: tg })}>
        <Button variant="primary" size="lg" onClick={() => reply({ force: true })}>
          {t('handoff.otherAccountConfirm')}
        </Button>
        <Link className={buttonClass({ variant: 'secondary', size: 'lg' })} to="/app">
          {t('handoff.otherAccountStay')}
        </Link>
      </Screen>
    );
  } else if (failure === 'linkAccount') {
    content = (
      <Screen
        title={t('handoff.linkAccount', { telegram: tg })}
        text={t('handoff.linkAccountText', { account: linkLabel || t('handoff.thisAccount'), telegram: tg })}
      >
        <Button variant="primary" size="lg" onClick={() => reply({ link: true })}>
          {t('handoff.linkAccountConfirm')}
        </Button>
        <Button variant="secondary" size="lg" onClick={() => reply({ force: true })}>
          {t('handoff.linkAccountSeparate')}
        </Button>
      </Screen>
    );
  } else if (failure === 'linkPending') {
    content = (
      <Screen title={t('handoff.linkPending')} text={t('handoff.linkPendingText', { telegram: tg })}>
        <Link className={buttonClass({ variant: 'secondary', size: 'lg' })} to="/app">
          {t('handoff.linkCancel')}
        </Link>
      </Screen>
    );
  } else if (failure === 'linkRejected') {
    content = (
      <Screen title={t('handoff.linkRejected')} text={t('handoff.linkRejectedText')}>
        <Link className={buttonClass({ variant: 'primary', size: 'lg' })} to="/app">
          {t('handoff.otherAccountStay')}
        </Link>
      </Screen>
    );
  } else if (failure === 'linkExpired') {
    content = (
      <Screen title={t('handoff.linkExpired')} text={t('handoff.linkExpiredText')}>
        <Button variant="primary" size="lg" onClick={() => reply({ link: true })}>
          {t('handoff.linkResend')}
        </Button>
        <Link className={buttonClass({ variant: 'secondary', size: 'lg' })} to="/app">
          {t('handoff.linkCancel')}
        </Link>
      </Screen>
    );
  } else if (failure === 'linkUndeliverable') {
    content = (
      <Screen title={t('handoff.linkUndeliverable')} text={t('handoff.linkUndeliverableText')}>
        {toBot}
        <Button variant={toBot ? 'secondary' : 'primary'} size="lg" onClick={() => reply({ link: true })}>
          {t('handoff.linkResend')}
        </Button>
      </Screen>
    );
  } else if (failure === 'used' || failure === 'expired') {
    content = (
      <Screen
        title={failure === 'used' ? t('handoff.used') : t('handoff.expired')}
        text={failure === 'used' ? t('handoff.usedText') : t('handoff.expiredText')}
      >
        {toBot}
        <Link className={buttonClass({ variant: toBot ? 'secondary' : 'primary', size: 'lg' })} to="/login">
          {t('handoff.toLogin')}
        </Link>
      </Screen>
    );
  } else {
    content = (
      <Screen title={t('handoff.error')} text={t('handoff.errorText')}>
        {retries < 1 && (
          <Button variant="primary" size="lg" onClick={() => { setFailure(null); setRetries((n) => n + 1); }}>
            {t('simple.refresh')}
          </Button>
        )}
        <Link className={buttonClass({ variant: retries < 1 ? 'secondary' : 'primary', size: 'lg' })} to="/login">
          {t('handoff.toLogin')}
        </Link>
      </Screen>
    );
  }

  return (
    <main className="flex min-h-dvh items-center justify-center bg-bg p-[24px] text-center">
      <section className="card-2 flex w-full flex-col items-center justify-center px-[24px] py-[60px]" style={{ maxWidth: 760, minHeight: 420 }}>
        <img src="/assets/figma/logo-star.svg" width="60" height="60" alt="Blast" />
        {content}
      </section>
      {/* страница вне AppShell: вопрос о замене черновика рисуем здесь */}
      <DraftReplaceDialog />
    </main>
  );
}

/** Экран-вопрос: заголовок, пояснение и кнопки */
function Screen({ title, text, children }: { title: string; text: string; children: ReactNode }) {
  return (
    <>
      <h1 className="mt-[32px] text-ui-32 font-[400] text-text">{title}</h1>
      <p className="mt-[16px] max-w-[480px] text-ui-16 font-[350] text-text-60">{text}</p>
      <div className="mt-[32px] flex flex-wrap justify-center gap-[12px]">{children}</div>
    </>
  );
}
