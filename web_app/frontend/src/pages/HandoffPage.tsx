import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Button, buttonClass } from '../components/ui/kit';
import { useToast } from '../contexts/ToastContext';
import { api, ApiError } from '../lib/api';
import { useWizardStore } from '../stores/wizardStore';
import { apiErrorCode } from '../components/funnel/useFunnel';

type Failure = 'expired' | 'error' | 'otherAccount' | 'linkAccount';
/** Ответ человека на вопрос про аккаунт в браузере (см. api.botHandoff) */
type Answer = { force?: boolean; link?: boolean };

/** Почта (или имя) открытого аккаунта из 409 handoff_link_account — для текста вопроса */
function linkAccountLabel(error: unknown): string {
  if (!(error instanceof ApiError)) return '';
  const detail = (error.detail as { detail?: { email?: string; name?: string } } | null)?.detail;
  return detail?.email || detail?.name || '';
}

/** секунды → «мм:сс:сс» (формат полей тайминга визарда, см. timingToSeconds) */
function secondsToTiming(value: number): string {
  const cs = Math.round(Math.max(0, value) * 100);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(Math.floor(cs / 6000))}:${pad(Math.floor((cs % 6000) / 100))}:${pad(cs % 100)}`;
}

/**
 * Ссылка «на сайт» из публичного бота: `/go/<token>`.
 *
 * Бэк сам логинит человека по токену (его выпустил бот, chat_id уже известен) и,
 * если в ссылке трек, заводит проект с этим треком. Здесь остаётся положить трек
 * в черновик визарда и открыть его — дальше ведут обычные подсказки визарда.
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
  const [failure, setFailure] = useState<Failure | null>(null);
  // В браузере другой аккаунт или аккаунт без Telegram: входим по ссылке только после
  // ответа человека (force — сменить / войти отдельно, link — привязать Telegram).
  const [answer, setAnswer] = useState<Answer>({});
  // Почта открытого аккаунта без Telegram — в вопросе «Привязать Telegram к …?»
  const [linkLabel, setLinkLabel] = useState('');
  // StrictMode в деве зовёт эффект дважды; сама ручка идемпотентна, но лишний
  // запрос съедал бы лимит /api/auth/*. Помним, с каким ответом уже ходили.
  const started = useRef<string | null>(null);

  useEffect(() => {
    const attempt = JSON.stringify(answer);
    if (started.current === attempt) return;
    started.current = attempt;
    api.botHandoff(token, answer)
      .then(async (res) => {
        queryClient.removeQueries({ queryKey: ['me'] });
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
              // Черновик не лёг (битый импорт, не загрузился модуль): говорим об этом, а
              // проект всё равно открываем — трек там уже есть.
              importFailed = true;
              console.error('handoff: wizard import failed', error);
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
          push({ variant: 'error', title: t('handoff.remixFailed'), text: t('handoff.remixApplyFailed') });
        } else if (!res.repeat && res.wizardImportError) {
          push({ variant: 'error', title: t('handoff.remixFailed'), text: res.wizardImportError });
        } else if (imported) {
          const notes = res.wizardImport?.notes ?? [];
          push({ variant: notes.length ? 'info' : 'success', title: t('handoff.remixReady'), text: notes.length ? notes.join(' ') : t('handoff.remixReadyText') });
        } else if (res.track && !res.repeat) {
          push({ variant: 'success', title: t('handoff.trackReady'), text: t('handoff.trackReadyText') });
        }
        navigate(res.redirectTo, { replace: true });
      })
      .catch((error: unknown) => {
        const code = apiErrorCode(error);
        if (code === 'handoff_other_account') setFailure('otherAccount');
        else if (code === 'handoff_link_account') {
          setLinkLabel(linkAccountLabel(error));
          setFailure('linkAccount');
        } else setFailure(error instanceof ApiError && error.status === 410 ? 'expired' : 'error');
      });
  }, [token, answer, navigate, queryClient, push, t]);

  const reply = (next: Answer) => {
    setFailure(null);
    setAnswer(next);
  };

  return (
    <main className="flex min-h-dvh items-center justify-center bg-bg p-[24px] text-center">
      <section className="card-2 flex w-full flex-col items-center justify-center px-[24px] py-[60px]" style={{ maxWidth: 760, minHeight: 420 }}>
        <img src="/assets/figma/logo-star.svg" width="60" height="60" alt="Blast" />
        {failure === null ? (
          <p className="mt-[32px] text-ui-16 font-[350] text-text-60" role="status">
            {t('handoff.loading')}
          </p>
        ) : failure === 'otherAccount' ? (
          <>
            <h1 className="mt-[32px] text-ui-32 font-[400] text-text">{t('handoff.otherAccount')}</h1>
            <p className="mt-[16px] max-w-[480px] text-ui-16 font-[350] text-text-60">{t('handoff.otherAccountText')}</p>
            <div className="mt-[32px] flex flex-wrap justify-center gap-[12px]">
              <Button variant="primary" size="lg" onClick={() => reply({ force: true })}>
                {t('handoff.otherAccountConfirm')}
              </Button>
              <Link className={buttonClass({ variant: 'secondary', size: 'lg' })} to="/app">
                {t('handoff.otherAccountStay')}
              </Link>
            </div>
          </>
        ) : failure === 'linkAccount' ? (
          <>
            <h1 className="mt-[32px] text-ui-32 font-[400] text-text">
              {linkLabel ? t('handoff.linkAccount', { account: linkLabel }) : t('handoff.linkAccountNoName')}
            </h1>
            <p className="mt-[16px] max-w-[480px] text-ui-16 font-[350] text-text-60">{t('handoff.linkAccountText')}</p>
            <div className="mt-[32px] flex flex-wrap justify-center gap-[12px]">
              <Button variant="primary" size="lg" onClick={() => reply({ link: true })}>
                {t('handoff.linkAccountConfirm')}
              </Button>
              <Button variant="secondary" size="lg" onClick={() => reply({ force: true })}>
                {t('handoff.linkAccountSeparate')}
              </Button>
            </div>
          </>
        ) : (
          <>
            <h1 className="mt-[32px] text-ui-32 font-[400] text-text">
              {t(failure === 'expired' ? 'handoff.expired' : 'handoff.error')}
            </h1>
            <p className="mt-[16px] max-w-[480px] text-ui-16 font-[350] text-text-60">
              {t(failure === 'expired' ? 'handoff.expiredText' : 'handoff.errorText')}
            </p>
            <div className="mt-[32px] flex flex-wrap justify-center gap-[12px]">
              {failure === 'error' && (
                <Button variant="primary" size="lg" onClick={() => location.reload()}>
                  {t('simple.refresh')}
                </Button>
              )}
              <Link className={buttonClass({ variant: failure === 'error' ? 'secondary' : 'primary', size: 'lg' })} to="/login">
                {t('handoff.toLogin')}
              </Link>
            </div>
          </>
        )}
      </section>
    </main>
  );
}
