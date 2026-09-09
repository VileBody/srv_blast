import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { LEGAL_ENTITY } from '../data/legal-docs';
import { LEGAL_LINKS } from '../lib/legal';

/**
 * Экран заблокированного аккаунта.
 *
 * Показывается вместо всего приложения: бэк отвечает 403 `account_banned` на любую ручку,
 * кроме статуса бана и выхода, и api-слой уводит сюда. Задача экрана — объяснить причину
 * своими словами, а не оставить человека с «сервер прилёг» на каждом экране.
 *
 * Сессия при бане намеренно не сбрасывается: без неё причину узнать было бы негде.
 */
export function BlockedPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const statusQuery = useQuery({ queryKey: ['ban-status'], queryFn: api.banStatus, retry: false });
  const logoutMutation = useMutation({
    mutationFn: api.logout,
    onSuccess: () => window.location.replace('/login')
  });

  const banned = statusQuery.data?.banned;
  const reason = statusQuery.data?.reason;

  // Не забанен — экрану тут делать нечего (например, зашёл по ссылке из истории)
  useEffect(() => {
    if (statusQuery.data && !banned) navigate('/app', { replace: true });
  }, [banned, navigate, statusQuery.data]);

  const bannedAt = statusQuery.data?.bannedAt
    ? new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' }).format(
        new Date(statusQuery.data.bannedAt)
      )
    : null;

  return (
    <main className="flex min-h-dvh items-center justify-center bg-bg p-[24px]">
      <section className="card-2 flex w-full flex-col px-[40px] py-[48px]" style={{ maxWidth: 720 }}>
        <img src="/assets/figma/logo-star.svg" width="48" height="48" alt="Blast" />
        <h1 className="mt-[28px] text-[32px] font-[400] leading-[38px] text-text">{t('blocked.title')}</h1>

        <p className="mt-[16px] text-[18px] font-[350] leading-[26px] text-text-80">
          {reason === 'tiktok_reuse' ? t('blocked.tiktokReuse') : t('blocked.generic')}
        </p>
        {reason === 'tiktok_reuse' && (
          <p className="mt-[12px] text-[17px] font-[350] leading-[25px] text-text-60">{t('blocked.tiktokReuseWhy')}</p>
        )}
        {bannedAt && <p className="mt-[16px] text-[15px] leading-[20px] text-text-40">{t('blocked.since', { date: bannedAt })}</p>}

        <div className="mt-[28px] rounded-r12 px-[20px] py-[16px]" style={{ background: 'var(--grad-soft-10)' }}>
          <p className="text-[16px] font-[350] leading-[23px] text-text-80">
            {t('blocked.appeal')}
            {LEGAL_ENTITY.email ? (
              <>
                {' '}
                <a className="text-accent-light underline underline-offset-2" href={`mailto:${LEGAL_ENTITY.email}`}>
                  {LEGAL_ENTITY.email}
                </a>
              </>
            ) : (
              <> {t('blocked.appealBot')}</>
            )}
          </p>
        </div>

        <div className="mt-[28px] flex flex-wrap items-center gap-[12px]">
          <button
            type="button"
            onClick={() => logoutMutation.mutate()}
            disabled={logoutMutation.isPending}
            className="flex h-[56px] items-center rounded-r15 bg-grad-main px-[26px] text-[18px] leading-none text-text transition hover:brightness-110 disabled:opacity-60"
          >
            {logoutMutation.isPending ? t('common.loading') : t('blocked.logout')}
          </button>
          <a
            href={LEGAL_LINKS.offer}
            target="_blank"
            rel="noreferrer"
            className="flex h-[56px] items-center rounded-r15 border border-accent-light bg-grad-soft-20 px-[26px] text-[17px] leading-none text-text-80 transition hover:text-text"
          >
            {t('blocked.rules')}
          </a>
        </div>
      </section>
    </main>
  );
}
