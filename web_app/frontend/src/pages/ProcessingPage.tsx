import { useEffect, useRef } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { api, ApiError } from '../lib/api';
import { Button } from '../components/ui/kit';
import { QueryError, queryDown } from '../components/ui/ErrorState';
import { BatchLayout, GenerationsCard, ProcessingAside, ProgressTrack, TrackCard } from '../components/project/BatchCards';
import { useQuizOnGeneration, useVideoRatings } from '../components/funnel/FunnelHost';

/** Средняя длительность рендера одной вариации — из неё считаем «осталось NN минут». */
const MINUTES_PER_VIDEO = 3;

/**
 * Генерация батча (Figma W51) — тот же макет, что и готовый батч (W36):
 * в шапке вместо пилюль батчей прогресс-бар, в списке готовые строки + строка-загрузка,
 * «Выложить все» ещё нет. Когда все ролики готовы — уходим на W36 (страница батча).
 *
 * Экран рисуется сразу и целиком: скелетонов здесь быть не должно — на этой странице
 * «загрузка» это и есть контент (прогресс в цифрах + заполнение шкалы).
 */
/** Полоса статуса упавшего батча: сколько собралось и что генерация остановлена (без «осталось N минут»). */
function FailedTrack({ done, total }: { done: number; total: number }) {
  const { t } = useTranslation();
  const pct = total ? done / total : 0;
  return (
    <div className="relative flex h-[60px] items-center overflow-hidden rounded-r15 bg-grad-soft-20 max-md:h-[44px]">
      <span className="absolute inset-y-0 left-0 rounded-r15 bg-success-bg" style={{ width: `${pct * 100}%` }} aria-hidden="true" />
      <span className="relative pl-[28px] text-ui-16 tabular-nums text-text max-md:pl-[14px] max-md:text-ui-14">{t('processing.failedDone', { done, total })}</span>
      <span className="relative ml-auto mr-[20px] flex items-center gap-[8px] rounded-full bg-warning-bg px-[12px] py-[4px] text-ui-14 text-warning max-md:mr-[10px]">
        <span className="h-[6px] w-[6px] rounded-full bg-warning" aria-hidden="true" />
        {t('processing.stopped')}
      </span>
    </div>
  );
}

