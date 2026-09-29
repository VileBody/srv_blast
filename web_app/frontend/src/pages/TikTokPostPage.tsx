import { CSSProperties, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent, ReactNode, RefObject, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../lib/api';
import { isVideoPosted, type VideoFrame, type VideoVersion } from '../lib/types';
import { cn } from '../lib/cn';
import { FullscreenZone } from '../components/ui/FullscreenZone';
import { QueryError, queryDown } from '../components/ui/ErrorState';
import { useWizardStore } from '../stores/wizardStore';
import './TikTokPostPage.css';

/*
 * Выкладка в TikTok. Слева форма, справа ролик на всю высоту, обложка выбирается внутри плеера.
 * Состав формы продиктован content-sharing-guidelines TikTok: аккаунт+аватар, кэпшен,
 * приватность БЕЗ дефолта, тумблеры взаимодействий выключены, раскрытие рекламы,
 * подтверждение прав на каждую публикацию и ссылка на Music Usage Confirmation.
 *
 * Кнопка публикации не бывает немой серой: если чего-то не хватает, она подсвечивает пропуски
 * у самих полей и ведёт к первому. Состояния: draft → uploading → posted (ссылка на пост +
 * переход к следующему невыложенному ролику, после последнего — в аналитику).
 */

type PostStage = 'draft' | 'uploading' | 'posted';
type Privacy = 'all' | 'followers' | 'friends' | 'self';
type Requirement = 'caption' | 'privacy' | 'brand' | 'rights';

const PRIVACY_ORDER: Privacy[] = ['all', 'followers', 'friends', 'self'];
const PRIVACY_API_VALUE: Record<Privacy, string> = {
  all: 'PUBLIC_TO_EVERYONE',
  followers: 'FOLLOWER_OF_CREATOR',
  friends: 'MUTUAL_FOLLOW_FRIENDS',
  self: 'SELF_ONLY'
};
const COVER_FRAME_COUNT = 8;
const CAPTION_MAX = 2200;
/** Габарит карточки на 1440: высота фуллскрин-зоны, ролик 9:16 на всю высоту справа */
const CARD_SIZE = { width: 960, height: 745 };
const MUSIC_USAGE_URL = 'https://www.tiktok.com/legal/page/global/music-usage-confirmation/en';
const BRANDED_POLICY_URL = 'https://www.tiktok.com/legal/page/global/bc-policy/en';

function Icon({ children, className = 'i' }: { children: ReactNode; className?: string }) {
  return <svg viewBox="0 0 24 24" className={className} aria-hidden="true">{children}</svg>;
}
const ICONS = {
  left: <path d="M14.5 6 8.5 12l6 6" />,
  right: <path d="M9.5 6l6 6-6 6" />,
  check: <path d="M5 12.5 9.5 17 19 7.5" />,
  comment: <path d="M4.5 6.5a2 2 0 0 1 2-2h11a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H11l-4.5 3.5v-3.5h0a2 2 0 0 1-2-2z" />,
  duet: <><rect x="3.5" y="5" width="7.5" height="14" rx="1.8" /><rect x="13" y="5" width="7.5" height="14" rx="1.8" /></>,
  stitch: <><rect x="4" y="4.5" width="16" height="15" rx="2" /><path d="M12 4.5v15M8 9.5l2 2.5-2 2.5" /></>,
  brand: <path d="M4 10v4a1 1 0 0 0 1 1h2l6 4V5L7 9H5a1 1 0 0 0-1 1zM17 9a4 4 0 0 1 0 6" />,
  copy: <><rect x="8.5" y="8.5" width="11" height="11" rx="2" /><path d="M15.5 8.5v-2a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2" /></>,
  ext: <path d="M13.5 5H19v5.5M19 5l-8 8M17 14v4a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 5 18V8.5A1.5 1.5 0 0 1 6.5 7H10" />
};

function Switch({ checked, onChange, labelledBy, disabled = false }: { checked: boolean; onChange: (v: boolean) => void; labelledBy: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-labelledby={labelledBy}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="ttp-switch"
    />
  );
}

const CheckMark = () => <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5 9.5 17 19 7.5" /></svg>;

const fmtTime = (sec: number) => {
  const safe = Number.isFinite(sec) && sec > 0 ? Math.floor(sec) : 0;
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, '0')}`;
};

/*
 * Хештег-подсказки: базовый набор из словаря + слова из названия проекта и чипов ролика
 * (фон / хук) — единственные осмысленные слова, которые у фронта есть без похода в TikTok за трендами.
 */
function toHashtag(raw: string): string {
  return raw
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, '')
    .slice(0, 24);
}

function hashtagSuggestions(base: string[], sources: (string | undefined | null)[], caption: string): string[] {
  const inCaption = new Set((caption.toLowerCase().match(/#[\p{L}\p{N}_]+/gu) ?? []).map((tag) => tag.slice(1)));
  const out: string[] = [];
  const push = (value: string) => {
    if (value.length < 3 || inCaption.has(value) || out.includes(value)) return;
    out.push(value);
  };
  // сначала «свои» — они точнее общих: сперва фраза целиком (#ночнойгород), потом слова
  sources.forEach((source) => {
    const value = (source ?? '').trim();
    if (!value) return;
    push(toHashtag(value));
    if (/\s/.test(value)) value.split(/\s+/).forEach((word) => push(toHashtag(word)));
  });
  base.forEach((word) => push(toHashtag(word)));
  return out.slice(0, 6);
}

/*
 * Лента кадров обложки внутри плеера. Кадры статичны, двигается рамка: её тянут, ставят
 * кликом или стрелками. Выбранный кадр сразу показывается во весь ролик (seek в плеере).
 */
function CoverStrip({
  frames,
  src,
  poster,
  value,
  onChange,
  onDone,
  active,
  stripRef,
  label
}: {
  frames?: VideoFrame[];
  src?: string | null;
  poster?: string | null;
  value: number;
  onChange: (frame: number) => void;
  onDone: () => void;
  active: boolean;
  stripRef: RefObject<HTMLDivElement>;
  label: string;
}) {
  const frameAtX = (clientX: number): number => {
    const rect = stripRef.current?.getBoundingClientRect();
    if (!rect) return value;
    return Math.max(0, Math.min(COVER_FRAME_COUNT - 1, Math.floor(((clientX - rect.left) / rect.width) * COVER_FRAME_COUNT)));
  };
  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    onChange(frameAtX(event.clientX));
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
    const frame = frameAtX(event.clientX);
    if (frame !== value) onChange(frame);
  };
  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Enter' || event.key === 'Escape') {
      event.preventDefault();
      onDone();
      return;
    }
    const delta = ({ ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1, Home: -COVER_FRAME_COUNT, End: COVER_FRAME_COUNT } as Record<string, number>)[event.key];
    if (delta === undefined) return;
    event.preventDefault();
    onChange(Math.max(0, Math.min(COVER_FRAME_COUNT - 1, value + delta)));
  };
  const selectedUrl = frames?.[value]?.url;

  return (
    <div
      ref={stripRef}
      className="ttp-strip"
      role="slider"
      tabIndex={active ? 0 : -1}
      aria-label={label}
      aria-valuemin={1}
      aria-valuemax={COVER_FRAME_COUNT}
      aria-valuenow={value + 1}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onKeyDown={onKeyDown}
    >
      {Array.from({ length: COVER_FRAME_COUNT }, (_, frame) => {
        // кадр с бэка (готовая раскадровка), иначе — перемотка <video> как фолбэк
        const ready = frames?.[frame]?.url;
        return (
          <span key={frame} aria-hidden="true">
            {ready ? (
              <img src={ready} alt="" />
            ) : src ? (
              <video
                src={src}
                poster={poster ?? undefined}
                muted
                playsInline
                preload="metadata"
                onLoadedMetadata={(event) => {
                  const duration = event.currentTarget.duration;
                  if (Number.isFinite(duration) && duration > 0) event.currentTarget.currentTime = Math.min(duration - 0.05, (duration * frame) / (COVER_FRAME_COUNT - 1));
                }}
                style={{ width: '100%', height: '100%', objectFit: 'cover', position: 'static', pointerEvents: 'none' }}
              />
            ) : poster ? <img src={poster} alt="" /> : null}
          </span>
        );
      })}
      <span className="ttp-sel" style={{ '--c': value } as CSSProperties} aria-hidden="true">
        {selectedUrl && <img src={selectedUrl} alt="" />}
      </span>
    </div>
  );
}

export function TikTokPostPage() {
  const { t } = useTranslation();
  const { id } = useParams();
  const [params] = useSearchParams();
  const qaPost = import.meta.env.DEV ? params.get('qaPost') : null;
  const batchId = params.get('batch');
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const resetWizard = useWizardStore((state) => state.reset);
  const setWizardStage = useWizardStore((state) => state.setStage);
  const projectQuery = useQuery({ queryKey: ['project', id], queryFn: () => api.project(id ?? ''), enabled: Boolean(id) });
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me });
  const creatorQuery = useQuery({
    queryKey: ['tiktok-creator-info'],
    queryFn: api.tiktokCreatorInfo,
    enabled: Boolean(meQuery.data?.tiktok),
    staleTime: 5 * 60 * 1000
  });

  const videos: VideoVersion[] = useMemo(() => {
    const jobs = projectQuery.data?.project.jobs ?? [];
    const selectedJobs = batchId ? jobs.filter((job) => job.id === batchId) : jobs;
    const generated = selectedJobs.flatMap((job) => job.videos).filter((v) => v.status === 'COMPLETED');
    if (generated.length || !qaPost) return generated;
    return [1, 2].map((index) => ({
      id: `qa-video-${index}`,
      index,
      status: 'COMPLETED' as const,
      progress: 100,
      source: 'Ночной город',
      subtitleStyle: 'Brat',
      hook: 'Молния',
      thumbnailUrl: '/assets/cover-placeholder.svg',
      downloadUrl: `/qa/video-${index}.mp4`
    }));
  }, [projectQuery.data, qaPost, batchId]);

  // ?video=N — постинг конкретной строки из батча (иконка TikTok в строке)
  const explicitIndex = params.get('video');
  const [index, setIndex] = useState(() => Math.max(0, Number(explicitIndex ?? 0) || 0));
  // ссылка без ?video (например «Выложить» с дашборда) открывает первый НЕвыложенный ролик.
  // Один раз на загрузку батча — иначе после публикации эффект перекидывал бы на следующий сам.
  const autoPicked = useRef(false);
  const [stage, setStage] = useState<PostStage>(() => qaPost === 'uploading' ? 'uploading' : qaPost === 'posted' || qaPost === 'next' ? 'posted' : 'draft');
  const [caption, setCaption] = useState(qaPost && qaPost !== 'empty' ? 'Новый сниппет уже в TikTok' : '');
  const [privacy, setPrivacy] = useState<Privacy | null>(qaPost && qaPost !== 'empty' ? 'all' : null);
  // TikTok requires every interaction to be enabled manually; none is preselected.
  const [comments, setComments] = useState(false);
  const [duet, setDuet] = useState(false);
  const [stitch, setStitch] = useState(false);
  const [commercialContent, setCommercialContent] = useState(false);
  const [brandOrganic, setBrandOrganic] = useState(false);
  const [brandContent, setBrandContent] = useState(false);
  // Первый кадр уже выбран как обложка: публиковать можно, не открывая пикер.
  const [coverFrame, setCoverFrame] = useState(qaPost && qaPost !== 'empty' ? 3 : 0);
  const [covering, setCovering] = useState(false);
  const [rights, setRights] = useState(Boolean(qaPost && qaPost !== 'empty'));
  // пропуски подсвечиваем только после попытки опубликовать, а не на пустой форме
  const [tried, setTried] = useState(false);
  const [postError, setPostError] = useState('');
  const [postUrl, setPostUrl] = useState<string | null>(null);
  /*
   * Реальный ход публикации по шагам: 1 — наш сервер передаёт файл в TikTok, 2 — TikTok
   * обрабатывает ролик (publish/status/fetch), 3 — опубликовано. Секундомер — что процесс живой.
   */
  const [progress, setProgress] = useState<{ step: 1 | 2 | 3; startedAt: number } | null>(null);
  const [clock, setClock] = useState(() => Date.now());
  const [playing, setPlaying] = useState(false);
  const [playhead, setPlayhead] = useState({ current: 0, duration: 0 });
  /*
   * Перенос описания, приватности и тумблеров на следующие ролики батча. Подтверждение прав
   * НЕ переносим — по гайдлайнам TikTok его надо подтверждать на каждую публикацию.
   */
  const [applyToAll, setApplyToAll] = useState(false);

  const videoRef = useRef<HTMLVideoElement>(null);
  const fieldsRef = useRef<HTMLDivElement>(null);
  const captionRef = useRef<HTMLTextAreaElement>(null);
  const privacyRef = useRef<HTMLDivElement>(null);
  const brandRef = useRef<HTMLDivElement>(null);
  const rightsRef = useRef<HTMLButtonElement>(null);
  const stripRef = useRef<HTMLDivElement>(null);
  const coverButtonRef = useRef<HTMLButtonElement>(null);

  const video = videos[index];
  // уже выложенный ролик (пришли по ?video=N или стрелкой) показываем выложенным, а не пустой формой
  const shownStage: PostStage = stage === 'draft' && video && isVideoPosted(video) ? 'posted' : stage;
  const creator = creatorQuery.data;
  const suggestions = hashtagSuggestions(
    t('tiktok.hashtagBase').split(','),
    [projectQuery.data?.project.name, video?.source, video?.hook],
    caption
  );
  // Раскадровка под пикер обложки приходит с бэка — вместо восьми <video>, перематывающих один файл.
  const framesQuery = useQuery({
    queryKey: ['video-frames', video?.id, COVER_FRAME_COUNT],
    queryFn: () => api.videoFrames(video?.id ?? '', COVER_FRAME_COUNT),
    enabled: Boolean(video?.id) && video?.status === 'COMPLETED' && !qaPost,
    staleTime: 5 * 60_000
  });
  const frames = framesQuery.data?.frames;

  // Требования гайдлайнов: без описания, явной приватности, типа рекламы и прав публиковать нельзя
  const missing: Requirement[] = [
    !caption.trim() && 'caption' as const,
    privacy === null && 'privacy' as const,
    commercialContent && !brandOrganic && !brandContent && 'brand' as const,
    !rights && 'rights' as const
  ].filter((item): item is Requirement => Boolean(item));
  /*
   * Раскрытие рекламы без выбранного типа — по гайдлайнам TikTok кнопка публикации заблокирована
   * (с подсказкой), а не «ведёт к пропуску», как остальные поля. Причину показываем сразу.
   */
  const brandIncomplete = missing.includes('brand');
  const invalid = (item: Requirement) => ((tried || item === 'brand') && missing.includes(item) ? '' : undefined);

  const nextUnposted = (): number => {
    for (let i = index + 1; i < videos.length; i += 1) if (!isVideoPosted(videos[i])) return i;
    for (let i = 0; i < index; i += 1) if (!isVideoPosted(videos[i])) return i;
    return -1;
  };
  const remaining = videos.filter((item, position) => position !== index && !isVideoPosted(item)).length;

  useEffect(() => {
    if (autoPicked.current || explicitIndex !== null || videos.length === 0) return;
    autoPicked.current = true;
    const first = videos.findIndex((item) => !isVideoPosted(item));
    if (first > 0) setIndex(first);
  }, [explicitIndex, videos]);

  // выбранный кадр обложки сразу показываем во весь плеер
  useEffect(() => {
    const element = videoRef.current;
    if (!element || !Number.isFinite(element.duration) || element.duration <= 0) return;
    element.currentTime = Math.min(element.duration - 0.05, (element.duration * coverFrame) / (COVER_FRAME_COUNT - 1));
  }, [coverFrame, video?.id]);

  useEffect(() => {
    if (covering) stripRef.current?.focus({ preventScroll: true });
  }, [covering]);

  useEffect(() => {
    if (creator?.comment_disabled) setComments(false);
    if (creator?.duet_disabled) setDuet(false);
    if (creator?.stitch_disabled) setStitch(false);
  }, [creator?.comment_disabled, creator?.duet_disabled, creator?.stitch_disabled]);

  useEffect(() => {
    if (commercialContent) return;
    setBrandOrganic(false);
    setBrandContent(false);
  }, [commercialContent]);

  useEffect(() => {
    if (brandContent && privacy === 'self') setPrivacy(null);
  }, [brandContent, privacy]);

  useEffect(() => {
    const allowed = creator?.privacy_level_options;
    if (!privacy || !allowed?.length) return;
    if (!allowed.includes(PRIVACY_API_VALUE[privacy])) setPrivacy(null);
  }, [creator?.privacy_level_options, privacy]);

  // секундомер публикации; стоял после ранних return — ломал порядок хуков при смене экрана
  useEffect(() => {
    if (!progress || progress.step === 3) return;
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [progress]);

  const startBatch = () => {
    resetWizard(id);
    setWizardStage(2);
    navigate(`/app/generate?project=${id}`);
  };

  /* Проект не загрузился — раньше это выглядело как «нет готовых видео»:
     человек думал, что ролики пропали, вместо «сеть отвалилась». */
  if (queryDown(projectQuery) && !qaPost) {
    const failed = <QueryError query={projectQuery} className="h-full" />;
    return <FullscreenZone responsiveScale onCollapse={() => navigate(`/app/projects/${id}`)} left={failed} right={<div className="card-2 h-full" />} />;
  }

  if (!projectQuery.isLoading && videos.length === 0) {
    const empty = (
      <div className="card-2 flex h-full flex-col items-center justify-center px-[28px] text-center">
        <h1 className="text-[32px] font-[400] leading-[38px] text-text">{t('tiktok.noVideosTitle')}</h1>
        <p className="mt-[20px] text-[16px] leading-[19px] text-text-60">{t('tiktok.noVideosText')}</p>
        <button type="button" onClick={startBatch} className="mt-[28px] flex h-[60px] items-center justify-center rounded-r15 border border-accent-light bg-grad-soft-20 px-[28px] text-[20px] font-[350] leading-none text-text-80 transition hover:text-text">
          {t('projectDetail.createBatch')}
        </button>
      </div>
    );
    const preview = (
      <div className="card-2 flex h-full flex-col p-[28px]">
        <h2 className="text-[24px] font-[350] leading-[29px] text-text-80">{t('projectDetail.previewVideo')}</h2>
        <div className="dash-panel-white mt-[28px] min-h-0 flex-1" />
      </div>
    );
    return <FullscreenZone responsiveScale onCollapse={() => navigate(`/app/projects/${id}`)} left={empty} right={preview} />;
  }

  if (!meQuery.isLoading && !meQuery.data?.tiktok && !qaPost) {
    const connect = (
      <div className="card-2 flex h-full flex-col items-center justify-center px-[28px] text-center">
        <h1 className="text-[32px] font-[400] leading-[38px] text-text">{t('tiktok.connectRequiredTitle')}</h1>
        <p className="mt-[20px] max-w-[300px] text-[16px] leading-[19px] text-text-60">{t('tiktok.connectRequiredText')}</p>
        <button type="button" onClick={() => window.location.assign(api.tiktokAuthUrl())} className="mt-[28px] flex h-[60px] items-center justify-center rounded-r15 border border-accent-light bg-grad-soft-20 px-[28px] text-[20px] font-[350] leading-none text-text-80 transition hover:text-text">
          {t('tiktok.connect')}
        </button>
      </div>
    );
    const preview = (
      <div className="card-2 flex h-full flex-col p-[28px]">
        <h2 className="text-[24px] font-[350] leading-[29px] text-text-80">{t('projectDetail.previewVideo')}</h2>
        <div className="dash-panel-white mt-[28px] min-h-0 flex-1 overflow-hidden">
          {(video?.downloadUrl || video?.thumbnailUrl) && (
            <video src={video.downloadUrl ?? undefined} poster={video.thumbnailUrl ?? undefined} muted playsInline preload="metadata" className="h-full w-full object-cover" />
          )}
        </div>
      </div>
    );
    return <FullscreenZone responsiveScale onCollapse={() => navigate(`/app/projects/${id}`)} left={connect} right={preview} />;
  }

  /** Ошибка TikTok человеческими словами: код из fail_reason или из ответа нашего API. */
  const explainFailure = (error: unknown): string => {
    const detail = error instanceof ApiError ? (error.detail as { detail?: { code?: string } | string })?.detail : undefined;
    const code = typeof detail === 'object' && detail?.code ? detail.code : error instanceof Error ? error.message : '';
    const known = code ? t(`tiktok.failReason.${code}`, { defaultValue: '' }) : '';
    if (known) return known;
    return code ? t('tiktok.failReason.other', { reason: code }) : t('tiktok.postError');
  };

  const user = meQuery.data?.user;
  const handle = creator?.creator_username ?? creator?.creator_nickname ?? meQuery.data?.tiktok?.handle ?? user?.artistNick ?? user?.name ?? '';
  const creatorAvatar = creator?.creator_avatar_url ?? user?.avatarUrl;
  const profileUrl = handle ? `https://www.tiktok.com/@${handle}` : 'https://www.tiktok.com/';
  const postLink = postUrl ?? (video?.tiktokPostIds?.[0] ? `${profileUrl}/video/${video.tiktokPostIds[0]}` : profileUrl);

  const submit = async () => {
    if (missing.length || shownStage !== 'draft') return;
    setStage('uploading');
    setPostError('');
    setCovering(false);
    setProgress({ step: 1, startedAt: Date.now() });
    setClock(Date.now());
    videoRef.current?.pause();
    try {
      const initialized = await api.postTiktok({
        projectId: id,
        videoId: video?.id,
        caption: caption.trim(),
        privacy,
        comments,
        duet,
        stitch,
        brandOrganic,
        brandContent,
        cover: true,
        coverFrame,
        coverTimestampMs: videoRef.current && Number.isFinite(videoRef.current.duration)
          ? Math.max(0, Math.round((videoRef.current.duration * 1000 * coverFrame) / (COVER_FRAME_COUNT - 1)))
          : 0,
        rights
      });
      let postId: string | undefined;
      if (initialized.status !== 'PUBLISH_COMPLETE') {
        // SENDING — файл ещё льётся с нашего сервера в TikTok (шаг 1), дальше обработка у TikTok
        let sending = initialized.status === 'SENDING';
        if (!sending) setProgress({ step: 2, startedAt: Date.now() });
        let complete = false;
        // обработка у TikTok — от десятков секунд до нескольких минут: ждём до 5 минут;
        // заливка с нашего сервера в этот лимит не входит (у неё свой таймаут на сервере)
        for (let attempt = 0; attempt < 200; ) {
          await new Promise((resolve) => window.setTimeout(resolve, 1500));
          const current = await api.tiktokPostStatus(initialized.publishId);
          if (current.status === 'PUBLISH_COMPLETE') {
            complete = true;
            postId = current.publicaly_available_post_id?.[0];
            break;
          }
          if (current.status === 'FAILED') throw new Error(current.fail_reason || t('tiktok.postError'));
          if (current.status === 'SENDING') continue;
          if (sending) {
            sending = false;
            setProgress({ step: 2, startedAt: Date.now() });
          }
          attempt += 1;
        }
        if (!complete) throw new Error(t('tiktok.postError'));
      }
      setPostUrl(postId ? `${profileUrl}/video/${postId}` : null);
      setProgress({ step: 3, startedAt: Date.now() });
      setStage('posted');
      // без этого «выложено N из M» и пропуск уже выложенных считались по устаревшему проекту
      queryClient.invalidateQueries({ queryKey: ['project', id] });
    } catch (error) {
      setStage('draft');
      setProgress(null);
      setPostError(explainFailure(error));
    }
  };

  /** Переход на другой ролик: форма сбрасывается (или переносится целиком при «те же настройки»). */
  const goTo = (next: number) => {
    setIndex(next);
    setStage('draft');
    setProgress(null);
    setPlaying(false);
    setPlayhead({ current: 0, duration: 0 });
    setCovering(false);
    setCoverFrame(0);
    setTried(false);
    setPostError('');
    setPostUrl(null);
    if (!applyToAll) {
      setCaption('');
      setPrivacy(null);
      setComments(false);
      setDuet(false);
      setStitch(false);
      setCommercialContent(false);
      setBrandOrganic(false);
      setBrandContent(false);
    }
    // права подтверждаем на каждый ролик отдельно — требование гайдлайнов TikTok
    setRights(false);
    fieldsRef.current?.scrollTo({ top: 0 });
  };

  /*
   * После публикации — к следующему НЕвыложенному ролику (с начала батча, если впереди пусто).
   * Всё выложено — в аналитику: дальше человеку нужен вывод «что прострелило», а не витрина.
   */
  const toNext = () => {
    const next = nextUnposted();
    if (next < 0) {
      navigate(`/app/stats?project=${id}`);
      return;
    }
    goTo(next);
  };

  /* Кнопка всегда нажимается: если чего-то не хватает, подсвечиваем пропуски и ведём к первому. */
  const onAction = () => {
    if (shownStage === 'uploading') return;
    if (shownStage === 'posted') {
      toNext();
      return;
    }
    if (!missing.length) {
      void submit();
      return;
    }
    setTried(true);
    const first = missing[0];
    const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (first === 'rights') {
      rightsRef.current?.focus();
      return;
    }
    const target = first === 'caption'
      ? captionRef.current
      : first === 'privacy'
        ? privacyRef.current?.querySelector<HTMLElement>('button[tabindex="0"]')
        : brandRef.current?.querySelector<HTMLElement>('.ttp-check');
    target?.closest('.ttp-field')?.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'nearest' });
    target?.focus({ preventScroll: true });
  };

  const togglePlay = () => {
    const element = videoRef.current;
    if (!element || covering) return;
    if (element.paused) void element.play();
    else element.pause();
  };
  const openCover = (open: boolean) => {
    if (open && shownStage !== 'draft') return;
    videoRef.current?.pause();
    setCovering(open);
    if (!open) window.setTimeout(() => coverButtonRef.current?.focus({ preventScroll: true }), 0);
  };

  const privacyChoice = (value: Privacy) => {
    const accountUnavailable = Boolean(creator?.privacy_level_options?.length) && !creator!.privacy_level_options.includes(PRIVACY_API_VALUE[value]);
    const brandedPrivate = value === 'self' && brandContent;
    return { unavailable: accountUnavailable || brandedPrivate, brandedPrivate };
  };
  const onPrivacyKey = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const step = ({ ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 } as Record<string, number>)[event.key];
    if (step === undefined || shownStage !== 'draft') return;
    event.preventDefault();
    const enabled = PRIVACY_ORDER.filter((value) => !privacyChoice(value).unavailable);
    if (!enabled.length) return;
    const current = Math.max(0, enabled.indexOf(privacy ?? enabled[0]));
    const next = enabled[(current + step + enabled.length) % enabled.length];
    setPrivacy(next);
    privacyRef.current?.querySelector<HTMLElement>(`[data-value="${next}"]`)?.focus();
  };
  const privacyIndex = privacy ? PRIVACY_ORDER.indexOf(privacy) : -1;
  const rovingPrivacy = privacy ?? PRIVACY_ORDER.find((value) => !privacyChoice(value).unavailable);

  const previewSrc = video?.playbackUrl ?? video?.downloadUrl ?? undefined;
  const chips = [video?.source, video?.subtitleStyle, video?.hook].filter(Boolean) as string[];
  const nextIndex = shownStage === 'posted' ? nextUnposted() : -1;
  const draft = shownStage === 'draft';
  const interactions = [
    { key: 'comments', icon: ICONS.comment, label: t('tiktok.comments'), hint: '', value: comments, set: setComments, off: Boolean(creator?.comment_disabled) },
    { key: 'duet', icon: ICONS.duet, label: t('tiktok.duet'), hint: t('tiktok.duetHint'), value: duet, set: setDuet, off: Boolean(creator?.duet_disabled) },
    { key: 'stitch', icon: ICONS.stitch, label: t('tiktok.stitch'), hint: t('tiktok.stitchHint'), value: stitch, set: setStitch, off: Boolean(creator?.stitch_disabled) }
  ];

  const card = (
    <main className={cn('ttp', !draft && 'locked')} aria-label={t('tiktok.screenTitle')}>
      <header className="ttp-top">
        <div className="ttp-batch">
          {videos.length > 1 && (
            <button type="button" className="ttp-nav" onClick={() => goTo(index - 1)} disabled={index === 0 || shownStage === 'uploading'} aria-label={t('tiktok.prevVideo')}>
              <Icon>{ICONS.left}</Icon>
            </button>
          )}
          <span className="ttp-title num">{videos.length > 1 ? t('tiktok.videoOfBatch', { n: index + 1, total: videos.length }) : t('tiktok.screenTitle')}</span>
          {videos.length > 1 && (
            <>
              <button type="button" className="ttp-nav" onClick={() => goTo(index + 1)} disabled={index === videos.length - 1 || shownStage === 'uploading'} aria-label={t('tiktok.nextVideoAria')}>
                <Icon>{ICONS.right}</Icon>
              </button>
              <span className="ttp-dots" aria-hidden="true">
                {videos.map((item, position) => (
                  <span key={item.id} className={cn('ttp-dot', position === index ? 'current' : isVideoPosted(item) && 'posted')} />
                ))}
              </span>
            </>
          )}
        </div>
        {/* аккаунт, куда уйдёт ролик — обязательный элемент гайдлайнов */}
        <div className="ttp-account" title={t('tiktok.accountHint')}>
          <span className="ttp-avatar">{creatorAvatar && <img src={creatorAvatar} alt="" />}</span>
          <span className="ttp-handle">@{handle}</span>
        </div>
      </header>

      <section className={cn('ttp-media', playing && 'playing', covering && 'covering')} aria-label={t('tiktok.videoAndCover')} onKeyDown={(event) => { if (event.key === 'Escape' && covering) openCover(false); }}>
        {previewSrc ? (
          <video
            key={video?.id}
            ref={videoRef}
            src={previewSrc}
            poster={video?.thumbnailUrl ?? frames?.[coverFrame]?.url ?? undefined}
            playsInline
            preload="metadata"
            onLoadedMetadata={(event) => {
              const duration = event.currentTarget.duration;
              setPlayhead({ current: 0, duration: Number.isFinite(duration) ? duration : 0 });
              if (Number.isFinite(duration) && duration > 0) event.currentTarget.currentTime = Math.min(duration - 0.05, (duration * coverFrame) / (COVER_FRAME_COUNT - 1));
            }}
            onTimeUpdate={(event) => setPlayhead({ current: event.currentTarget.currentTime, duration: event.currentTarget.duration || 0 })}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={() => setPlaying(false)}
            onClick={togglePlay}
          />
        ) : video?.thumbnailUrl ? <img src={video.thumbnailUrl} alt="" /> : null}
        <div className="ttp-shade" />
        <div className="ttp-meta">
          {shownStage === 'posted' && <span className="posted"><Icon>{ICONS.check}</Icon>{t('tiktok.onTiktok')}</span>}
          {chips.map((chip) => <span key={chip}>{chip}</span>)}
        </div>
        {previewSrc && (
          <button type="button" className="ttp-play" onClick={togglePlay} aria-label={playing ? t('common.pause') : t('common.play')} tabIndex={covering ? -1 : 0}>
            {playing ? (
              <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></svg>
            ) : (
              <svg viewBox="0 0 24 24" className="ic-play" aria-hidden="true"><path d="M7 4.5v15l12.5-7.5z" /></svg>
            )}
          </button>
        )}
        <div className="ttp-bar" aria-hidden={covering}>
          <div className="ttp-track"><i style={{ '--t': playhead.duration ? Math.min(1, playhead.current / playhead.duration) : 0 } as CSSProperties} /></div>
          <span className="ttp-time num">{fmtTime(playhead.current)} / {fmtTime(playhead.duration)}</span>
          <button ref={coverButtonRef} type="button" className="ttp-cover-btn" onClick={() => openCover(true)} disabled={!draft} tabIndex={covering ? -1 : 0}>
            <span className="ttp-mini">{frames?.[coverFrame]?.url && <img src={frames[coverFrame].url!} alt="" />}</span>
            {t('tiktok.cover')}
          </button>
        </div>
        <div className="ttp-picker" aria-hidden={!covering}>
          <div className="ttp-picker-head">
            <span>{t('tiktok.coverHint')}</span>
            <button type="button" className="ttp-done" onClick={() => openCover(false)} tabIndex={covering ? 0 : -1}>{t('tiktok.coverDone')}</button>
          </div>
          <CoverStrip
            frames={frames}
            src={video?.downloadUrl}
            poster={video?.thumbnailUrl}
            value={coverFrame}
            onChange={setCoverFrame}
            onDone={() => openCover(false)}
            active={covering}
            stripRef={stripRef}
            label={t('tiktok.cover')}
          />
        </div>
      </section>

      <div className="ttp-fields" ref={fieldsRef}>
        <div className="ttp-field" data-invalid={invalid('caption')}>
          <div className="ttp-field-head">
            <label className="ttp-label" htmlFor="ttp-caption">{t('tiktok.captionLabel')}</label>
            {tried && missing.includes('caption')
              ? <span className="ttp-err">{t('tiktok.errCaption')}</span>
              : caption.length > CAPTION_MAX - 200 && <span className="ttp-hint num">{caption.length} / {CAPTION_MAX}</span>}
          </div>
          <textarea
            id="ttp-caption"
            ref={captionRef}
            value={caption}
            maxLength={CAPTION_MAX}
            onChange={(event) => setCaption(event.target.value)}
            readOnly={!draft}
            placeholder={t('tiktok.captionPlaceholder')}
          />
          {draft && suggestions.length > 0 && (
            <div className="ttp-tags" aria-label={t('tiktok.hashtagsTitle')}>
              {suggestions.map((tag) => (
                <button key={tag} type="button" className="ttp-tag" onClick={() => { setCaption((c) => `${c.replace(/\s+$/, '')}${c.trim() ? ' ' : ''}#${tag} `); captionRef.current?.focus(); }}>
                  + #{tag}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* приватность без предвыбранного значения — требование TikTok */}
        <div className="ttp-field" data-invalid={invalid('privacy')}>
          <div className="ttp-field-head">
            <span className="ttp-label" id="ttp-privacy-label">{t('tiktok.privacyLabel')}</span>
            <span className="ttp-err">{t('tiktok.errPrivacy')}</span>
          </div>
          <div
            ref={privacyRef}
            className="ttp-seg"
            role="radiogroup"
            aria-labelledby="ttp-privacy-label"
            data-picked={privacyIndex >= 0 ? '' : undefined}
            onKeyDown={onPrivacyKey}
          >
            <span
              className="ttp-seg-thumb"
              aria-hidden="true"
              style={{ '--i': Math.max(0, privacyIndex), '--c2': Math.max(0, privacyIndex) % 2, '--r2': Math.floor(Math.max(0, privacyIndex) / 2) } as CSSProperties}
            />
            {PRIVACY_ORDER.map((value) => {
              const { unavailable, brandedPrivate } = privacyChoice(value);
              return (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  data-value={value}
                  aria-checked={privacy === value}
                  tabIndex={value === rovingPrivacy ? 0 : -1}
                  disabled={unavailable}
                  title={brandedPrivate ? t('tiktok.brandedPrivateUnavailable') : unavailable ? t('tiktok.privacyUnavailable') : undefined}
                  onClick={() => setPrivacy(value)}
                >
                  {t(`tiktok.privacy.${value}`)}
                </button>
              );
            })}
          </div>
        </div>

        {/* все interaction controls выключены по умолчанию — обязательное условие Direct Post */}
        <div className="ttp-field">
          <div className="ttp-field-head"><span className="ttp-label">{t('tiktok.allowTitle')}</span></div>
          <div className="ttp-rows">
            {interactions.map((item) => (
              <div key={item.key} className={cn('ttp-row', item.off && 'off')}>
                <Icon>{item.icon}</Icon>
                <span className="txt" id={`ttp-${item.key}`}>
                  {item.label}
                  {(item.off || item.hint) && <span className="sub">{item.off ? t('tiktok.disabledInApp') : item.hint}</span>}
                </span>
                <Switch checked={item.off ? false : item.value} onChange={item.set} labelledBy={`ttp-${item.key}`} disabled={item.off || !draft} />
              </div>
            ))}
          </div>
        </div>

        {/* Commercial Content Disclosure: выключено по умолчанию; включено — нужен хотя бы один тип */}
        <div className="ttp-field" ref={brandRef} data-invalid={invalid('brand')}>
          <div className="ttp-rows">
            <div className="ttp-row">
              <Icon>{ICONS.brand}</Icon>
              <span className="txt" id="ttp-brand">{t('tiktok.brandTitle')}<span className="sub">{t('tiktok.brandHint')}</span></span>
              <Switch
                checked={commercialContent}
                onChange={(on) => {
                  setCommercialContent(on);
                  // раскрытые варианты иначе уезжают под строку действия — докручиваем после анимации
                  if (on) window.setTimeout(() => brandRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 240);
                }}
                labelledBy="ttp-brand"
                disabled={!draft}
              />
            </div>
            <div className="ttp-disclose" data-open={commercialContent ? '' : undefined}>
              <div>
                <div className="ttp-inner">
                  <button type="button" className="ttp-check" role="checkbox" aria-checked={brandOrganic} tabIndex={commercialContent ? 0 : -1} onClick={() => setBrandOrganic((v) => !v)}>
                    <span className="ttp-box"><CheckMark /></span>
                    <span>{t('tiktok.yourBrand')}<span className="sub">{t('tiktok.yourBrandHint')}</span></span>
                  </button>
                  <button
                    type="button"
                    className="ttp-check"
                    role="checkbox"
                    aria-checked={brandContent}
                    tabIndex={commercialContent ? 0 : -1}
                    onClick={() => {
                      const checked = !brandContent;
                      setBrandContent(checked);
                      if (checked && privacy === 'self') setPrivacy(null);
                    }}
                  >
                    <span className="ttp-box"><CheckMark /></span>
                    <span>{t('tiktok.brandedContent')}<span className="sub">{t('tiktok.brandedContentHint')}</span></span>
                  </button>
                  <span className="ttp-err">{t('tiktok.errBrand')}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* перенос настроек на остаток батча — главный ускоритель выкладки */}
        {remaining > 0 && (
          <div className="ttp-carry">
            <Icon>{ICONS.copy}</Icon>
            <span className="txt" id="ttp-carry">{remaining === 1 ? t('tiktok.carryNext') : t('tiktok.carry', { count: remaining })}</span>
            <Switch checked={applyToAll} onChange={setApplyToAll} labelledBy="ttp-carry" disabled={!draft} />
          </div>
        )}
      </div>

      <div className="ttp-foot">
        <div className="ttp-foot-left ttp-swap" key={shownStage} aria-live="polite">
          {shownStage === 'posted' ? (
            <div className="ttp-status">
              <span className="ok"><Icon>{ICONS.check}</Icon></span>
              <span>
                {t('tiktok.postedLine', { n: index + 1 })}{' '}
                <a href={postLink} target="_blank" rel="noreferrer">{t('tiktok.openInTiktok')}<Icon>{ICONS.ext}</Icon></a>
              </span>
            </div>
          ) : shownStage === 'uploading' ? (
            <span>{progress?.step === 2 ? t('tiktok.processingNote') : t('tiktok.sendingNote')}</span>
          ) : (
            <>
              {postError && <p role="alert" className="ttp-alert">{postError}</p>}
              <div className="ttp-rights" data-invalid={invalid('rights')}>
                <button ref={rightsRef} type="button" className="ttp-box" role="checkbox" aria-checked={rights} aria-labelledby="ttp-rights-text" onClick={() => setRights((v) => !v)}>
                  <CheckMark />
                </button>
                <span id="ttp-rights-text">
                  {t('tiktok.rightsLead')}{' '}
                  {brandContent && (
                    <>
                      <a href={BRANDED_POLICY_URL} target="_blank" rel="noreferrer">{t('tiktok.brandedPolicyLink')}</a> {t('tiktok.and')}{' '}
                    </>
                  )}
                  <a href={MUSIC_USAGE_URL} target="_blank" rel="noreferrer">{t('tiktok.musicConsentLink')}</a>
                </span>
              </div>
            </>
          )}
        </div>
        <button
          type="button"
          onClick={onAction}
          disabled={draft && brandIncomplete}
          title={draft && brandIncomplete ? t('tiktok.errBrand') : undefined}
          aria-disabled={shownStage === 'uploading' || undefined}
          className={cn('ttp-act', shownStage === 'uploading' ? 'busy' : shownStage === 'posted' || !missing.length ? 'primary' : 'pending')}
        >
          {shownStage === 'uploading' && <span className="ttp-fill" style={{ '--p': progress?.step === 2 ? 0.85 : 0.45 } as CSSProperties} />}
          <span key={shownStage} className="ttp-swap" style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            {shownStage === 'uploading' ? (
              <>
                <span className="ttp-spin" aria-hidden="true" />
                <span className="num">
                  {progress
                    ? `${progress.step === 1 ? t('tiktok.phaseSending') : t('tiktok.phaseProcessing')} · ${Math.max(0, Math.round((clock - progress.startedAt) / 1000))} ${t('tiktok.secondsShort')}`
                    : t('tiktok.uploading')}
                </span>
              </>
            ) : shownStage === 'posted' ? (
              <>
                {nextIndex >= 0 ? t('tiktok.toVideo', { n: nextIndex + 1 }) : t('tiktok.toStats')}
                <Icon>{ICONS.right}</Icon>
              </>
            ) : t('tiktok.publish')}
          </span>
        </button>
      </div>
    </main>
  );

  return <FullscreenZone responsiveScale onCollapse={() => navigate(`/app/projects/${id}`)} card={{ ...CARD_SIZE, node: card }} />;
}
