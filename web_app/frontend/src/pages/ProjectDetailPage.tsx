import { useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../lib/api';
import { useToast } from '../contexts/ToastContext';
import { isVideoPosted } from '../lib/types';
import { Skeleton } from '../components/ui/Skeleton';
import { QueryError, queryDown } from '../components/ui/ErrorState';
import { BatchLayout, BatchTrack, GenerationsCard, PreviewColumn, TrackCard } from '../components/project/BatchCards';
import { hasTrackInput, useWizardStore } from '../stores/wizardStore';

/** Батч видео (Figma W36, состояние с лимитами — W47). Раскладка общая с W51 (генерация). */
export function ProjectDetailPage() {
  const { t } = useTranslation();
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { push } = useToast();
  const projectQuery = useQuery({ queryKey: ['project', id], queryFn: () => api.project(id ?? ''), enabled: Boolean(id) });
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: 15_000 });
  const reset = useWizardStore((state) => state.reset);
  const newBatch = useWizardStore((state) => state.newBatch);
  const setStage = useWizardStore((state) => state.setStage);
  const project = projectQuery.data?.project;
  const videos = useMemo(() => project?.jobs?.flatMap((job) => job.videos) ?? [], [project]);

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
  const addBatch = () => {
    const state = useWizardStore.getState();
    const sameProject = state.projectId === id;
    if (sameProject && hasTrackInput(state)) {
      newBatch(id);
      setStage(2);
    } else if (sameProject && state.track) {
      // трек уже загружен (например, в модалке «Новый проект») — файл не переспрашиваем,
      // но текст отрывка ещё нужен, поэтому начинаем с этапа «Трек»
      newBatch(id);
      setStage(1);
    } else {
      reset(id);
      setStage(1);
    }
    navigate(`/app/generate?project=${id}`);
  };

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
            <BatchTrack onAddBatch={addBatch} />
          </TrackCard>
          <GenerationsCard
            videos={videos}
            // «Выложить все» стартует с первого ещё НЕ опубликованного ролика (уже выложенные пропускаем)
            postAll={videos.length > 0 && videos.every((video) => video.status === 'COMPLETED')
              ? () => { const start = Math.max(0, videos.findIndex((v) => !isVideoPosted(v))); navigate(`/app/projects/${id}/post?video=${start}`); }
              : undefined}
            postOne={(index) => navigate(`/app/projects/${id}/post?video=${index}`)}
            onEmptyAction={addBatch}
          />
        </>
      }
      right={<PreviewColumn videos={videos} onBack={() => navigate('/app/projects')} />}
    />
  );
}
