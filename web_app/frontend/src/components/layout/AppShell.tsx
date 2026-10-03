import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { api, ApiError } from '../../lib/api';
import { currentAppPath } from '../../lib/appPath';
import { activeJobOptions } from '../../lib/activeJob';
import { ProfileSetupGate } from './ProfileSetupGate';
import { cn } from '../../lib/cn';
import { Button } from '../ui/Button';
import { Button as KitButton } from '../ui/kit';
import { ErrorBoundary } from '../ui/ErrorBoundary';
import { Skeleton } from '../ui/Skeleton';
import { useToast } from '../../contexts/ToastContext';
import { SvgMaskIcon } from './SvgMaskIcon';
import { LanguageSwitcher } from './LanguageSwitcher';
import { AppAnalytics } from '../analytics/AppAnalytics';
import { FunnelBadge, FunnelHost } from '../funnel/FunnelHost';
import { rememberSessionUser } from '../../stores/session';
import { DraftReplaceDialog } from './DraftReplaceDialog';
import { usePaymentReturn } from '../funnel/useFunnel';

// The desktop screens were laid out for a 1600x900 canvas. Scaling from 1280x800
// left a 1280x720 laptop at 90%, while the same page at browser zoom 80% got the
// intended 1600x900 CSS viewport. Keep that geometry inside the app so users do
// not have to change browser zoom themselves.
const DESKTOP_LAYOUT_WIDTH = 1600;
const DESKTOP_LAYOUT_HEIGHT = 900;
const MIN_DESKTOP_SCALE = 0.64;

function desktopScale(width: number, height: number): number {
  // Tailwind's max-lg rules end below 1024px. At exactly 1024px the desktop
  // shell is still active and uses the same virtual viewport principle.
  if (width < 1024) return 1;
  // Browser zoom increases both virtual dimensions. Use the tighter axis so a
  // short laptop screen gets the same usable 1600x900 canvas as a manual zoom.
  return Math.max(MIN_DESKTOP_SCALE, Math.min(1, width / DESKTOP_LAYOUT_WIDTH, height / DESKTOP_LAYOUT_HEIGHT));
}

function useAppViewport() {
  const [viewport, setViewport] = useState(() => ({ width: window.innerWidth, height: window.innerHeight }));

  useEffect(() => {
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setViewport({ width: window.innerWidth, height: window.innerHeight }));
    };
    window.addEventListener('resize', update);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', update);
    };
  }, []);

  const scale = desktopScale(viewport.width, viewport.height);
  return {
    scale,
    layoutWidth: viewport.width / scale,
    layoutHeight: viewport.height / scale
  };
}

const baseNav = [
  // Оптически один размер: круг и квадрат — 28, лампочка уже (40×44), поэтому выше — 31
  { href: '/app/projects', label: 'nav.projects', icon: '/assets/figma/nav-projects.svg', size: 31 },
  { href: '/app/generate', label: 'nav.generate', icon: '/assets/figma/nav-generate.svg', size: 28 },
  { href: '/app/stats', label: 'nav.stats', icon: '/assets/figma/nav-stats.svg', size: 28 }
];


const AVATAR_CLASS = 'flex h-[60px] w-[60px] items-center justify-center overflow-hidden rounded-full border-2 border-[var(--dash-white)] bg-accent-20 text-[20px] font-bold text-text-80 transition';

/** Сам кружок аватара, без ссылки — для мест, где ссылка уже снаружи (пункт меню в шторке). */
function AvatarFace({ name, avatarUrl }: { name?: string; avatarUrl?: string }) {
  return avatarUrl ? (
    <img src={avatarUrl} alt="" className="h-full w-full rounded-full object-cover p-[2px]" />
  ) : (
    <span className="leading-none">{(name ?? 'B').slice(0, 1).toUpperCase()}</span>
  );
}

function Avatar({ name, avatarUrl, className, onClick }: { name?: string; avatarUrl?: string; className?: string; onClick?: () => void }) {
  const { t } = useTranslation();
  return (
    <NavLink
      to="/app/profile"
      onClick={onClick}
      className={({ isActive }) => cn(AVATAR_CLASS, 'hover:shadow-glow', isActive && 'text-text shadow-glow', className)}
      aria-label={t('nav.profile')}
    >
      <AvatarFace name={name} avatarUrl={avatarUrl} />
    </NavLink>
  );
}

