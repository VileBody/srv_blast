import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { api } from '../../lib/api';

const ATTRIBUTION_KEY = 'blast_app_attribution';
const ENTRY_KEY = 'blast_app_entry_tracked';

function attribution(): Record<string, string> {
  const names: Record<string, string> = {
    utm_source: 'source', utm_medium: 'medium', utm_campaign: 'campaign',
    utm_content: 'content', utm_term: 'term'
  };
  try {
    const saved = JSON.parse(sessionStorage.getItem(ATTRIBUTION_KEY) || '{}') as Record<string, string>;
    const params = new URLSearchParams(window.location.search);
    Object.entries(names).forEach(([query, prop]) => {
      const value = params.get(query)?.trim();
      if (value) saved[prop] = value.slice(0, 160);
    });
    if (!saved.referrer && document.referrer) {
      const host = new URL(document.referrer).hostname;
      if (host && host !== window.location.hostname) saved.referrer = host.slice(0, 160);
    }
    sessionStorage.setItem(ATTRIBUTION_KEY, JSON.stringify(saved));
    return saved;
  } catch {
    return {};
  }
}

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
    try {
      if (!sessionStorage.getItem(ENTRY_KEY)) {
        sessionStorage.setItem(ENTRY_KEY, '1');
        void api.trackEvent('app_entry', attribution()).catch(() => {
          sessionStorage.removeItem(ENTRY_KEY);
        });
      }
    } catch {
      void api.trackEvent('app_entry', attribution()).catch(() => {});
    }
  }, []);

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
