import { lazy, Suspense, type ComponentType, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from '../components/layout/AppShell';
import { AuthPage } from '../pages/AuthPage';
import { BlockedPage } from '../pages/BlockedPage';
import { SimplePage } from '../pages/SimplePage';
import { importWithReload } from '../lib/chunkReload';

/*
 * Страницы грузятся по требованию — каждая своим куском JS. Раньше весь сайт был одним
 * файлом (~920 КБ, 283 КБ в gzip): его качали целиком даже ради экрана входа. Вход, 404 и
 * блокировка — в основном файле: маленькие и нужны сразу.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const page = <K extends string>(load: () => Promise<Record<K, ComponentType<any>>>, name: K) =>
  lazy(() => importWithReload(load).then((m) => ({ default: m[name] })));

const MobileUploadPage = page(() => import('../pages/MobileUploadPage'), 'MobileUploadPage');
const AdminAnalyticsPage = page(() => import('../pages/AdminAnalyticsPage'), 'AdminAnalyticsPage');
const DashboardPage = page(() => import('../pages/DashboardPage'), 'DashboardPage');
const LegalPage = page(() => import('../pages/LegalPage'), 'LegalPage');
const PricingPage = page(() => import('../pages/PricingPage'), 'PricingPage');
const ProcessingPage = page(() => import('../pages/ProcessingPage'), 'ProcessingPage');
const ProfilePage = page(() => import('../pages/ProfilePage'), 'ProfilePage');
const TikTokPostPage = page(() => import('../pages/TikTokPostPage'), 'TikTokPostPage');
const ProjectDetailPage = page(() => import('../pages/ProjectDetailPage'), 'ProjectDetailPage');
const ProjectsPage = page(() => import('../pages/ProjectsPage'), 'ProjectsPage');
const StatsPage = page(() => import('../pages/StatsPage'), 'StatsPage');
const WizardPage = page(() => import('../pages/WizardPage'), 'WizardPage');
const KitPage = page(() => import('../pages/KitPage'), 'KitPage');
const HandoffPage = page(() => import('../pages/HandoffPage'), 'HandoffPage');
const FunnelShowcasePage = page(() => import('../pages/FunnelShowcasePage'), 'FunnelShowcasePage');

/** Пока кусок страницы грузится — спокойный спиннер на её месте (оболочка сайта остаётся). */
function Lazy({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <Suspense fallback={<div className="grid min-h-[40vh] place-items-center" role="status" aria-label={t('common.loading')}><span className="spinner" /></div>}>
      {children}
    </Suspense>
  );
}

export function App() {
  return (
    <Routes>
      <Route path="/upload/" element={<Lazy><MobileUploadPage /></Lazy>} />
      <Route path="/" element={<Navigate to="/app" replace />} />
      <Route path="/login" element={<AuthPage mode="login" />} />
      <Route path="/register" element={<AuthPage mode="register" />} />
      <Route path="/blocked" element={<BlockedPage />} />
      {/* ссылка «на сайт» из публичного бота: вход по токену + трек в визарде */}
      <Route path="/go" element={<Lazy><HandoffPage /></Lazy>} />
      <Route path="/go/:token" element={<Lazy><HandoffPage /></Lazy>} />
      <Route path="/not-found" element={<SimplePage kind="404" />} />
      <Route path="/error" element={<SimplePage kind="error" />} />
      <Route path="/legal/policy" element={<Lazy><LegalPage kind="policy" /></Lazy>} />
      <Route path="/legal/offer" element={<Lazy><LegalPage kind="offer" /></Lazy>} />
      {/* витрина компонентов единой шкалы UI — только dev-сборка, в прод не попадает */}
      {import.meta.env.DEV && <Route path="/dev/kit" element={<Lazy><KitPage /></Lazy>} />}
      {/* витрина воронки после генерации: все модалки и состояния — только dev */}
      {import.meta.env.DEV && <Route path="/dev/funnel" element={<Lazy><FunnelShowcasePage /></Lazy>} />}
      <Route path="/app" element={<AppShell />}>
        <Route index element={<Lazy><DashboardPage /></Lazy>} />
        <Route path="generate" element={<Lazy><WizardPage /></Lazy>} />
        <Route path="projects" element={<Lazy><ProjectsPage /></Lazy>} />
        <Route path="projects/:id" element={<Lazy><ProjectDetailPage /></Lazy>} />
        <Route path="projects/:id/post" element={<Lazy><TikTokPostPage /></Lazy>} />
        <Route path="profile" element={<Lazy><ProfilePage /></Lazy>} />
        <Route path="pricing" element={<Lazy><PricingPage /></Lazy>} />
        <Route path="stats" element={<Lazy><StatsPage /></Lazy>} />
        <Route path="admin/analytics" element={<Lazy><AdminAnalyticsPage /></Lazy>} />
        <Route path="processing/:jobId" element={<Lazy><ProcessingPage /></Lazy>} />
      </Route>
      <Route path="*" element={<SimplePage kind="404" />} />
    </Routes>
  );
}
