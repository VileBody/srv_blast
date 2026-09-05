import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { TiktokButton } from '../components/ui/TiktokButton';
import { cn } from '../lib/cn';
import { useWizardStore } from '../stores/wizardStore';
import { CreateProjectModal } from '../components/project/CreateProjectModal';
import { InsightChart } from './StatsPage';
import { api } from '../lib/api';
import type { IterationAnalysis, Project } from '../lib/types';
import { DIMENSION_KEY, leaderBars, leadingDimension } from '../lib/analysis';
import { Skeleton } from '../components/ui/Skeleton';
import { QueryError, queryDown } from '../components/ui/ErrorState';
import { statusLabel } from '../components/ui/StatusBadge';
import { FigIcon } from '../components/ui/FigIcon';

const STATUS: Record<string, { key: string; dot: string }> = {
  ACTIVE: { key: 'active', dot: '#04BA38' },
  COMPLETED: { key: 'completed', dot: '#04BA38' },
  IN_PROGRESS: { key: 'inProgress', dot: '#ABBA04' }
};

/* Ноль просмотров на 48-м кегле читался как «провал», хотя обычно это просто «ещё ничего
   не выложено». Данных нет — ставим прочерк, а не большой ноль. */
function formatViews(n?: number): string {
  if (typeof n !== 'number' || n <= 0) return '—';
  if (n < 1000) return String(n);
  const k = n / 1000;
  const s = k >= 10 ? Math.round(k).toString() : k.toFixed(1).replace(/\.0$/, '');
  return `${s}k`;
}

/** Заголовок карточки: 32px Point Book + стрелка «›» (Figma 662:3 / 662:48). */
function CardHeader({ title, to }: { title: string; to: string }) {
  return (
    <Link to={to} className="group flex items-center gap-[14px] text-text">
      <span className="text-[32px] font-[350] leading-none">{title}</span>
      <FigIcon
        name="home-arrow.svg"
        h={16}
        className="translate-y-[1px] transition-transform duration-200 group-hover:translate-x-[4px]"
      />
    </Link>
  );
}

/** Строка проекта: обложка 80×80 r5, название 24, статус с точкой, справа «глаз» + просмотры 48. */
function ProjectRow({ project }: { project: Project }) {
  const { t } = useTranslation();
  const s = STATUS[project.status];
  const label = s ? t(`status.${s.key}`) : statusLabel(project.status);
  const dot = s?.dot ?? 'var(--accent-light)';
  const views = formatViews(project.views);
  /*
   * «N из M выложено» — то, что артисту нужно от строки проекта: статус говорит про стадию
   * проекта, просмотры — про итог, а между ними стоит незакрытое дело. Пока ничего не
   * сгенерировано, показывать нечего — блок скрыт целиком, а не «0 из 0».
   */
  const generated = project.generated ?? 0;
  const posted = Math.min(project.posted ?? 0, generated);
  const allPosted = generated > 0 && posted === generated;
  return (
    <Link to={`/app/projects/${project.id}`} className="group flex items-center gap-space-5">
      <img
        src={project.coverUrl ?? '/assets/cover-placeholder.svg'}
        alt=""
        className="h-[80px] w-[80px] shrink-0 rounded-[5px] object-cover"
      />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[24px] leading-none text-text-80 transition-colors group-hover:text-text">
          {project.name}
        </div>
        <div className="mt-[14px] flex min-w-0 items-center gap-[8px] text-[16px] leading-none text-text-80">
          <span className="h-[6px] w-[6px] shrink-0 rounded-full" style={{ background: dot }} />
          <span className="truncate">{label}</span>
        </div>
      </div>
      {/* Счётчик выкладки живёт в правой колонке, а не в строке статуса: под названием всего
          232px, и «1/4 выложено» рядом со статусом переносилось на вторую строку. */}
      <div className="flex shrink-0 flex-col items-end gap-[8px]">
        <div className="flex items-center gap-space-3">
          <img src="/assets/figma/home-eye.svg" width="20" height="13" alt="" aria-hidden />
          <span className={cn('text-[48px] font-[350] leading-none', views === '—' ? 'text-text-40' : 'text-text')} title={views === '—' ? t('projects.noViewsYet') : undefined}>{views}</span>
        </div>
        {generated > 0 && (
          <span className={cn('whitespace-nowrap text-[15px] leading-none', allPosted ? 'text-text-40' : 'text-text-80')}>
            {allPosted ? t('projects.allPosted') : t('projects.postedOf', { posted, total: generated })}
          </span>
        )}
      </div>
    </Link>
  );
}

