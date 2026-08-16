import { useTranslation } from 'react-i18next';
import { ApiError } from '../../lib/api';
import { cn } from '../../lib/cn';

/*
 * Экран «данные не загрузились». Скелетоны в приложении были, а поведения при упавшем API —
 * нет: запрос падал, и пользователь оставался в вечном скелетоне или на пустой карточке.
 *
 * Текст зависит от причины: нет сети → «проверь соединение», 5xx → «это на нашей стороне»,
 * остальное → общий вариант. Код показываем мелким шрифтом — он нужен поддержке, не юзеру.
 * 401 сюда не попадает: `request()` в lib/api уводит на /login по code=auth_required.
 */
function reason(error: unknown, offline?: boolean): { titleKey: string; textKey: string; code?: number } {
  const status = error instanceof ApiError ? error.status : undefined;
  if (offline || (typeof navigator !== 'undefined' && navigator.onLine === false)) {
    return { titleKey: 'error.offlineTitle', textKey: 'error.offlineText', code: status };
  }
  if (status !== undefined && status >= 500) {
    return { titleKey: 'error.serverTitle', textKey: 'error.serverText', code: status };
  }
  return { titleKey: 'error.title', textKey: 'error.text', code: status };
}

/**
 * Запрос «лёг» — не только явная ошибка.
 *
 * Без сети react-query НЕ роняет запрос: он ставит его на паузу (`fetchStatus === 'paused'`)
 * и ждёт возврата соединения, а `isError` остаётся false. Экран при этом навсегда застревал
 * в скелетоне — снаружи это выглядит как зависшее приложение. Пауза на pending-запросе для
 * пользователя такой же отказ, как 500-ка, поэтому считаем её отказом и показываем офлайн-текст.
 */
export type QueryLike = { isError: boolean; status: string; fetchStatus: string; error: unknown; isFetching: boolean; refetch: () => unknown };

export function queryDown(query: Pick<QueryLike, 'isError' | 'status' | 'fetchStatus'>): boolean {
  return query.isError || (query.status === 'pending' && query.fetchStatus === 'paused');
}

/** Состояние ошибки прямо из объекта useQuery — чтобы не собирать пропсы руками на каждом экране. */
export function QueryError({ query, className }: { query: QueryLike; className?: string }) {
  return (
    <ErrorState
      error={query.error}
      offline={query.status === 'pending' && query.fetchStatus === 'paused'}
      onRetry={() => query.refetch()}
      retrying={query.isFetching}
      className={className}
    />
  );
}

/** Полноразмерное состояние ошибки — вместо карточки/страницы. */
export function ErrorState({ error, onRetry, retrying, offline, className }: {
  error: unknown;
  onRetry?: () => void;
  retrying?: boolean;
  offline?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const { titleKey, textKey, code } = reason(error, offline);
  return (
    <div className={cn('card-2 flex min-h-[260px] flex-1 flex-col items-center justify-center px-[28px] py-[40px] text-center', className)} role="alert">
      <h2 className="text-[28px] font-[400] leading-[34px] text-text">{t(titleKey)}</h2>
      <p className="mt-[12px] max-w-[420px] text-[16px] leading-[21px] text-text-60">{t(textKey)}</p>
      {code !== undefined && <p className="mt-[8px] text-[13px] leading-none text-text-40">{t('error.code', { code })}</p>}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          disabled={retrying}
          className="mt-[24px] flex h-[60px] items-center justify-center rounded-r15 border border-accent-light bg-grad-soft-20 px-[28px] text-[20px] font-[350] leading-none text-text-80 transition hover:text-text disabled:cursor-wait disabled:opacity-60"
        >
          {retrying ? t('error.retrying') : t('error.retry')}
        </button>
      )}
    </div>
  );
}

/** Компактный вариант — внутри уже существующей карточки (блок дашборда, панель визарда). */
export function InlineError({ error, onRetry, retrying, offline }: { error: unknown; onRetry?: () => void; retrying?: boolean; offline?: boolean }) {
  const { t } = useTranslation();
  const { titleKey, code } = reason(error, offline);
  return (
    <div className="flex min-h-[120px] flex-col items-center justify-center gap-[10px] px-[20px] text-center" role="alert">
      <p className="text-[16px] leading-[20px] text-text-60">
        {t(titleKey)}
        {code !== undefined && <span className="ml-[6px] text-text-40">({code})</span>}
      </p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          disabled={retrying}
          className="rounded-r10 border border-[rgba(246,245,253,0.2)] px-[14px] py-[8px] text-[15px] leading-none text-text-80 transition hover:border-accent-light hover:text-text disabled:opacity-60"
        >
          {retrying ? t('error.retrying') : t('error.inlineRetry')}
        </button>
      )}
    </div>
  );
}
