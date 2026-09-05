import { useEffect, useRef, useState } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { api } from '../../lib/api';
import { ProfileSetupGate } from './ProfileSetupGate';
import { cn } from '../../lib/cn';
import { Button } from '../ui/Button';
import { ErrorBoundary } from '../ui/ErrorBoundary';
import { Skeleton } from '../ui/Skeleton';
import { useToast } from '../../contexts/ToastContext';
import { SvgMaskIcon } from './SvgMaskIcon';
import { LanguageSwitcher } from './LanguageSwitcher';

const nav = [
  { href: '/app/projects', label: 'nav.projects', icon: '/assets/figma/nav-projects.svg', size: 37 },
  { href: '/app/generate', label: 'nav.generate', icon: '/assets/figma/nav-generate.svg', size: 34 },
  { href: '/app/stats', label: 'nav.stats', icon: '/assets/figma/nav-stats.svg', size: 34, locked: true }
];

function Avatar({ name, avatarUrl }: { name?: string; avatarUrl?: string }) {
  const { t } = useTranslation();
  return (
    <NavLink
      to="/app/profile"
      className={({ isActive }) =>
        cn(
          'flex h-[60px] w-[60px] items-center justify-center overflow-hidden rounded-full border-2 border-[var(--dash-white)] bg-accent-20 text-[20px] font-bold text-text-80 transition hover:shadow-glow',
          isActive && 'text-text shadow-glow'
        )
      }
      aria-label={t('nav.profile')}
    >
      {avatarUrl ? (
        <img src={avatarUrl} alt="" className="h-full w-full rounded-full object-cover p-[2px]" />
      ) : (
        (name ?? 'B').slice(0, 1).toUpperCase()
      )}
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
      <nav className="mt-[clamp(48px,10vh,107px)] flex flex-col items-center gap-space-7">
        {nav.map((item) => (
          <NavLink
            key={item.href}
            to={item.href === '/app/generate' && activeJobId ? `/app/processing/${activeJobId}` : item.href}
            aria-label={t(item.label)}
            className={({ isActive }) => cn(
              'sidebar-icon relative text-text-60 hover:text-text-80',
              isActive && 'sidebar-icon-active text-text',
              // locked гасит иконку, но НЕ когда раздел выбран — иначе активная выглядит неактивной
              item.locked && !isActive && 'opacity-40'
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

function MobileHeader({ onOpen }: { onOpen: () => void }) {
  const { t } = useTranslation();
  return (
    <header className="sticky top-0 z-sticky mb-space-4 flex items-center justify-between rounded-r20 border border-border bg-nav p-space-4 backdrop-blur md:hidden">
      <NavLink to="/app" className="flex items-center gap-space-3">
        <img src="/assets/figma/logo-star.svg" width="32" height="32" alt="Blast" />
        <span className="font-bold">Blast</span>
      </NavLink>
      <Button variant="ghost" size="sm" onClick={onOpen} aria-label={t('nav.openMenu')}>☰</Button>
    </header>
  );
}

function Drawer({ open, onClose, activeJobId }: { open: boolean; onClose: () => void; activeJobId?: string }) {
  const { t } = useTranslation();
  if (!open) return null;
  return (
    <>
      <div className="fixed inset-0 z-overlay bg-[rgba(5,1,15,.72)] md:hidden" onClick={onClose} />
      <aside className="fixed left-0 top-0 z-drawer h-dvh w-[280px] border-r border-border bg-nav p-space-5 shadow-soft md:hidden">
        <div className="mb-space-7 flex items-center justify-between">
          <img src="/assets/figma/logo-star.svg" width="40" height="40" alt="Blast" />
          <Button variant="ghost" size="sm" onClick={onClose}>×</Button>
        </div>
        <nav className="flex flex-col gap-space-3">
          {nav.map((item) => (
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
        <LanguageSwitcher className="mt-space-5 w-max" />
      </aside>
    </>
  );
}

export function AppShell() {
  const { push } = useToast();
  const navigate = useNavigate();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: 15_000 });
  const activeJobQuery = useQuery({ queryKey: ['active-job'], queryFn: api.activeJob, refetchInterval: 5000 });
  const activeJob = activeJobQuery.data?.job;
  const [lastCompletedJob, setLastCompletedJob] = useState<string | null>(null);
  const notifiedJob = useRef<string | null>(null);

  useEffect(() => {
    if (!activeJobQuery.data || activeJob) return;
    if (lastCompletedJob) return;
  }, [activeJob, activeJobQuery.data, lastCompletedJob]);

  useEffect(() => {
    const job = activeJobQuery.data?.job;
    if (!job || job.status !== 'COMPLETED') return;
    // ref, а не state: под StrictMode эффект прогоняется дважды в одном коммите, и
    // setLastCompletedJob не успевает применится — уведомление задваивалось.
    if (notifiedJob.current === job.id) return;
    notifiedJob.current = job.id;
    setLastCompletedJob(job.id);
    push({ variant: 'success', title: 'Ролики готовы', action: { label: 'Открыть', href: `/app/processing/${job.id}` } });
  }, [activeJobQuery.data?.job, push]);

  const userName = meQuery.data?.user.name;

  return (
    <div className="app-frame">
      <Sidebar activeJobId={activeJob?.id} userName={userName} avatarUrl={meQuery.data?.user.avatarUrl ?? undefined} />
      <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} activeJobId={activeJob?.id} />
      {/* вход через Telegram не спрашивает ФИО — добираем их до первого экрана */}
      <ProfileSetupGate open={meQuery.isSuccess && meQuery.data.user.profileComplete === false} />
      <main className="with-sidebar min-w-0 flex-1">
        <div className="app-content">
          <MobileHeader onOpen={() => setDrawerOpen(true)} />
          {meQuery.isLoading ? (
            <Skeleton className="h-[120px]" />
          ) : meQuery.error ? (
            <div className="card flex items-center justify-between gap-space-4">
              <div>
                <h1 className="text-[28px] font-bold">API недоступен</h1>
                <p className="mt-space-2 text-text-60">Проверь, что FastAPI запущен на 8000 порту.</p>
              </div>
              <Button onClick={() => navigate('/login')}>К логину</Button>
            </div>
          ) : (
            <ErrorBoundary>
              <Outlet />
            </ErrorBoundary>
          )}
        </div>
      </main>
    </div>
  );
}