export function ProcessingPage() {
  const { t } = useTranslation();
  const { jobId } = useParams();
  const navigate = useNavigate();

  const jobQuery = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api.job(jobId ?? ''),
    enabled: Boolean(jobId),
    // Несуществующий джоб ретраить бессмысленно — сразу показываем «не найдено»
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 1,
    refetchInterval: (query) => {
      const status = query.state.data?.job.status;
      return status === 'COMPLETED' || status === 'FAILED' ? false : 3000;
    }
  });
  const job = jobQuery.data?.job;
  const notFound = jobQuery.isError && jobQuery.error instanceof ApiError && jobQuery.error.status === 404;
  const failed = job?.status === 'FAILED' || job?.videos.some((video) => video.status === 'FAILED');
  const projectQuery = useQuery({
    queryKey: ['project', job?.projectId],
    queryFn: () => api.project(job?.projectId ?? ''),
    enabled: Boolean(job?.projectId)
  });
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: 15_000 });

  const videos = job?.videos ?? [];
  const done = videos.filter((video) => video.status === 'COMPLETED');
  const failedVideos = videos.filter((video) => video.status === 'FAILED');
  const activeVideo = videos.find((video) => video.status === 'PROCESSING')
    ?? videos.find((video) => video.status === 'PENDING' && video.stage !== 'waiting_previous')
    ?? videos.find((video) => video.status === 'PENDING');
  const activeVariation = job?.renderJob?.variations?.find((variation) => variation.index === activeVideo?.index);
  const activeFormat = activeVideo?.format ?? activeVariation?.background?.sourceFormat;
  const rootFailure = failedVideos.find((video) => video.stage !== 'skipped') ?? failedVideos[0];
  const rawFailure = rootFailure?.error?.split('\n')[0].trim() ?? '';
  const failureReason = rawFailure.includes('stage2_style_rotation_missing_artist_id')
    ? t('processing.reasonSourceMetadata')
    : rawFailure.includes('collection not found')
      ? t('processing.reasonCollectionMissing')
      : rawFailure.includes('solid backgrounds')
        ? t('processing.reasonSolidColor')
        : rawFailure
          ? t('processing.reasonStage', { stage: rootFailure?.stage || 'render' })
          : t('processing.reasonUnknown');
  const allDone = videos.length > 0 && done.length === videos.length;
  const project = projectQuery.data?.project;
  // Воронка: квиз, пока идёт рендер; оценка под каждым готовым роликом (первая 7+ — безлимит)
  // Упавший ролик уже рисует «Генерация не удалась» (джоб ещё может быть в PROCESSING) —
  // квиз поверх экрана ошибки не нужен.
  useQuizOnGeneration(failed ? undefined : job);
  const ratings = useVideoRatings(job, job?.projectId);

  /*
   * Метрика отвала на экране ожидания (из ревью): рендер идёт минутами, и главный вопрос —
   * дожидаются ли его вообще. Пишем, сколько человек провёл на экране и ушёл ли он до
   * готовности батча. `sendBeacon`-подобной надёжности не нужно: событие уходит на
   * размонтировании, а закрытая вкладка и так считается отвалом.
   */
  const waitStartedRef = useRef<number>(Date.now());
  const waitDoneRef = useRef(false);
  waitDoneRef.current = allDone;
  useEffect(() => {
    if (!jobId) return;
    const startedAt = Date.now();
    waitStartedRef.current = startedAt;
    void api.trackEvent('waiting_opened', { jobId });
    return () => {
      void api.trackEvent('waiting_left', {
        jobId,
        seconds: Math.round((Date.now() - startedAt) / 1000),
        completed: waitDoneRef.current
      });
    };
  }, [jobId]);

  /*
   * Батч собран — CJM ведёт на страницу батча (W36). Уходим ТОЛЬКО когда проект реально
   * подгрузился: иначе редирект упирался в «Проект не найден» и выглядел как сброс генерации.
   */
  useEffect(() => {
    if (allDone && project && !failed) navigate(`/app/projects/${project.id}`, { replace: true });
  }, [allDone, failed, project, navigate]);

  const total = videos.length || job?.versions || 0;
  const minutesLeft = Math.max(1, Math.ceil((total - done.length) * MINUTES_PER_VIDEO));

  /*
   * Джоба нет (перезапуск бэка, чужая/битая ссылка) — раньше страница молча рисовала
   * фантомный прогресс «0/0, осталось 1 минута» и висела так вечно.
   */
  if (notFound) {
    return (
      <div className="card-2 flex flex-1 flex-col items-center justify-center gap-space-4 p-[40px] text-center">
        <h1 className="text-ui-32 font-[400]">{t('processing.notFound')}</h1>
        <p className="max-w-[420px] text-ui-16 text-text-60">{t('processing.notFoundText')}</p>
        <Button variant="primary" size="lg" onClick={() => navigate('/app/projects')}>{t('common.toProjects')}</Button>
      </div>
    );
  }

  /*
   * Статус не загрузился (5xx, нет сети) и показать нечего — раньше страница вечно рисовала
   * «загрузку» 0/0. Явная ошибка с «Повторить»; генерация на бэке от этого не страдает.
   * Если данные уже были, а упал очередной опрос — оставляем последний известный прогресс.
   */
  if (!job && queryDown(jobQuery)) {
    return <QueryError query={jobQuery} />;
  }

  /*
   * Генерация упала — редирект по allDone уже не случится, нужен явный выход. Экран держит
   * тот же макет, что и генерация: слева трек и ролики (видно, что собралось, а что нет, и
   * готовые можно открыть), справа — что случилось и что делать дальше.
   */
  if (failed) {
    return (
      <BatchLayout
        left={
          <>
            <TrackCard title={project?.name} artistNick={meQuery.data?.user.artistNick || undefined}>
              <FailedTrack done={done.length} total={videos.length} />
            </TrackCard>
            <GenerationsCard
              videos={videos}
              postOne={project && done.length ? (video) => {
                const index = done.findIndex((item) => item.id === video.id);
                navigate(`/app/projects/${project.id}/post?batch=${job?.id}&video=${Math.max(0, index)}`);
              } : undefined}
            />
          </>
        }
        right={
          <aside className="wizard-aside card-2 flex shrink-0 flex-col p-[40px] max-md:order-first max-md:p-[20px]" aria-labelledby="failed-title">
            <span className="flex h-[44px] w-[44px] items-center justify-center rounded-full bg-warning-bg text-warning" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 8v5M12 16.5v.01" /><path d="M10.3 3.9 2.6 17.3A2 2 0 0 0 4.3 20.3h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" /></svg>
            </span>
            <h1 id="failed-title" className="mt-[20px] text-ui-24 font-[400] text-text">{t('processing.failed')}</h1>
            <p className="mt-[8px] text-ui-16 text-text-60">{t('processing.failedText')}</p>

            <div className="mt-[24px] rounded-r15 bg-panel p-[20px]">
              <p className="text-ui-12 text-text-40">{t('processing.whatHappened')}</p>
              <p className="mt-[6px] text-ui-16 text-text">{failureReason}</p>
              <p className="mt-[6px] text-ui-14 tabular-nums text-text-60">
                {t('processing.failedProgress', { done: done.length, total: videos.length, failed: failedVideos.length })}
              </p>
              {rawFailure && (
                <details className="group mt-[12px] text-ui-14 text-text-60">
                  <summary className="w-fit cursor-pointer list-none text-accent-light transition-colors hover:text-text [&::-webkit-details-marker]:hidden">
                    {t('processing.technicalReason')} <span className="inline-block transition-transform duration-200 group-open:rotate-90" aria-hidden="true">›</span>
                  </summary>
                  <code className="mt-[8px] block max-h-[120px] overflow-auto whitespace-pre-wrap break-words rounded-r10 bg-field p-[12px] font-mono text-ui-12 text-text-60">{rawFailure}</code>
                </details>
              )}
            </div>

            <div className="mt-auto flex flex-col gap-[10px] pt-[28px]">
              {job?.projectId && (
                <Button variant="primary" size="lg" className="w-full" onClick={() => navigate(`/app/generate?project=${job.projectId}`)}>
                  {t('processing.retry')}
                </Button>
              )}
              {job?.projectId && done.length > 0 && (
                <Button size="lg" className="w-full" onClick={() => navigate(`/app/projects/${job.projectId}`)}>
                  {t('processing.openReady')}
                </Button>
              )}
              <Button variant="ghost" size="md" className="w-full" onClick={() => navigate('/app/projects')}>{t('common.toProjects')}</Button>
            </div>
          </aside>
        }
      />
    );
  }

  return (
    <BatchLayout
      left={
        <>
          <TrackCard title={project?.name} artistNick={meQuery.data?.user.artistNick || undefined}>
            <ProgressTrack done={done.length} total={total} minutesLeft={minutesLeft} />
          </TrackCard>
          <GenerationsCard
            videos={videos}
            loading={!allDone}
            videoFooter={ratings.render}
            postOne={project ? (video) => {
              const index = done.findIndex((item) => item.id === video.id);
              navigate(`/app/projects/${project.id}/post?batch=${job?.id}&video=${Math.max(0, index)}`);
            } : undefined}
          />
        </>
      }
      right={
        <ProcessingAside
          done={done.length}
          total={total}
          activeVideo={activeVideo}
          renderFormat={activeFormat}
          telegram={Boolean(meQuery.data?.telegramNotifications)}
          onBack={() => navigate('/app/projects')}
        />
      }
    />
  );
}