function Sidebar({ activeJobId, userName, avatarUrl }: { activeJobId?: string; userName?: string; avatarUrl?: string }) {
  const { t } = useTranslation();
  return (
    <aside className="sidebar">
      <NavLink to="/app" aria-label={t('nav.dashboard')} className="sidebar-icon !w-[60px]">
        <img src="/assets/figma/logo-star.svg" width="60" height="60" alt="Blast" />
      </NavLink>
      <nav className="sidebar-nav mt-[clamp(48px,calc(var(--app-layout-h,100vh)*.1),107px)] flex flex-col items-center gap-[20px]">
        {baseNav.map((item) => (
          <NavLink
            key={item.href}
            to={item.href === '/app/generate' && activeJobId ? `/app/processing/${activeJobId}` : item.href}
            aria-label={t(item.label)}
            className={({ isActive }) => cn(
              'sidebar-icon relative text-text-60 hover:text-text-80',
              isActive && 'sidebar-icon-active text-text'
            )}
          >
            {/*
              Идёт генерация — пульсирует САМА иконка визарда. Раньше рядом висела отдельная
              мигающая точка: лишняя сущность, которая читалась как «уведомление/ошибка» и
              липла к краю иконки. Пульс на иконке говорит ровно то же — «здесь что-то идёт».
            */}
            <SvgMaskIcon
              src={item.icon}
              style={{
                width: item.size ?? 34,
                height: item.size ?? 34,
                ...(item.href === '/app/generate' && activeJobId
                  ? { animation: 'navBusyPulse 1.4s ease-in-out infinite' }
                  : null)
              }}
            />
          </NavLink>
        ))}
      </nav>
      <div className="flex-1" />
      <LanguageSwitcher className="mb-space-4" />
      <Avatar name={userName} avatarUrl={avatarUrl} />
    </aside>
  );
}

function MobileHeader({ onOpen, userName, avatarUrl }: { onOpen: () => void; userName?: string; avatarUrl?: string }) {
  const { t } = useTranslation();
  return (
    /* Шапка не липнет: скроллится вместе со страницей, чтобы не съедать экран. Компактная —
       лого 24, «Blast» 15px (+1px вниз: у Point кап-высота сидит выше центра), бургер 36. */
    <header className="flex items-center justify-between rounded-r15 border border-border bg-nav px-[20px] py-[12px] md:hidden">
      <NavLink to="/app" className="flex items-center gap-[8px]">
        <img src="/assets/figma/logo-star.svg" width="24" height="24" alt="Blast" />
        <span className="text-[15px] font-bold leading-none">Blast</span>
      </NavLink>
      <span className="flex items-center gap-[8px]">
        {/* личный кабинет: на десктопе это аватар в сайдбаре, на телефоне — тот же аватар у бургера */}
        <Avatar name={userName} avatarUrl={avatarUrl} className="!h-[36px] !w-[36px] !text-[14px]" />
        <button type="button" onClick={onOpen} aria-label={t('nav.openMenu')} className="flex h-[36px] w-[36px] items-center justify-center rounded-r10 bg-grad-soft-20 text-[16px] text-text-80">☰</button>
      </span>
    </header>
  );
}

