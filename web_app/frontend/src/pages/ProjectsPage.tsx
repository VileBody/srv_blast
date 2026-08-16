import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { cn } from '../lib/cn';
import type { Project } from '../lib/types';
import { Skeleton } from '../components/ui/Skeleton';
import { Modal } from '../components/ui/Modal';
import { QueryError, queryDown } from '../components/ui/ErrorState';
import { CreateProjectModal } from '../components/project/CreateProjectModal';
import { FigIcon } from '../components/ui/FigIcon';
import { useToast } from '../contexts/ToastContext';

/** Светлый градиент-заливка для текста (bg-clip-text), как в макетах W35/W37. */
const gradLight = {
  backgroundImage: 'linear-gradient(184deg, #f6f5fd 8%, rgba(246,245,253,.8) 95%)',
  WebkitBackgroundClip: 'text',
  backgroundClip: 'text'
} as const;

/** Мини-карта статистики текущего проекта (299×192, r15) с частицами-свирлом (Figma 712:1682/1982).
    `value` может быть словом («Без лимита») — тогда кегль меньше, иначе фигмовские 104 не влезают. */
function StatCard({ label, value, to, variant }: { label: string; value: number | string; to: string; variant: 'primary' | 'muted' }) {
  const muted = variant === 'muted';
  const wordy = typeof value === 'string' && !/^\d+$/.test(value);
  // цвет фейда = фон карты, чтобы частицы жёстко «уходили» в него слева (эффект глубины).
  // Тянем до 80% ширины — иначе не достаёт до свирла muted-карты (он правее).
  const fade = muted
    ? 'linear-gradient(90deg, #2a1e49 0%, #2a1e49 55%, rgba(42,30,73,0) 80%)'
    : 'linear-gradient(90deg, #241a3c 0%, #241a3c 55%, rgba(36,26,60,0) 80%)';
  return (
    <Link
      to={to}
      className="group relative flex h-[192px] w-[299px] shrink-0 flex-col overflow-hidden rounded-[15px]"
      style={muted ? { background: '#2a1e49' } : { background: 'var(--grad-soft-20)' }}
    >
      {/* мягкое свечение (две размытые эллипс-частицы) + свирл — точные позиции/наклоны из Figma */}
      <img src="/assets/figma/proj-particle-a.svg" alt="" aria-hidden className="pointer-events-none absolute left-[-126px] top-[-127px] h-[454px] w-[372px] max-w-none rotate-[-18.32deg] select-none" />
      <img src="/assets/figma/proj-particle-b.svg" alt="" aria-hidden className={`pointer-events-none absolute h-[454px] w-[372px] max-w-none rotate-[-18.32deg] select-none ${muted ? 'left-[-89px] top-[15px]' : 'left-[-139px] top-[-135px]'}`} />
      {muted ? (
        <img src="/assets/figma/proj-swirl-2.svg" alt="" aria-hidden className="pointer-events-none absolute left-[148px] top-[62px] h-[184px] w-[184px] max-w-none rotate-[-12.58deg] select-none" />
      ) : (
        <img src="/assets/figma/proj-swirl-1.svg" alt="" aria-hidden className="pointer-events-none absolute left-[98px] top-[76px] h-[139px] w-[174px] max-w-none rotate-[-30deg] select-none" />
      )}
      {/* фейд поверх частиц (под текстом): частицы частично уходят в фон слева → глубина */}
      <div className="pointer-events-none absolute inset-y-0 left-0 right-0" style={{ background: fade }} />
      {/* заголовок со стрелкой сразу после текста (Figma: стрелка привязана к тексту, не к углу) */}
      <div className="relative flex items-center gap-[12px] px-[28px] pt-[28px]">
        <span className="text-[24px] font-[350] leading-none text-transparent" style={gradLight}>{label}</span>
        <FigIcon name="home-arrow.svg" h={16} className="transition-transform duration-200 group-hover:translate-x-[3px]" />
      </div>
      <span className={cn('relative mt-[24px] px-[28px] font-[350] leading-none text-transparent', wordy ? 'text-[44px]' : 'text-[104px]')} style={gradLight}>{value}</span>
    </Link>
  );
}

/**
 * Карта проекта в ленте «Все проекты» (263×358, r15): обложка + название + стрелка (Figma 712:1932).
 * Правка: «⋯» в углу — переименовать / архив / удалить. До этого ошибку в названии нельзя было
 * исправить, а лента со временем зарастала мусорными проектами.
 */
