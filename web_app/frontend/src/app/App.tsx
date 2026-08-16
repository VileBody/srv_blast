import { Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from '../components/layout/AppShell';
import { AdminAnalyticsPage } from '../pages/AdminAnalyticsPage';
import { AuthPage } from '../pages/AuthPage';
import { BlockedPage } from '../pages/BlockedPage';
import { DashboardPage } from '../pages/DashboardPage';
import { LegalPage } from '../pages/LegalPage';
import { PricingPage } from '../pages/PricingPage';
import { ProcessingPage } from '../pages/ProcessingPage';
import { ProfilePage } from '../pages/ProfilePage';
import { TikTokPostPage } from '../pages/TikTokPostPage';
import { ProjectDetailPage } from '../pages/ProjectDetailPage';
import { ProjectsPage } from '../pages/ProjectsPage';
import { SimplePage } from '../pages/SimplePage';
import { StatsPage } from '../pages/StatsPage';
import { WizardPage } from '../pages/WizardPage';

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/app" replace />} />
      <Route path="/login" element={<AuthPage mode="login" />} />
      <Route path="/register" element={<AuthPage mode="register" />} />
      <Route path="/blocked" element={<BlockedPage />} />
      <Route path="/not-found" element={<SimplePage kind="404" />} />
      <Route path="/error" element={<SimplePage kind="error" />} />
      <Route path="/legal/policy" element={<LegalPage kind="policy" />} />
      <Route path="/legal/offer" element={<LegalPage kind="offer" />} />
      <Route path="/app" element={<AppShell />}>
        <Route index element={<DashboardPage />} />
        <Route path="generate" element={<WizardPage />} />
        <Route path="projects" element={<ProjectsPage />} />
        <Route path="projects/:id" element={<ProjectDetailPage />} />
        <Route path="projects/:id/post" element={<TikTokPostPage />} />
        <Route path="profile" element={<ProfilePage />} />
        <Route path="pricing" element={<PricingPage />} />
        <Route path="stats" element={<StatsPage />} />
        <Route path="admin/analytics" element={<AdminAnalyticsPage />} />
        <Route path="processing/:jobId" element={<ProcessingPage />} />
      </Route>
      <Route path="*" element={<SimplePage kind="404" />} />
    </Routes>
  );
}