function Drawer({ open, onClose, activeJobId, userName, avatarUrl }: { open: boolean; onClose: () => void; activeJobId?: string; userName?: string; avatarUrl?: string }) {
  const { t } = useTranslation();
  const panelRef = useRef<HTMLElement>(null);
  // Esc закрывает шторку, фокус — на её крестик (иначе клавиатура осталась бы под подложкой)
  useEffect(() => {
    if (!open) return undefined;
    panelRef.current?.querySelector<HTMLElement>('[data-drawer-close]')?.focus({ preventScroll: true });
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <>
      <div className="fixed inset-0 z-overlay bg-[rgba(5,1,15,.72)] md:hidden" onClick={onClose} />
      <aside ref={panelRef} className="fixed left-0 top-0 z-drawer h-dvh w-[280px] border-r border-border bg-nav p-space-5 shadow-soft md:hidden">
        <div className="mb-space-7 flex items-center justify-between">
          <img src="/assets/figma/logo-star.svg" width="40" height="40" alt="Blast" />
          <Button data-drawer-close variant="ghost" size="sm" onClick={onClose} aria-label={t('common.closeMenu')}><span aria-hidden="true">×</span></Button>
        </div>
        <nav className="flex flex-col gap-space-3">
          {/* админ-аналитика — десктопный инструмент, в мобильном меню её нет */}
          {baseNav.map((item) => (
            <NavLink
              key={item.href}
              to={item.href === '/app/generate' && activeJobId ? `/app/processing/${activeJobId}` : item.href}
              onClick={onClose}
              className={({ isActive }) => cn('flex items-center gap-space-3 rounded-r12 border border-border p-space-4 text-text-60', isActive && 'border-accent-light bg-accent-20 text-text')}
            >
              <SvgMaskIcon src={item.icon} />
              {t(item.label)}
            </NavLink>
          ))}
        </nav>
        <NavLink
          to="/app/profile"
          onClick={onClose}
          className={({ isActive }) => cn('mt-space-3 flex items-center gap-space-3 rounded-r12 border border-border p-space-4 text-text-60', isActive && 'border-accent-light bg-accent-20 text-text')}
        >
          {/* кружок без своей ссылки: пункт уже NavLink, а <a> в <a> — невалидная разметка */}
          <span className={cn(AVATAR_CLASS, '!h-[28px] !w-[28px] !border !text-[12px]')} aria-hidden="true">
            <AvatarFace name={userName} avatarUrl={avatarUrl} />
          </span>
          {t('nav.profile')}
        </NavLink>
        <LanguageSwitcher className="mt-space-5 w-max" />
      </aside>
    </>
  );
}

/*
 * «Ролики готовы» / «Генерация не удалась». /api/jobs/active отдаёт только PENDING/PROCESSING,
 * поэтому COMPLETED там не увидеть никогда — раньше тост не всплывал вовсе. Ловим переход:
 * активный джоб пропал (или сменился) → дочитываем его по id и говорим, чем кончилось.
 */
function useJobFinishedToast(currentJobId: string | null | undefined) {
  const { t } = useTranslation();
  const { push } = useToast();
  const queryClient = useQueryClient();
  const location = useLocation();
  const pathRef = useRef(location.pathname);
  pathRef.current = location.pathname;
  const trackedJob = useRef<string | null>(null);
  // ref, а не state: под StrictMode эффект прогоняется дважды в одном коммите — тост задваивался
  const notifiedJob = useRef<string | null>(null);

  useEffect(() => {
    if (currentJobId === undefined) return; // ответа ещё нет / запрос упал — перехода не знаем
    const previous = trackedJob.current;
    trackedJob.current = currentJobId;
    if (!previous || previous === currentJobId || notifiedJob.current === previous) return;
    notifiedJob.current = previous;
    api.job(previous).then(({ job }) => {
      if (job.status !== 'COMPLETED' && job.status !== 'FAILED') return;
      // батч закончился: счётчики, список проектов и сам проект устарели
      void queryClient.invalidateQueries({ queryKey: ['projects'] });
      void queryClient.invalidateQueries({ queryKey: ['me'] });
      void queryClient.invalidateQueries({ queryKey: ['project'] });
      void queryClient.invalidateQueries({ queryKey: ['job', job.id] });
      // экран генерации этого батча сам покажет исход (или уведёт в проект) — тост там лишний
      if (pathRef.current === `/app/processing/${job.id}`) return;
      // как на экране генерации: упавший ролик = неудача, даже если джоб формально завершён
      const failed = job.status === 'FAILED' || job.videos.some((video) => video.status === 'FAILED');
      push(failed
        ? { variant: 'error', title: t('processing.failed'), action: { label: t('processing.toastDetails'), href: `/app/processing/${job.id}` } }
        : { variant: 'success', title: t('processing.toastReady'), action: { label: t('common.view'), href: job.projectId ? `/app/projects/${job.projectId}` : `/app/processing/${job.id}` } });
    }, (error: unknown) => {
      // статус не дочитали — молча не выдумываем исход, но и не теряем след в консоли
      console.warn(`job ${previous} left the active slot, final status unavailable`, error);
    });
  }, [currentJobId, push, queryClient, t]);
}

export function AppShell() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const closeDrawer = useCallback(() => setDrawerOpen(false), []);
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: 15_000 });
  // чей черновик в браузере — ссылка из бота сверит с вошедшим аккаунтом (HandoffPage)
  const meId = meQuery.data?.user.id;
  useEffect(() => { if (meId) rememberSessionUser(meId); }, [meId]);
  // идёт генерация — следим часто; нет — раз в 30 с (раньше каждые 5 с на любой странице)
  const activeJobQuery = useQuery(activeJobOptions);
  const activeJob = activeJobQuery.data?.job;
  useJobFinishedToast(activeJobQuery.isSuccess ? (activeJob?.id ?? null) : undefined);
  const viewport = useAppViewport();
  // возврат из банка после трипваера: на любую страницу /app (батч, визард, генерация)
  usePaymentReturn();

  useLayoutEffect(() => {
    const root = document.documentElement;
    root.style.zoom = String(viewport.scale);
    root.style.setProperty('--app-layout-w', `${viewport.layoutWidth}px`);
    root.style.setProperty('--app-layout-h', `${viewport.layoutHeight}px`);
    return () => {
      root.style.zoom = '';
      root.style.removeProperty('--app-layout-w');
      root.style.removeProperty('--app-layout-h');
    };
  }, [viewport.layoutHeight, viewport.layoutWidth, viewport.scale]);

  const userName = meQuery.data?.user.name;
  const frameStyle = {
    width: `${viewport.layoutWidth}px`,
    height: `${viewport.layoutHeight}px`,
    '--app-layout-w': `${viewport.layoutWidth}px`,
    '--app-layout-h': `${viewport.layoutHeight}px`,
    '--app-page-h': `${viewport.layoutHeight - 2 * 32}px`
  } as React.CSSProperties;

  return (
    <>
      <AppAnalytics />
      <div className="app-scale-viewport">
        <div className="app-frame" style={frameStyle}>
          {/* Тот же выбор аватара, что в ЛК: свой, иначе из TikTok — сайдбар отставал и показывал букву */}
          <Sidebar activeJobId={activeJob?.id} userName={userName} avatarUrl={meQuery.data?.user.avatarUrl || meQuery.data?.tiktok?.avatarUrl || undefined} />
        <Drawer open={drawerOpen} onClose={closeDrawer} activeJobId={activeJob?.id} userName={userName} avatarUrl={meQuery.data?.user.avatarUrl || meQuery.data?.tiktok?.avatarUrl || undefined} />
        {/* вход через Telegram не спрашивает ФИО — добираем их до первого экрана */}
        <ProfileSetupGate open={meQuery.isSuccess && meQuery.data.user.profileComplete === false} />
        {/* модалки воронки после генерации: квиз и безлимит на трек */}
        {meQuery.isSuccess && <FunnelHost />}
        {/* модалку безлимита закрыли, не пройдя: плашка в углу открывает её снова */}
        {meQuery.isSuccess && <FunnelBadge />}
        {/* «Заменить текущую настройку?» перед подменой черновика визарда */}
        <DraftReplaceDialog />
        <main className="with-sidebar min-w-0 flex-1">
          <div className="app-content">
            <MobileHeader onOpen={() => setDrawerOpen(true)} userName={userName} avatarUrl={meQuery.data?.user.avatarUrl || meQuery.data?.tiktok?.avatarUrl || undefined} />
            {meQuery.isLoading ? (
              <Skeleton className="h-[120px]" />
            ) : meQuery.error ? (
              /* аккаунт не загрузился: повторить или войти заново (с возвратом на эту страницу) */
              <div className="card-2 flex min-h-[260px] flex-col items-center justify-center px-[28px] py-[40px] text-center" role="alert">
                <h1 className="text-ui-24 font-[400] text-text">{t('error.meTitle')}</h1>
                <p className="mt-[12px] max-w-[420px] text-ui-16 text-text-60">{t('error.meText')}</p>
                {meQuery.error instanceof ApiError && (
                  <p className="mt-[8px] text-ui-12 text-text-40">{t('error.code', { code: meQuery.error.status })}</p>
                )}
                <div className="mt-[24px] flex flex-wrap justify-center gap-[12px]">
                  <KitButton variant="primary" size="md" loading={meQuery.isFetching} onClick={() => void meQuery.refetch()}>{t('error.retry')}</KitButton>
                  <KitButton variant="secondary" size="md" onClick={() => {
                    const back = currentAppPath();
                    navigate(back ? `/login?next=${encodeURIComponent(back)}` : '/login');
                  }}>{t('error.relogin')}</KitButton>
                </div>
              </div>
            ) : (
              <ErrorBoundary>
                <Outlet />
              </ErrorBoundary>
            )}
          </div>
        </main>
        </div>
      </div>
    </>
  );
}
