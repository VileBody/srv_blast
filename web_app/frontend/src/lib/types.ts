export type PackageType = 'TRIAL' | 'BLAST' | 'GLOW' | 'IMPULSE';
export type ProjectStatus = 'ACTIVE' | 'IN_PROGRESS' | 'COMPLETED';
export type JobStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';

export interface User {
  id: string;
  email: string;
  name: string;
  surname: string;
  artistNick?: string | null;
  avatarUrl?: string | null;
  tgVerified: boolean;
  /** false — вход через Telegram прошёл, но ФИО ещё не заполнены (см. ProfileSetupGate) */
  profileComplete?: boolean;
  /** каким способом заведён аккаунт */
  authProvider?: 'telegram' | 'google';
  /** почта привязанного Google; null — второй способ входа не подключён */
  googleEmail?: string | null;
}

export interface Subscription {
  id: string;
  userId: string;
  tier: PackageType;
  /** null — безлимит по видео (Figma W43, тариф «Бесплатный») */
  creditsTotal: number | null;
  creditsUsed: number;
  /** Лимит уникальных треков (Figma W46/W47); null — безлимит */
  tracksTotal: number | null;
  tracksUsed: number;
  renewsAt?: string | null;
  isActive: boolean;
  startedAt?: string;
  /** trial | active | past_due (списание не прошло) | canceled (доступ до конца периода) */
  billingStatus?: 'trial' | 'active' | 'past_due' | 'canceled';
  /** true — автопродление отключено, доступ живёт до renewsAt */
  cancelAtPeriodEnd?: boolean;
  lastPaymentError?: string | null;
  /**
   * Модель оплаты активного плана. Blast — подписка (продлевается, отменяется),
   * Glow/Impulse — разовые покупки: автопродления нет, управлять нечем.
   */
  planKind?: 'subscription' | 'product' | 'trial';
  /** Срок действия разового продукта (Impulse — год); null — бессрочно */
  expiresAt?: string | null;
  /** Сколько бонусов со шкалы месяцев уже забрано */
  bonusesClaimed?: number;
}

/** Подписка ли это — от ответа зависят состояния оплаты и наличие отмены */
export function isSubscriptionPlan(subscription: Pick<Subscription, 'tier' | 'planKind'>): boolean {
  return (subscription.planKind ?? (subscription.tier === 'BLAST' ? 'subscription' : 'trial')) === 'subscription';
}

export interface TiktokAccount {
  userId: string;
  handle?: string | null;
  openId?: string | null;
  avatarUrl?: string | null;
  scopes?: string | null;
  expiresAt?: string | null;
}

export interface TiktokVideo {
  id: string;
  create_time: number;
  cover_image_url?: string;
  share_url?: string;
  title?: string;
  video_description?: string;
  view_count?: number;
  like_count?: number;
  comment_count?: number;
  share_count?: number;
}

export interface IterationSlice {
  key: string;
  source?: string;
  subtitleStyle?: string;
  hook?: string;
  views: number;
  engagement: number;
  videos: number;
  averageScore: number;
  jobId?: string;
}

/** Измерение, по которому сравниваются ролики: фон / стиль субтитров / хук */
export type AnalysisDimension = 'background' | 'subtitles' | 'fx';

/**
 * Вердикт по одному измерению:
 * - `signal` — есть лидер и отрыв, выдержавший поправку на число значений;
 * - `no_difference` — данных хватило, значения равны (тоже ответ: параметр не решает);
 * - `low_data` — не хватает роликов, чтобы читались хотя бы два значения;
 * - `blocked` — проверить нельзя: одно значение либо слиплось с другим измерением.
 */
export type AnalysisVerdict = 'signal' | 'no_difference' | 'low_data' | 'blocked';

export interface AnalysisValue {
  value: string;
  videos: number;
  averageScore: number;
  views: number;
}

export interface DimensionAnalysis {
  dimension: AnalysisDimension;
  verdict: AnalysisVerdict;
  values: AnalysisValue[];
  leader: AnalysisValue | null;
  liftPercent: number;
  /** Отрыв за вычетом поправки на число значений и размер выборки; >0 — сигнал живой */
  confidence: number;
  videosNeeded: number;
  /** `single_value` либо `entangled:<измерение>` */
  blockedBy: string | null;
}

export interface IterationAnalysis {
  projectId: string;
  videosAnalyzed: number;
  dimensions: DimensionAnalysis[];
  leadingDimension: AnalysisDimension | null;
  /** Сколько роликов до ближайшего читаемого измерения (0 — уже есть что показать) */
  videosNeeded: number;
  enoughData: boolean;
  minVideosPerValue: number;
  liftThresholdPercent: number;
  /** Лучшая связка целиком — нужна только как родитель следующей итерации */
  winner: IterationSlice | null;
}

export interface ContentIteration {
  id: string;
  projectId: string;
  number: number;
  parentJobId: string;
  jobId: string;
  testParameter: 'subtitles' | 'hooks' | 'background';
  winner: IterationSlice | null;
  createdAt: string;
}

export interface Project {
  id: string;
  userId: string;
  name: string;
  coverUrl?: string | null;
  packageType: PackageType;
  status: ProjectStatus;
  startedAt: string;
  endsAt?: string | null;
  views?: number;
  sparkline?: number[];
  generated?: number;
  /** Сколько из сгенерированных роликов уже опубликовано в TikTok */
  posted?: number;
  total?: number;
  /** Текущий проект — тот, в который идёт генерация по умолчанию */
  isCurrent?: boolean;
  /** Мягко скрыт из ленты: ролики и статистика остаются */
  archived?: boolean;
  jobs?: GenerationJob[];
}

