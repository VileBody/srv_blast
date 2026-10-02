import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../lib/api';
import { useToast } from '../contexts/ToastContext';
import { isVideoPosted } from '../lib/types';
import { Skeleton } from '../components/ui/Skeleton';
import { QueryError, queryDown } from '../components/ui/ErrorState';
import { BatchLayout, BatchTrack, GenerationsCard, PreviewColumn, TrackCard } from '../components/project/BatchCards';
import { startNextBatch } from '../stores/wizardStore';
import { trackOfJob, useVideoRatings } from '../components/funnel/FunnelHost';
import { useFunnelState } from '../components/funnel/useFunnel';
import { markFunnelSeen, useFunnelUi } from '../stores/funnelUi';

/** Батч видео (Figma W36, состояние с лимитами — W47). Раскладка общая с W51 (генерация). */
export function ProjectDetailPage() {
  const { t } = useTranslation();
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { push } = useToast();
  const projectQuery = useQuery({ queryKey: ['project', id], queryFn: () => api.project(id ?? ''), enabled: Boolean(id) });
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: 15_000 });
  const project = projectQuery.data?.project;
  const jobs = useMemo(
    () => [...(project?.jobs ?? [])].sort((left, right) => left.createdAt.localeCompare(right.createdAt) || left.id.localeCompare(right.id)),
    [project]
  );
  const [selectedJobId, setSelectedJobId] = useState<string>();
  const selectedJob = jobs.find((job) => job.id === selectedJobId) ?? jobs[jobs.length - 1];
  const videos = selectedJob?.videos ?? [];
  const completedVideos = videos.filter((video) => video.status === 'COMPLETED');
  const ratings = useVideoRatings(selectedJob, id);
  const [search, setSearch] = useSearchParams();
  const funnelQuery = useFunnelState();
  const openUnlimited = useFunnelUi((state) => state.openUnlimited);

  // «Оценить и получить безлимит» из Telegram (?unlimited=1): сразу модалка безлимита
  // по последнему батчу. Параметр снимаем, чтобы обновление страницы её не открывало.
  const unlimitedParam = search.get('unlimited') === '1';
  const funnelReady = Boolean(funnelQuery.data) || funnelQuery.isError;
  useEffect(() => {
    if (!unlimitedParam || !selectedJob || !funnelReady) return;
    if (funnelQuery.data && !funnelQuery.data.hasPaid) {
      const track = trackOfJob(selectedJob);
      markFunnelSeen(`unlimited:${selectedJob.id}`);
      openUnlimited({
        source: 'results',
        jobId: selectedJob.id,
        projectId: id,
        trackId: track.id,
        audioHash: track.audioHash,
        trackTitle: track.title,
        videos: selectedJob.videos
      });
    }
    search.delete('unlimited');
    setSearch(search, { replace: true });
  }, [unlimitedParam, selectedJob, funnelReady, funnelQuery.data, openUnlimited, id, search, setSearch]);

  // Возврат из банка после трипваера 399 ₽ (оплату подтверждает бот, лимиты снимает credits_db)
  useEffect(() => {
    const payment = search.get('payment');
    if (!payment) return;
    push(payment === 'success'
      ? { variant: 'success', title: t('funnel.tripwire.paid') }
      : { variant: 'error', title: t('funnel.tripwire.failed') });
    void queryClient.invalidateQueries({ queryKey: ['funnel-state'] });
    void queryClient.invalidateQueries({ queryKey: ['me'] });
    search.delete('payment');
    setSearch(search, { replace: true });
  }, [search, setSearch, push, t, queryClient]);

  const activateMutation = useMutation({
    mutationFn: () => api.activateProject(id ?? ''),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['project', id] }),
        queryClient.invalidateQueries({ queryKey: ['projects'] })
      ]);
      push({ variant: 'success', title: t('projectDetail.madeCurrent') });
    },
    onError: () => push({ variant: 'error', title: t('simple.error') })
  });

  /*
   * «+» у батча = новый батч по тому же треку: как «Сделать ещё» на странице проектов,
   * ведёт в визард сразу на этап «Фон» — но только если трек этого проекта реально лежит
   * в сторе. Иначе (первый батч, другой проект, чистая сессия) начинаем с «Трек»:
   * без трека и текста генерировать нечего.
   */
  const addBatch = () => navigate(startNextBatch(id ?? ''));

  // 404 разбираем ниже отдельным экраном «проект не найден» — здесь только сбой загрузки
  if (queryDown(projectQuery) && !(projectQuery.error instanceof ApiError && projectQuery.error.status === 404)) {
    return <QueryError query={projectQuery} className="min-h-[620px]" />;
  }
  if (projectQuery.isLoading) return <Skeleton className="h-full min-h-[620px]" />;
  if (!project) {
    return (
      <div className="card-2 flex flex-1 flex-col items-center justify-center gap-space-4 p-[40px] text-center">
        <h1 className="text-[32px] font-[400]">{t('projectDetail.notFound')}</h1>
        <button type="button" className="soft-btn h-[60px] px-space-6 text-[20px]" onClick={() => navigate('/app/projects')}>{t('common.toProjects')}</button>
      </div>
    );
  }

  return (
    <BatchLayout
      left={
        <>
          <TrackCard
            title={project.name}
            artistNick={meQuery.data?.user.artistNick || undefined}
            current={project.isCurrent}
            onMakeCurrent={() => activateMutation.mutate()}
          >
            <BatchTrack
              batches={jobs.map((job, index) => ({ id: job.id, number: index + 1 }))}
              selectedId={selectedJob?.id}
              onSelect={setSelectedJobId}
              onAddBatch={addBatch}
            />
          </TrackCard>
          <GenerationsCard
            videos={videos}
            // «Выложить все» стартует с первого ещё НЕ опубликованного ролика (уже выложенные пропускаем)
            postAll={selectedJob && completedVideos.length > 0
              ? () => { const start = Math.max(0, completedVideos.findIndex((v) => !isVideoPosted(v))); navigate(`/app/projects/${id}/post?batch=${selectedJob.id}&video=${start}`); }
              : undefined}
            postOne={(video) => {
              const index = completedVideos.findIndex((item) => item.id === video.id);
              navigate(`/app/projects/${id}/post?batch=${selectedJob?.id}&video=${Math.max(0, index)}`);
            }}
            onEmptyAction={addBatch}
            videoFooter={ratings.render}
            track={{ ...trackOfJob(selectedJob), projectId: id }}
          />
        </>
      }
      right={<PreviewColumn videos={completedVideos} onBack={() => navigate('/app/projects')} />}
    />
  );
}
