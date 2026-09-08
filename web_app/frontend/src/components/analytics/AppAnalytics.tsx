import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { api } from '../../lib/api';

function routeName(pathname: string): string {
  if (pathname === '/app') return 'dashboard';
  if (pathname === '/app/projects') return 'projects';
  if (/^\/app\/projects\/[^/]+\/post$/.test(pathname)) return 'publish';
  if (/^\/app\/projects\/[^/]+$/.test(pathname)) return 'project_detail';
  if (/^\/app\/processing\/[^/]+$/.test(pathname)) return 'processing';
  if (pathname === '/app/generate') return 'wizard';
  if (pathname === '/app/pricing') return 'pricing';
  if (pathname === '/app/profile') return 'profile';
  if (pathname === '/app/stats') return 'stats';
  if (pathname === '/app/admin/analytics') return 'admin_analytics';
  return 'other';
}

/** Authenticated product analytics. Route names contain no project ids or user input. */
export function AppAnalytics() {
  const location = useLocation();
  const previous = useRef<string | null>(null);

  useEffect(() => {
    const route = routeName(location.pathname);
    if (previous.current === route) return;
    const from = previous.current;
    previous.current = route;
    void api.trackEvent('page_view', {
      route,
      ...(from ? { from } : {}),
      language: document.documentElement.lang || 'ru',
      viewport: window.innerWidth < 768 ? 'mobile' : window.innerWidth < 1200 ? 'compact' : 'desktop'
    }).catch(() => {});
  }, [location.pathname]);

  return null;
}