export interface VideoVersion {
  id: string;
  index: number;
  status: JobStatus;
  progress: number;
  source: string;
  subtitleStyle: string;
  hook: string;
  thumbnailUrl?: string | null;
  downloadUrl?: string | null;
  /** проставляется бэком после успешной публикации в TikTok */
  postedAt?: string | null;
  tiktokStatus?: string | null;
  metrics?: {
    view_count: number;
    like_count: number;
    comment_count: number;
    share_count: number;
  };
  metricsSyncedAt?: string | null;
}

/** Ролик считается опубликованным, если бэк проставил postedAt / статус PUBLISH_COMPLETE */
export function isVideoPosted(video: Pick<VideoVersion, 'postedAt' | 'tiktokStatus'>): boolean {
  return Boolean(video.postedAt) || video.tiktokStatus === 'PUBLISH_COMPLETE';
}

export interface GenerationJob {
  id: string;
  projectId: string;
  userId: string;
  status: JobStatus;
  stageData: Record<string, unknown>;
  versions: number;
  rating?: number | string | null;
  outputUrls?: string[];
  videos: VideoVersion[];
  createdAt: string;
  completedAt?: string | null;
}

/** Свой исходник пользователя (Figma W39/W49) */
export interface UserSource {
  id: string;
  userId: string;
  s3Key: string;
  name: string;
  localUrl?: string;
  createdAt: string;
}

export interface SavedTrack {
  id: string;
  userId: string;
  s3Key: string;
  filename: string;
  durationS: number;
  localUrl?: string;
  createdAt: string;
  expiresAt: string;
}

export interface Vibe {
  id: string;
  name: string;
  score: number;
  previewUrl: string;
}

export interface DropCandidate {
  time: string;
  seconds: number;
  best: boolean;
  confidence: number;
}

export interface MeResponse {
  user: User;
  subscription: Subscription;
  tiktok: TiktokAccount | null;
  /** null — безлимит по видео (Figma W43) */
  creditsLeft: number | null;
  /** бот настроен И чат привязан — только тогда можно обещать «пришлём в Telegram» */
  telegramNotifications?: boolean;
  billingLinkRequired?: boolean;
  capabilities?: {
    customSources: boolean;
    analyzedDrops: boolean;
    remoteCompositePreviews: boolean;
    subscriptionBonuses: boolean;
  };
  mock?: boolean;
}

/** Кадр раскадровки для пикера обложки: url === null, пока ролик не готов */
export interface VideoFrame {
  index: number;
  timestampMs: number;
  url: string | null;
}

export interface VideoFramesResponse {
  videoId: string;
  jobId: string;
  status: JobStatus;
  durationMs: number;
  count: number;
  frames: VideoFrame[];
}

export interface ProjectsResponse {
  projects: Project[];
  activeProject?: Project | null;
  mock?: boolean;
}

export interface WizardSession {
  id: string;
  userId: string;
  projectId?: string | null;
  stage: number;
  data: Record<string, unknown>;
  updatedAt: string;
}

/** Аналитика (админка): всё считается из потока событий, отдельных счётчиков нет */
export interface AnalyticsSummary {
  days: number;
  activeUsers: number;
  signups: number;
  payingUsers: number;
  conversionToPaid: number;
  videosGenerated: number;
  videosPosted: number;
  generationFailRate: number;
  paymentFailures: number;
  cancellations: number;
  limitHits: number;
  events: number;
}

export interface FunnelRow {
  step: string;
  users: number;
  fromPrev: number;
  fromStart: number;
}

export interface RetentionRow {
  cohort: string;
  users: number;
  weeks: number[];
  percent: number[];
}

export interface DeliverySummary {
  days: number;
  users: number;
  byStage: Record<string, number>;
  generated: number;
  posted: number;
  postRate: number;
  generatedNotPosted: number;
  stuckAfterGeneration: number;
  avgBatchesPerUser: number;
  sawPricing: number;
  pricingToPaid: number;
  hitLimit: number;
  limitToPaid: number;
  postRateByPaid: { paid: number; free: number };
}

export interface UserJourney {
  userId: string;
  stage: string;
  stageIndex: number;
  projects: number;
  tracks: number;
  batches: number;
  generated: number;
  posted: number;
  postRate: number;
  sawPricing: boolean;
  hitLimit: boolean;
  paid: boolean;
  tier: string | null;
  failedGenerations: number;
  firstSeen: string | null;
  lastSeen: string | null;
  events: number;
}

/** Метрики прохождения из ревью: ожидание генерации, этап «Пул», возвраты назад */
export interface FlowMetrics {
  days: number;
  waitSessions: number;
  waitAbandonRate: number;
  waitMedianSeconds: number;
  abandonMedianSeconds: number;
  poolMedianSeconds: number;
  stageMedianSeconds: Record<string, number>;
  backClicks: number;
  backByStage: Record<string, number>;
}

export interface AnalyticsResponse {
  summary: AnalyticsSummary;
  funnel: FunnelRow[];
  retention: RetentionRow[];
  delivery: DeliverySummary;
  flow: FlowMetrics;
  journeys: UserJourney[];
  recent: { id: string; name: string; userId: string; ts: string; props: Record<string, unknown> }[];
  isAdmin: boolean;
}