/**
 * Hero: тёмная карта r25 с фиолетовыми линиями и приветствием (Figma 661:6825).
 *
 * Если есть что продолжить (готовый невыложенный батч или брошенный визард) — hero об этом
 * и говорит: раньше это была отдельная полоска над hero, и главный экран начинался с «создай
 * ещё один проект», хотя человеку нужно было доделать начатое. Кнопка создания при этом
 * никуда не девается — она вторая, рядом.
 */
function Hero({ name, resume, onCreate, onResume }: {
  name?: string;
  resume?: { kind: 'post' | 'wizard'; projectName: string; count?: number } | null;
  onCreate: () => void;
  onResume: () => void;
}) {
  const { t } = useTranslation();
  return (
    <section className="card-2 relative flex min-h-[300px] items-center justify-center overflow-hidden lg:min-h-0 lg:flex-1">
      <img
        src="/assets/figma/home-lines.svg"
        alt=""
        aria-hidden
        className="pointer-events-none absolute left-[-37.2%] top-0 aspect-[2078/728] w-[174.3%] max-w-none select-none"
      />
      <div className="relative flex flex-col items-center px-space-6 text-center">
        <h1
          className="text-[clamp(40px,5vw,64px)] font-[400] leading-none text-transparent"
          style={{
            backgroundImage: 'linear-gradient(184deg, #f6f5fd 8%, rgba(246,245,253,.8) 95%)',
            WebkitBackgroundClip: 'text',
            backgroundClip: 'text'
          }}
        >
          {/* именно ||: у нового аккаунта имя — пустая строка, а не undefined */}
          {t('dashboard.greeting', { name: name || t('dashboard.greetingFallback') })}
        </h1>

        {resume ? (
          <>
            {/* Без надстрочного «ПРОДОЛЖИТЬ»: сама фраза «N роликов готовы в …» уже говорит,
                что это незакрытое дело, а капслок над ней только шумел. */}
            <p className="mt-[24px] max-w-[460px] text-[24px] font-[350] leading-[1.15] text-text-80">
              {resume.kind === 'post'
                ? t('dashboard.resumePost', { count: resume.count ?? 0, name: resume.projectName })
                : t('dashboard.resumeWizard', { name: resume.projectName })}
            </p>
            <div className="mt-[36px] flex flex-wrap items-center justify-center gap-[16px]">
              <button
                type="button"
                onClick={onResume}
                className="flex h-[60px] items-center rounded-r15 bg-accent px-space-6 text-[20px] font-[400] leading-none text-text transition hover:brightness-110 focus-visible:outline-none"
              >
                {resume.kind === 'post' ? t('dashboard.resumePostCta') : t('dashboard.resumeWizardCta')}
              </button>
              <button type="button" onClick={onCreate} className="soft-btn h-[60px] gap-space-3 px-space-6 text-[20px] font-[400]">
                <img src="/assets/figma/home-note.svg" width="14" height="19" alt="" aria-hidden />
                {t('dashboard.createProject')}
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="mt-[24px] max-w-[320px] text-[24px] font-[350] leading-[1.15] text-text-80">
              {t('dashboard.subtitle')}
            </p>
            <button
              type="button"
              onClick={onCreate}
              className="soft-btn mt-[40px] h-[60px] gap-space-3 px-space-6 text-[20px] font-[400]"
            >
              <img src="/assets/figma/home-note.svg" width="14" height="19" alt="" aria-hidden />
              {t('dashboard.createProject')}
            </button>
          </>
        )}
      </div>
    </section>
  );
}

