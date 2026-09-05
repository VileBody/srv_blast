import type {
  DropCandidate,
  GenerationJob,
  MeResponse,
  Project,
  ProjectsResponse,
  AnalyticsResponse,
  Subscription,
  SavedTrack,
  UserSource,
  TiktokVideo,
  ContentIteration,
  IterationAnalysis,
  Vibe,
  VideoFramesResponse,
  WizardSession
} from './types';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '';

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : `API error ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

/*
 * DEV-переключатель для проверки состояний ошибки: `?qaFail=projects,me` роняет
 * перечисленные ручки с 503, `?qaFail=all` — вообще все. В проде выключен (import.meta.env.DEV),
 * как и остальные QA-флаги (?qaStage, ?qaPost, ?plan, ?state).
 */
function qaFailMatches(path: string): boolean {
  if (!import.meta.env.DEV) return false;
  const raw = new URLSearchParams(window.location.search).get('qaFail');
  if (!raw) return false;
  if (raw === 'all') return true;
  return raw.split(',').some((part) => part.trim() && path.includes(part.trim()));
}

/*
 * CSRF: бэк кладёт токен в НЕ-HttpOnly cookie, а мы возвращаем его заголовком.
 * Сторонний домен cookie прочитать не может — значит и заголовок подделать не может.
 */
const CSRF_COOKIE = 'blast_csrf';
const CSRF_HEADER = 'X-CSRF-Token';

function csrfToken(): string {
  const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${CSRF_COOKIE}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : '';
}

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

async function send(path: string, init: RequestInit): Promise<Response> {
  const method = (init.method ?? 'GET').toUpperCase();
  const headers: Record<string, string> = {
    ...(init.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
    ...((init.headers as Record<string, string>) ?? {})
  };
  if (!SAFE_METHODS.has(method)) {
    const token = csrfToken();
    if (token) headers[CSRF_HEADER] = token;
  }
  return fetch(`${API_BASE}${path}`, { credentials: 'include', ...init, headers });
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (qaFailMatches(path)) {
    // задержка обязательна: мгновенный reject — не то же самое, что упавший запрос
    await new Promise((resolve) => window.setTimeout(resolve, 150));
    throw new ApiError(503, { detail: 'qaFail', code: 'qa_fail' });
  }
  let response = await send(path, init);

  /*
   * 403 csrf_failed — не «сессия кончилась», а «токена ещё нет» (первый заход, cookie
   * протухла). Любой GET заставляет бэк выдать свежий токен, после чего повторяем.
   * Ровно один раз, иначе при реальной проблеме получился бы бесконечный цикл.
   */
  if (response.status === 403 && !SAFE_METHODS.has((init.method ?? 'GET').toUpperCase())) {
    const detail = await response.clone().json().catch(() => null);
    if (detail && (detail as { code?: string }).code === 'csrf_failed') {
      await fetch(`${API_BASE}/api/tiktok/status`, { credentials: 'include' }).catch(() => {});
      response = await send(path, init);
    }
  }

  if (!response.ok) {
    // A Response body is a one-shot stream.  Reading json() and then text()
    // in the catch path used to turn every non-JSON backend error into the
    // misleading "body stream already read" message.
    let detail: unknown = response.statusText || `Request failed (${response.status})`;
    const body = await response.text().catch(() => '');
    if (body) {
      try {
        detail = JSON.parse(body) as unknown;
      } catch {
        detail = body;
      }
    }
    /*
     * Сессия кончилась или её не было — уводим на вход, а не показываем пустой экран.
     * Ориентируемся на code === 'auth_required', а не на голый 401: тем же статусом
     * отвечает TikTok при протухшем токене, и выкидывать из аккаунта за это нельзя.
     * Со страниц входа не редиректим — там 401 нормальный ответ формы.
     */
    const authRequired =
      response.status === 401 &&
      typeof detail === 'object' && detail !== null &&
      (detail as { code?: string }).code === 'auth_required';
    if (authRequired && !/^\/(login|register)/.test(window.location.pathname)) {
      window.location.replace('/login');
    }

    /*
     * Бан за переиспользованный TikTok: бэк отвечает 403 с этим кодом на ЛЮБУЮ ручку,
     * кроме статуса бана и выхода. Уводим на экран блокировки — без этого человек видел
     * бы «сервер прилёг» на каждом экране и считал бы бан сбоем.
     */
    const banned =
      response.status === 403 &&
      typeof detail === 'object' && detail !== null &&
      (detail as { code?: string }).code === 'account_banned';
    if (banned && window.location.pathname !== '/blocked') {
      window.location.replace('/blocked');
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  me: () => request<MeResponse>('/api/me'),
  /**
   * Вход/регистрация через Telegram: личность определяется по chat_id из `/start <token>`.
   * `mode: 'login'` НЕ создаёт аккаунт — если его нет, ответ придёт с `noAccount`.
   */
  tgStart: (profile: { name?: string; surname?: string; mode?: 'login' | 'register' } = {}) =>
    request<{ token: string; deepLink: string; botConfigured: boolean }>('/api/auth/tg-start', {
      method: 'POST',
      body: JSON.stringify(profile)
    }),
  tgVerify: (token: string) =>
    request<{ verified: boolean; noAccount?: boolean; user?: { id: string; email: string; name: string } }>(`/api/auth/tg-verify?token=${encodeURIComponent(token)}`),
  logout: () => request<{ ok: boolean }>('/api/auth/logout', { method: 'POST' }),
  /** Причина блокировки аккаунта — единственная ручка, которая забаненному отвечает 200 */
  banStatus: () => request<{ banned: boolean; reason: string | null; bannedAt: string | null }>('/api/auth/ban-status'),
  /**
   * Какие способы входа доступны. `googleBlocked` — ключи есть, но стране предлагать
   * Google нельзя (см. security.google_allowed): у «не настроено» и «запрещено» разные тексты.
   */
  authProviders: () => request<{ telegram: boolean; google: boolean; googleBlocked: boolean; country: string | null }>('/api/auth/providers'),
  /** Вход через Google: уходим на бэк, он редиректит на экран выбора аккаунта */
  googleAuthUrl: () => `${API_BASE}/api/auth/google`,
  /** Привязка Google к уже открытому аккаунту (кнопка в профиле) */
  googleLinkUrl: () => `${API_BASE}/api/auth/google/link`,
  unlinkGoogle: () => request<{ ok: boolean }>('/api/auth/google/link', { method: 'DELETE' }),

  /** Аналитика админки: сводка + воронка + удержание */
  adminAnalytics: (days = 30) => request<AnalyticsResponse>(`/api/admin/analytics?days=${days}`),
  /** Клиентское событие воронки (то, чего не видно на бэке) */
  trackEvent: (name: string, props: Record<string, unknown> = {}) =>
    request<{ ok: boolean; id: string }>('/api/analytics/track', { method: 'POST', body: JSON.stringify({ name, props }) }),

  projects: () => request<ProjectsResponse>('/api/projects'),
  project: (projectId: string) => request<{ project: Project }>(`/api/projects/${projectId}`),
  createProject: (payload: { name: string; coverChoice: string; packageType: string }) =>
    request<{ project: Project; redirectTo: string }>('/api/projects', { method: 'POST', body: JSON.stringify(payload) }),
  /** Раскадровка ролика под пикер обложки (модалка выкладки) */
  videoFrames: (videoId: string, count = 8) =>
    request<VideoFramesResponse>(`/api/videos/${encodeURIComponent(videoId)}/frames?count=${count}`),
  /** Переименование / архив проекта (присылаем только изменённое поле) */
  updateProject: (projectId: string, payload: { name?: string; archived?: boolean }) =>
    request<{ project: Project }>(`/api/projects/${projectId}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteProject: (projectId: string) => request<{ ok: boolean }>(`/api/projects/${projectId}`, { method: 'DELETE' }),
  activateProject: (projectId: string) =>
    request<{ project: Project }>(`/api/projects/${projectId}/activate`, { method: 'POST' }),
  uploadProjectCover: (projectId: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<{ coverUrl: string }>(`/api/projects/${projectId}/cover`, { method: 'POST', body: form });
  },

  createOrder: (payload: { packageType: string; projectId?: string; name?: string; coverChoice?: string; recurrentAccepted?: boolean }) =>
    request<{ orderId: string; paymentUrl: string; project?: Project | null }>('/api/payments/create-order', {
      method: 'POST',
      body: JSON.stringify(payload)
    }),
  /** Отмена: по умолчанию с конца оплаченного периода (immediate — сразу) */
  cancelSubscription: (immediate = false) =>
    request<{ ok: boolean; subscription: Subscription }>(`/api/payments/cancel-sub?immediate=${immediate}`, { method: 'POST' }),
  /** Повтор списания после неудачной оплаты */
  retryPayment: () => request<{ ok: boolean; subscription: Subscription; paymentUrl: string | null }>('/api/payments/retry', { method: 'POST' }),
  /** Вернуть автопродление после запланированной отмены */
  resumeSubscription: () => request<{ ok: boolean; subscription: Subscription }>('/api/payments/resume', { method: 'POST' }),
  /** Забрать бонус со шкалы месяцев (+1 трек, за третий месяц — снятие лимита) */
  claimBonus: () => request<{ ok: boolean; subscription: Subscription }>('/api/payments/claim-bonus', { method: 'POST' }),

  /** Старт подключения TikTok: уходим на бэк, он редиректит на TikTok (или мокает без ключей) */
  tiktokAuthUrl: () => `${API_BASE}/api/tiktok/auth`,
  tiktokStatus: () => request<{ configured: boolean; scopes: string; redirectUri: string; uploadSource: 'FILE_UPLOAD' | 'PULL_FROM_URL' }>('/api/tiktok/status'),
  tiktokCreatorInfo: () => request<{
    creator_avatar_url?: string;
    creator_username?: string;
    creator_nickname?: string;
    privacy_level_options: string[];
    duet_disabled?: boolean;
    comment_disabled?: boolean;
    stitch_disabled?: boolean;
    mock?: boolean;
  }>('/api/tiktok/creator-info'),
  tiktokVideos: (days = 30) => request<{ videos: TiktokVideo[]; hasMore: boolean; retentionAvailable: false; mock?: boolean }>(`/api/tiktok/videos?days=${days}`),

  previousTrack: () => request<{ track: SavedTrack | null }>('/api/wizard/previous-track'),
  uploadTrack: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<{ track: SavedTrack }>('/api/wizard/upload-track', { method: 'POST', body: form });
  },
  sources: (projectId: string) => request<{ sources: UserSource[] }>(`/api/wizard/sources?projectId=${encodeURIComponent(projectId)}`),
  deleteSource: (id: string) => request(`/api/wizard/sources/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  uploadLink: (projectId: string, format: string) => request<{ url: string; expiresAt: number; qrSvg: string }>(`/api/wizard/upload-link?projectId=${encodeURIComponent(projectId)}&format=${encodeURIComponent(format)}`, { method: 'POST' }),
  uploadSource: (file: File, projectId: string, format: string) => {
    const form = new FormData();
    form.append('file', file);
    return request<{ source: UserSource }>(`/api/wizard/upload-source?projectId=${encodeURIComponent(projectId)}&format=${encodeURIComponent(format)}`, { method: 'POST', body: form });
  },
  fxPreviews: () => request<{ previews: { id: string; name: string; previewUrl: string }[] }>('/api/wizard/fx-previews'),
  uploadHookVideo: (file: File) => {
    const form = new FormData(); form.append('file', file);
    return request<{ name: string; url: string; playbackUrl: string; duration: number; width: number; height: number; hasAudio: boolean }>('/api/wizard/upload-hook-video', { method: 'POST', body: form });
  },
  uploadHookSound: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<{ name: string; url: string; playbackUrl: string; duration: number; width?: number; height?: number; hasAudio?: boolean; mock: boolean }>('/api/wizard/upload-hook-sound', { method: 'POST', body: form });
  },
  // Окно отрывка обязательно: кандидаты дропа ищутся ВНУТРИ него, как в боте.
  // Без окна прод отвечает status:'NEEDS_CLIP' (не ошибкой — это нормальное
  // состояние визарда до выбора отрывка).
  drops: (clipFrom = '', clipTo = '') =>
    request<{ status: string; bpm: number; drops: DropCandidate[] }>(
      `/api/wizard/drops?clipFrom=${encodeURIComponent(clipFrom)}&clipTo=${encodeURIComponent(clipTo)}`
    ),
  // plane — план подбора (vibes 9:16 / cine16x9 / films). Без него степпер типов
  // футажей листался, а список примеров не менялся.
  vibes: (plane = 'vibes') =>
    request<{ status: string; vibes: Vibe[] }>(`/api/wizard/vibes?plane=${encodeURIComponent(plane)}`),
  photos: () => request<{ status: string; photos: Vibe[] }>('/api/wizard/photos'),
  subtitleStyles: () => request<{ status: string; styles: { id: string; name: string; previewUrl: string }[] }>('/api/wizard/subtitle-styles'),
  wizardSession: () => request<{ session: WizardSession | null }>('/api/wizard/session'),
  saveWizardSession: (payload: { projectId?: string | null; stage: number; data: Record<string, unknown> }) =>
    request<{ session: WizardSession }>('/api/wizard/session', { method: 'POST', body: JSON.stringify(payload) }),
  submitWizard: (payload: { projectId?: string | null; stageData: Record<string, unknown>; videosToGenerate: number; idempotencyKey: string }) =>
    request<{ job: GenerationJob; redirectTo: string }>('/api/wizard/submit', { method: 'POST', body: JSON.stringify(payload) }),

  compositePreview: (style: string, hook: string) => {
    const params = new URLSearchParams({ style, hook });
    return request<{ previewUrl: string }>(`/api/preview/composite?${params.toString()}`);
  },

  job: (jobId: string) => request<{ job: GenerationJob }>(`/api/jobs/${jobId}`),
  activeJob: () => request<{ job: GenerationJob | null }>('/api/jobs/active'),
  rateJob: (jobId: string, payload: { rating: string | number; feedback?: string }) =>
    request<{ ok: boolean; job: GenerationJob }>(`/api/jobs/${jobId}/rate`, { method: 'POST', body: JSON.stringify(payload) }),
  iterations: (projectId: string) => request<{ iterations: ContentIteration[]; analysis: IterationAnalysis }>(`/api/projects/${projectId}/iterations`),
  createIteration: (projectId: string, payload: { videosToGenerate: number; testParameter: 'subtitles' | 'hooks' | 'background' }) =>
    request<{ iteration: ContentIteration; job: GenerationJob; redirectTo: string }>(`/api/projects/${projectId}/iterations`, { method: 'POST', body: JSON.stringify(payload) }),

  updateProfile: (payload: { name?: string; surname?: string; artistNick?: string }) =>
    request<MeResponse['user'] extends infer _ ? { user: MeResponse['user'] } : never>('/api/profile', { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteAccount: () => request<{ ok: true; deleted: { identities: number; projects: number; jobs: number } }>('/api/profile', {
    method: 'DELETE',
    body: JSON.stringify({ confirmation: 'DELETE' })
  }),
  uploadAvatar: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<{ avatarUrl: string }>('/api/profile/avatar', { method: 'POST', body: form });
  },
  disconnectTiktok: () => request<{ ok: boolean }>('/api/tiktok/disconnect', { method: 'DELETE' }),
  postTiktok: (payload: Record<string, unknown>) => request<{ ok: boolean; status: string; publishId: string; mock?: boolean }>('/api/tiktok/post', { method: 'POST', body: JSON.stringify(payload) }),
  tiktokPostStatus: (publishId: string) => request<{ publishId: string; status?: string; fail_reason?: string; publicaly_available_post_id?: string[]; mock?: boolean }>(`/api/tiktok/post/${encodeURIComponent(publishId)}`)
};

export function humanDate(value?: string | null): string {
  if (!value) return '—';
  return new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' }).format(new Date(value));
}

export function durationLabel(seconds: number): string {
  const min = Math.floor(seconds / 60);
  const sec = Math.round(seconds % 60).toString().padStart(2, '0');
  return `${min}:${sec}`;
}