function ProjectCard({ project, menuOpen, onToggleMenu, onRename, onArchive, onDelete }: {
  project: Project;
  menuOpen: boolean;
  onToggleMenu: () => void;
  onRename: () => void;
  onArchive: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  const action = (handler: () => void) => (event: React.MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    handler();
  };
  return (
    <Link
      to={`/app/projects/${project.id}`}
      className={cn('group relative flex max-h-[358px] w-[263px] shrink-0 flex-col overflow-hidden rounded-[15px]', project.archived && 'opacity-60')}
      style={{ background: 'var(--grad-soft-20)' }}
    >
      {/* обложка: отступ 14 слева/сверху, уходит за правый край (bleed 38px) и перекрыта фейдом в #281e47 */}
      <div className="relative ml-[14px] mr-[-38px] mt-[14px] min-h-0 flex-1">
        <img src={project.coverUrl ?? '/assets/cover-placeholder.svg'} alt="" className="h-full w-full rounded-[14px] object-cover" />
        <div className="pointer-events-none absolute inset-y-0 right-[38px] w-[100px]" style={{ background: 'linear-gradient(90deg, rgba(40,30,71,0) 0%, #281e47 100%)' }} />
      </div>

      <button
        type="button"
        onClick={action(onToggleMenu)}
        aria-label={t('projects.manage')}
        aria-expanded={menuOpen}
        className={cn(
          // отступ 24, а не 14: на 14 кнопка налезала на обложку
          'absolute right-[24px] top-[24px] z-[2] flex h-[32px] w-[32px] items-center justify-center rounded-r10 bg-[rgba(5,1,15,0.62)] text-[18px] leading-none text-text-80 backdrop-blur-[12px] transition',
          menuOpen ? 'opacity-100 text-text' : 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100'
        )}
      >
        ⋯
      </button>
      {menuOpen && (
        <div className="absolute right-[24px] top-[62px] z-[3] w-[196px] overflow-hidden rounded-r10 bg-[#2b2145] py-[6px] shadow-soft">
          {[
            { label: t('projects.rename'), run: onRename },
            { label: project.archived ? t('projects.unarchive') : t('projects.archive'), run: onArchive },
            { label: t('projects.delete'), run: onDelete, danger: true }
          ].map((item) => (
            <button
              key={item.label}
              type="button"
              onClick={action(item.run)}
              className={cn('block w-full px-[14px] py-[9px] text-left text-[15px] leading-none transition hover:bg-accent-10', item.danger ? 'text-[#ff8f9a]' : 'text-text-80 hover:text-text')}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}

      <div className="mx-[14px] mb-[14px] mt-[14px] flex translate-y-[2px] items-center gap-[10px]">
        <span className="truncate text-[24px] font-[350] leading-none text-transparent" style={gradLight}>{project.name}</span>
        {project.archived && <span className="shrink-0 whitespace-nowrap rounded-[5px] bg-grad-soft-20 px-[8px] py-[4px] text-[12px] leading-none text-text-60">{t('projects.archivedBadge')}</span>}
        <FigIcon name="home-arrow.svg" h={12} className="shrink-0 translate-y-[1px] transition-transform duration-200 group-hover:translate-x-[3px]" />
      </div>
    </Link>
  );
}

/** Карточка «Новый проект» в конце ленты — ghost-тайл во всю высоту ряда (как карты проектов):
    пунктирная accent-обводка, «+» в круге и подпись по центру. Один размер с картами → не «едет». */
function AddProjectButton({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={t('projects.createProject')}
      className="group flex max-h-[358px] w-[263px] shrink-0 flex-col items-center justify-center gap-[16px] rounded-[15px] border-2 border-dashed border-[rgba(139,111,230,0.35)] bg-grad-soft-10 transition hover:border-accent-light hover:bg-grad-soft-20"
    >
      {/* «+» рисуем вектором: текстовый глиф центрируется по своим метрикам и визуально
          сидел выше середины круга */}
      <span className="flex h-[56px] w-[56px] items-center justify-center rounded-full bg-grad-soft-20 text-text-80 transition group-hover:text-text">
        <svg viewBox="0 0 24 24" width="24" height="24" fill="none" aria-hidden="true">
          <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      </span>
      <span className="text-[18px] font-[400] leading-none text-text-80 transition group-hover:text-text">{t('projects.createProject')}</span>
    </button>
  );
}

/** Верхняя карта «Текущий проект» (1192×379): заголовок + 2 мини-статы + линии (Figma 712:1636). */
function CurrentProjectCard({ active, onCreate }: { active?: Project | null; onCreate: () => void }) {
  const { t } = useTranslation();
  const generated = active?.generated ?? 0;
  // total === null/undefined → безлимит (TikTok подключён). Значок ∞ на 104-м кегле никто
  // не читал как «без лимита» — пишем словами.
  const unlimited = active != null && active.total == null;
  const remaining: number | string = unlimited ? t('limits.noLimit') : Math.max(0, (active?.total ?? 0) - generated);
  return (
    <section className="card-2 relative shrink-0 overflow-hidden p-[40px] md:h-[379px]">
      <img src="/assets/figma/proj-lines.svg" alt="" aria-hidden className="pointer-events-none absolute right-[-378px] top-[-20px] h-[509px] w-[835px] max-w-none rotate-[-16.44deg] select-none" />
      <div className="relative">
        <h1 className="text-[32px] font-[400] leading-none text-transparent" style={gradLight}>{active?.name ?? t('projects.noActive')}</h1>
        <div className="mt-[16px] flex items-center gap-[10px]">
          <img src="/assets/figma/home-note.svg" width="12" height="17" alt="" aria-hidden />
          <span className="text-[24px] font-[350] leading-none text-transparent" style={gradLight}>{active ? t('projects.currentProject') : t('projects.createFirst')}</span>
        </div>
      </div>
      {active ? (
        <div className="relative mt-[28px] flex gap-space-5">
          <StatCard label={t('projects.currentVideos')} value={generated} to={`/app/projects/${active.id}`} variant="primary" />
          <StatCard label={t('projects.makeMore')} value={remaining} to={`/app/projects/${active.id}`} variant="muted" />
        </div>
      ) : (
        <button type="button" onClick={onCreate} className="soft-btn relative mt-[28px] h-[60px] px-space-6 text-[20px] font-[400]">{t('projects.createProject')}</button>
      )}
    </section>
  );
}

export function ProjectsPage() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  // ?new=1 — сюда переадресует визард, когда генерировать ещё некуда: человек попадает
  // сразу в создание проекта, а не на очередной пустой экран
  const [modalOpen, setModalOpen] = useState(params.get('new') === '1');
  const queryClient = useQueryClient();
  const { push } = useToast();
  const query = useQuery({ queryKey: ['projects'], queryFn: api.projects });
  const active = query.data?.activeProject;
  const all = query.data?.projects ?? [];

  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [renameFor, setRenameFor] = useState<Project | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [deleteFor, setDeleteFor] = useState<Project | null>(null);
  const [showArchived, setShowArchived] = useState(false);

  const archivedCount = all.filter((project) => project.archived).length;
  const projects = showArchived ? all : all.filter((project) => !project.archived);

  const refresh = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['projects'] }),
    queryClient.invalidateQueries({ queryKey: ['me'] })
  ]);

  const renameMutation = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => api.updateProject(id, { name }),
    onSuccess: async () => { await refresh(); setRenameFor(null); push({ variant: 'success', title: t('projects.renamed') }); },
    onError: () => push({ variant: 'error', title: t('simple.error') })
  });

  const archiveMutation = useMutation({
    mutationFn: ({ id, archived }: { id: string; archived: boolean }) => api.updateProject(id, { archived }),
    onSuccess: async (_data, variables) => {
      await refresh();
      push({ variant: 'success', title: t(variables.archived ? 'projects.archived' : 'projects.unarchived') });
    },
    onError: () => push({ variant: 'error', title: t('simple.error') })
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteProject(id),
    onSuccess: async () => { await refresh(); setDeleteFor(null); push({ variant: 'success', title: t('projects.deleted') }); },
    onError: () => push({ variant: 'error', title: t('simple.error') })
  });

  // клик мимо карточки закрывает меню — иначе оно висит поверх соседних проектов
  useEffect(() => {
    if (!menuFor) return;
    const close = () => setMenuFor(null);
    window.addEventListener('click', close);
    return () => window.removeEventListener('click', close);
  }, [menuFor]);

  if (queryDown(query)) return <QueryError query={query} className="min-h-[560px]" />;
  if (query.isLoading) return <Skeleton className="h-full min-h-[560px]" />;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-[20px] md:h-[calc(100dvh_-_2*var(--space-6))] md:flex-none md:py-[calc(var(--rail-pad-y)_-_var(--space-6))]">
      <CurrentProjectCard active={active} onCreate={() => setModalOpen(true)} />

      <section className="card-2 relative flex min-h-0 flex-col overflow-hidden p-[40px] md:flex-1">
        <div className="flex items-center justify-between gap-space-4">
          <h2 className="text-[32px] font-[400] leading-none text-transparent" style={gradLight}>{t('projects.allProjects')}</h2>
          {archivedCount > 0 && (
            <button
              type="button"
              onClick={() => setShowArchived((value) => !value)}
              className="shrink-0 whitespace-nowrap rounded-r10 border border-[rgba(246,245,253,0.2)] px-[14px] py-[8px] text-[15px] leading-none text-text-60 transition hover:border-accent-light hover:text-text"
            >
              {showArchived ? t('projects.hideArchived') : t('projects.showArchived', { count: archivedCount })}
            </button>
          )}
        </div>
        <div className="no-scrollbar mt-[28px] flex min-h-0 flex-1 items-stretch gap-space-5 overflow-x-auto">
          {projects.map((project) => (
            <ProjectCard
              key={project.id}
              project={project}
              menuOpen={menuFor === project.id}
              onToggleMenu={() => setMenuFor((current) => (current === project.id ? null : project.id))}
              onRename={() => { setMenuFor(null); setRenameValue(project.name); setRenameFor(project); }}
              onArchive={() => { setMenuFor(null); archiveMutation.mutate({ id: project.id, archived: !project.archived }); }}
              onDelete={() => { setMenuFor(null); setDeleteFor(project); }}
            />
          ))}
          <AddProjectButton onClick={() => setModalOpen(true)} />
        </div>
      </section>

      <CreateProjectModal
        open={modalOpen}
        onClose={() => {
          setModalOpen(false);
          // чтобы возврат назад/перезагрузка не открывали модалку снова
          if (params.get('new')) {
            params.delete('new');
            setParams(params, { replace: true });
          }
        }}
      />

      <Modal open={Boolean(renameFor)} title={t('projects.rename')} onClose={() => setRenameFor(null)}>
        <label className="block text-[16px] leading-none text-text-60" htmlFor="project-rename">{t('projects.renameTitle')}</label>
        <input
          id="project-rename"
          autoFocus
          value={renameValue}
          maxLength={120}
          onChange={(event) => setRenameValue(event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter' && renameValue.trim() && renameFor) renameMutation.mutate({ id: renameFor.id, name: renameValue.trim() }); }}
          className="mt-[14px] h-[56px] w-full rounded-r10 bg-grad-soft-10 px-[18px] text-[18px] text-text outline-none transition focus:shadow-[inset_0_0_0_1px_var(--accent-light)]"
        />
        <div className="mt-[24px] flex justify-end gap-[12px]">
          <button type="button" onClick={() => setRenameFor(null)} className="h-[48px] rounded-r10 px-[20px] text-[16px] text-text-60 transition hover:text-text">{t('common.cancel')}</button>
          <button
            type="button"
            disabled={!renameValue.trim() || renameMutation.isPending}
            onClick={() => renameFor && renameMutation.mutate({ id: renameFor.id, name: renameValue.trim() })}
            className="h-[48px] rounded-r10 border border-accent-light bg-grad-soft-20 px-[20px] text-[16px] text-text-80 transition hover:text-text disabled:cursor-not-allowed disabled:opacity-50"
          >
            {t('projects.rename')}
          </button>
        </div>
      </Modal>

      <Modal open={Boolean(deleteFor)} title={t('projects.delete')} onClose={() => setDeleteFor(null)}>
        <p className="text-[18px] leading-[24px] text-text-80">{t('projects.deleteConfirm', { name: deleteFor?.name ?? '' })}</p>
        <p className="mt-[12px] text-[15px] leading-[20px] text-text-60">{t('projects.archiveNote')}</p>
        <div className="mt-[24px] flex justify-end gap-[12px]">
          <button type="button" onClick={() => setDeleteFor(null)} className="h-[48px] rounded-r10 px-[20px] text-[16px] text-text-60 transition hover:text-text">{t('common.cancel')}</button>
          <button
            type="button"
            disabled={deleteMutation.isPending}
            onClick={() => deleteFor && deleteMutation.mutate(deleteFor.id)}
            className="h-[48px] rounded-r10 border border-[#ff8f9a] px-[20px] text-[16px] text-[#ff8f9a] transition hover:bg-[rgba(255,143,154,0.12)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {t('projects.deleteYes')}
          </button>
        </div>
      </Modal>
    </div>
  );
}