/**
 * Фантомная строка проекта на нулевом аккаунте: та же раскладка, что у настоящей,
 * но пустыми блоками — юзер сразу видит, что здесь появится, вместо голой строки текста.
 */
function GhostProjectRow() {
  // Фантомы держим глухими (opacity .18 + плоская заливка вместо градиента): они фон для
  // сообщения, а не самостоятельный контент — на .4 с градиентом они спорили с текстом.
  const bar = 'block rounded-[4px] bg-[rgba(246,245,253,0.10)]';
  return (
    <div aria-hidden="true" className="flex items-center gap-space-5 opacity-[0.18]">
      <span className="h-[80px] w-[80px] shrink-0 rounded-[5px] border border-dashed border-[rgba(246,245,253,0.18)]" />
      <div className="min-w-0 flex-1">
        <span className={cn(bar, 'h-[18px] w-[60%]')} />
        <div className="mt-[14px] flex items-center gap-[8px]">
          <span className="h-[6px] w-[6px] shrink-0 rounded-full bg-[rgba(246,245,253,0.10)]" />
          <span className={cn(bar, 'h-[12px] w-[84px]')} />
        </div>
      </div>
      <span className={cn(bar, 'h-[34px] w-[74px] shrink-0')} />
    </div>
  );
}

/** «Все проекты»: список из последних проектов с разделителем. */
function ProjectsCard({ projects }: { projects: Project[] }) {
  const { t } = useTranslation();
  return (
    <section className="card-2 flex flex-col overflow-hidden p-[40px]">
      <CardHeader title={t('dashboard.allProjects')} to="/app/projects" />
      <div className="mt-[40px] flex flex-1 flex-col justify-center">
        {projects.length === 0 ? (
          // Нулевой аккаунт: фантомные строки + прямое действие вместо демо-проектов
          <div className="relative flex flex-1 flex-col justify-center">
            <GhostProjectRow />
            <div className="my-[30px] h-px w-full bg-[rgba(246,245,253,0.06)]" />
            <GhostProjectRow />
            {/* Затемняющая подложка под сообщением: фантомы не должны просвечивать сквозь текст */}
            {/* Только подпись: кнопка создания уже есть в hero, вторая такая же рядом
                перетягивала внимание на себя и ничего не добавляла. */}
            <div className="absolute inset-[-16px] flex flex-col items-center justify-center gap-space-4 rounded-r15 bg-[rgba(16,9,34,0.72)] text-center backdrop-blur-[2px]">
              <p className="max-w-[260px] text-[18px] leading-[23px] text-text-80">{t('dashboard.emptyProjects')}</p>
            </div>
          </div>
        ) : (
          <>
            {projects.slice(0, 2).map((project, index) => (
              <div key={project.id}>
                {index > 0 && <div className="my-[30px] h-px w-full bg-[rgba(246,245,253,0.1)]" />}
                <ProjectRow project={project} />
              </div>
            ))}
            {/* Один проект в карточке на две строки оставлял половину пустой — она читалась
                как «здесь ничего нет». Добираем фантомом: место занято и видно, что тут будет. */}
            {projects.length === 1 && (
              <div>
                <div className="my-[30px] h-px w-full bg-[rgba(246,245,253,0.1)]" />
                <GhostProjectRow />
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}

/**
 * «Статистика» на home (Figma Wireframe-64 → 63). Три состояния:
 *  - нет TikTok → кнопка «Подключить» (статистика тянется из Display API);
 *  - подключён, данных мало → «Собираем данные» + чипы созданo/опубликовано/осталось (wf64);
 *  - подключён, данных достаточно → инсайт-бары (личные исходники vs средний ролик, wf63).
 */
function StatsCard({ connected, handle, published, created, previewData, analysis }: {
  connected: boolean;
  handle?: string;
  published: number;
  created: number;
  previewData: boolean;
  analysis?: IterationAnalysis | null;
}) {
  const { t } = useTranslation();
  // Готовность решает разбор по измерениям (см. lib/analysis), а не счётчик «10 роликов»:
  // карточка не должна обещать инсайт, которого на странице аналитики ещё нет.
  const leading = leadingDimension(analysis);
  const bars = leading ? leaderBars(t, leading, analysis?.minVideosPerValue ?? 2) : null;
  const enough = previewData || Boolean(bars);
  const left = analysis?.videosNeeded ?? 0;
  return (
    <Link to={connected ? '/app/stats' : '/app/profile'} className="card-2 group relative flex flex-col overflow-hidden p-[40px]">
      <span className="flex items-center gap-[14px] text-text">
        <span className="text-[32px] font-[350] leading-none">{t('dashboard.stats')}</span>
        <FigIcon name="home-arrow.svg" h={16} className="translate-y-[1px] transition-transform duration-200 group-hover:translate-x-[4px]" />
      </span>
      {!connected ? (
        <div className="flex flex-1 items-center justify-center">
          <TiktokButton connected={false} />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col justify-center gap-[20px]">
          <span className="flex items-center gap-[10px] text-[16px] font-[350] leading-none text-text-60">
            <span className="h-[8px] w-[8px] rounded-full bg-accent-light" />
            @{handle}
          </span>
          {enough ? (
            <>
              {leading && (
                <span className="text-[16px] leading-none text-text-80">
                  {t('stats.leadingTitle', { dimension: t(DIMENSION_KEY[leading.dimension]) })}
                </span>
              )}
              <InsightChart
                bars={bars ?? [{ label: t('stats.barWinner'), views: 1120, winner: true }, { label: t('stats.barAverage'), views: 480 }]}
                trend={`${Math.round(leading?.liftPercent ?? 133)}%`}
                unitKey={bars ? 'stats.viewsPerVideo' : 'stats.views'}
              />
            </>
          ) : (
            <div className="rounded-r15 bg-grad-soft-10 p-[24px] text-center">
              <span className="mx-auto flex h-[43px] w-fit items-center gap-[16px] rounded-r10 bg-grad-soft-20 px-[15px] text-[16px] font-[400] leading-none text-text-80">
                <span>{t('stats.chipCreated', { n: created })}</span>
                <span className="text-text-60">|</span>
                <span>{t('stats.chipPublished', { n: published })}</span>
                {left > 0 && (
                  <>
                    <span className="text-text-60">|</span>
                    <span>{t('stats.chipLeft', { n: left })}</span>
                  </>
                )}
              </span>
              <p className="mt-[20px] flex items-center justify-center gap-[10px] text-[20px] font-[400] leading-none text-text">
                {t('stats.collecting')}
                <FigIcon name="icon-bolt.svg" h={16} />
              </p>
              {/* Тизер повторяет формулировку страницы аналитики, чтобы цифры и смысл сходились */}
              <p className="mx-auto mt-[12px] max-w-[400px] text-balance text-[15px] leading-[19px] text-text-60">
                {published === 0
                  ? t('stats.collectingNothingTitle')
                  : left > 0
                    ? t('stats.collectingLeftTitle', { count: left })
                    : t('stats.collectingSameTitle')}
              </p>
            </div>
          )}
        </div>
      )}
    </Link>
  );
}

export function DashboardPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  // dev-превью состояний карточки статистики: ?state=data (инсайт-бары) / ?state=collecting (сбор)
  const previewData = import.meta.env.DEV && params.get('state') === 'data';
  const previewCollecting = import.meta.env.DEV && params.get('state') === 'collecting';
  const projectsQuery = useQuery({ queryKey: ['projects'], queryFn: api.projects });
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: 15_000 });
  // архивные проекты в ленту дашборда не попадают — их специально убрали с глаз
  const projects = (projectsQuery.data?.projects ?? []).filter((project) => !project.archived);
  const connected = Boolean(meQuery.data?.tiktok) || previewData || previewCollecting;
  const homeVideosQuery = useQuery({ queryKey: ['tiktok-videos', 30], queryFn: () => api.tiktokVideos(30), enabled: Boolean(meQuery.data?.tiktok), staleTime: 60_000 });
  const homeVideos = homeVideosQuery.data?.videos ?? [];
  const published = homeVideos.length;
  const created = projects.reduce((sum, project) => sum + (project.generated ?? 0), 0);
  // Тот же разбор, что на /app/stats: карточка — тизер аналитики, и цифры должны совпадать
  const teaserProject = projectsQuery.data?.activeProject ?? projects[0];
  const analysisQuery = useQuery({
    queryKey: ['iterations', teaserProject?.id],
    queryFn: () => api.iterations(teaserProject?.id ?? ''),
    enabled: Boolean(teaserProject?.id) && Boolean(meQuery.data?.tiktok),
    staleTime: 30_000
  });

  /*
   * Что предложить продолжить. Готовые невыложенные ролики важнее брошенного визарда:
   * они уже стоили человеку кредитов, и до результата остался один шаг.
   */
  const wizard = useWizardStore();
  const unpostedProject = projects.find((project) => (project.generated ?? 0) > 0 && project.status === 'COMPLETED');
  const draftProject = projects.find((project) => project.id === wizard.projectId);
  const resume = unpostedProject
    ? { kind: 'post' as const, project: unpostedProject, count: unpostedProject.generated ?? 0 }
    : wizard.projectId && wizard.track && draftProject
      ? { kind: 'wizard' as const, project: draftProject, count: undefined }
      : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-[20px] lg:h-[calc(100dvh_-_2*var(--space-6))] lg:flex-none lg:py-[calc(var(--rail-pad-y)_-_var(--space-6))]">
      {/* «Создать проект» именно создаёт проект: раньше кнопка вела в визард уже существующего,
          а на нулевом аккаунте упиралась в гард «Сначала создай активный проект». */}
      <Hero
        name={meQuery.data?.user.name}
        resume={resume && { kind: resume.kind, projectName: resume.project.name, count: resume.count }}
        onCreate={() => setCreateOpen(true)}
        // «Выложить» вело на страницу батча — человек жал и попадал в список, а не в выкладку.
        // Экран выкладки сам встаёт на первый ещё не опубликованный ролик.
        onResume={() => navigate(resume?.kind === 'post'
          ? `/app/projects/${resume.project.id}/post`
          : `/app/generate?project=${resume?.project.id}`)}
      />
      <CreateProjectModal open={createOpen} onClose={() => setCreateOpen(false)} />

      <section className="grid shrink-0 gap-[20px] lg:h-[379px] lg:grid-cols-2">
        {/* API упал (или сеть пропала и запрос встал на паузу) — вместо вечного скелетона
            показываем причину и кнопку повтора */}
        {queryDown(projectsQuery) ? (
          <QueryError query={projectsQuery} className="lg:col-span-2" />
        ) : projectsQuery.isLoading ? (
          <>
            <Skeleton className="min-h-[300px] lg:min-h-[379px]" />
            <Skeleton className="min-h-[300px] lg:min-h-[379px]" />
          </>
        ) : (
          <>
            <ProjectsCard projects={projects} />
            <StatsCard
              connected={connected}
              handle={meQuery.data?.tiktok?.handle || meQuery.data?.user.artistNick || undefined}
              published={published}
              created={created}
              previewData={previewData}
              analysis={analysisQuery.data?.analysis}
            />
          </>
        )}
      </section>
    </div>
  );
}
