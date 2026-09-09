import { useEffect, useRef } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { api, ApiError } from '../lib/api';
import { BatchLayout, GenerationsCard, ProcessingAside, ProgressTrack, TrackCard } from '../components/project/BatchCards';

/** Средняя длительность рендера одной вариации — из неё считаем «осталось NN минут». */
const MINUTES_PER_VIDEO = 1.5;

/**
 * Генерация батча (Figma W51) — тот же макет, что и готовый батч (W36):
 * в шапке вместо пилюль батчей прогресс-бар, в списке готовые строки + строка-загрузка,
 * «Выложить все» ещё нет. Когда все ролики готовы — уходим на W36 (страница батча).
 *
 * Экран рисуется сразу и целиком: скелетонов здесь быть не должно — на этой странице
 * «загрузка» это и есть контент (прогресс в цифрах + заполнение шкалы).
 */
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
  const allDone = videos.length > 0 && done.length === videos.length;
  const project = projectQuery.data?.project;

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
        <h1 className="text-[32px] font-[400]">{t('processing.notFound')}</h1>
        <p className="max-w-[420px] text-[18px] leading-[23px] text-text-60">{t('processing.notFoundText')}</p>
        <button type="button" className="soft-btn h-[60px] px-space-6 text-[20px]" onClick={() => navigate('/app/projects')}>{t('common.toProjects')}</button>
      </div>
    );
  }

  /* Генерация упала — редирект по allDone уже не случится, нужен явный выход. */
  if (failed) {
    return (
      <div className="card-2 flex flex-1 flex-col items-center justify-center gap-space-4 p-[40px] text-center">
        <h1 className="text-[32px] font-[400]">{t('processing.failed')}</h1>
        <p className="max-w-[420px] text-[18px] leading-[23px] text-text-60">{t('processing.failedText')}</p>
        <div className="flex flex-wrap items-center justify-center gap-space-3">
          {job?.projectId && (
            <button type="button" className="soft-btn h-[60px] px-space-6 text-[20px]" onClick={() => navigate(`/app/generate?project=${job.projectId}`)}>
              {t('processing.retry')}
            </button>
          )}
          <button type="button" className="soft-btn h-[60px] px-space-6 text-[20px]" onClick={() => navigate('/app/projects')}>{t('common.toProjects')}</button>
        </div>
      </div>
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
            videos={done}
            loading={!allDone}
            postOne={project ? (index) => navigate(`/app/projects/${project.id}/post?video=${index}`) : undefined}
          />
        </>
      }
      right={
        <ProcessingAside
          done={done.length}
          total={total}
          telegram={Boolean(meQuery.data?.telegramNotifications)}
          onBack={() => navigate('/app/projects')}
        />
      }
    />
  );
}
