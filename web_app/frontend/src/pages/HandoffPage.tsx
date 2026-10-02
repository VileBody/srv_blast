import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Button, buttonClass } from '../components/ui/kit';
import { useToast } from '../contexts/ToastContext';
import { api, ApiError } from '../lib/api';
import { useWizardStore } from '../stores/wizardStore';

type Failure = 'expired' | 'error';

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
  const { token = '' } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { push } = useToast();
  const [failure, setFailure] = useState<Failure | null>(null);
  // StrictMode в деве зовёт эффект дважды; сама ручка идемпотентна, но лишний
  // запрос съедал бы лимит /api/auth/*.
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    api.botHandoff(token)
      .then((res) => {
        queryClient.removeQueries({ queryKey: ['me'] });
        if (res.projectId) {
          const store = useWizardStore.getState();
          if (store.projectId !== res.projectId) store.reset(res.projectId);
          if (res.track) store.setTrack(res.track);
          // «Докрутить на сайте»: отрезок и текст ролика из бота сразу в черновике
          // только при первом открытии: повтор той же ссылки не затирает правки
          if (!res.repeat && res.draft && res.draft.clipEnd > res.draft.clipStart) {
            store.setField('timingMode', 'manual');
            store.setField('timingFrom', secondsToTiming(res.draft.clipStart));
            store.setField('timingTo', secondsToTiming(res.draft.clipEnd));
            if (res.draft.lyrics) store.setField('lyrics', res.draft.lyrics);
          }
        }
        if (res.trackError === 'tracks_limit') {
          push({ variant: 'error', title: t('handoff.tracksLimit'), text: t('handoff.tracksLimitText') });
        } else if (res.track && !res.repeat) {
          push({ variant: 'success', title: t('handoff.trackReady'), text: t('handoff.trackReadyText') });
        }
        navigate(res.redirectTo, { replace: true });
      })
      .catch((error: unknown) => {
        setFailure(error instanceof ApiError && error.status === 410 ? 'expired' : 'error');
      });
  }, [token, navigate, queryClient, push, t]);

  return (
    <main className="flex min-h-dvh items-center justify-center bg-bg p-[24px] text-center">
      <section className="card-2 flex w-full flex-col items-center justify-center px-[24px] py-[60px]" style={{ maxWidth: 760, minHeight: 420 }}>
        <img src="/assets/figma/logo-star.svg" width="60" height="60" alt="Blast" />
        {failure === null ? (
          <p className="mt-[32px] text-ui-16 font-[350] text-text-60" role="status">
            {t('handoff.loading')}
          </p>
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
