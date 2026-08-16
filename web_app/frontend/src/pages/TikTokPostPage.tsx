import { PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { isVideoPosted, type VideoFrame, type VideoVersion } from '../lib/types';
import { cn } from '../lib/cn';
import { FullscreenZone } from '../components/ui/FullscreenZone';
import { QueryError, queryDown } from '../components/ui/ErrorState';
import { useWizardStore } from '../stores/wizardStore';

/*
 * Предпост в TikTok (Figma W52 → W54 → W55 → W56 → W57 → W58) в каркасе фуллскрин-зоны.
 * Состав формы продиктован content-sharing-guidelines TikTok: аккаунт+аватар, кэпшен,
 * селектор приватности БЕЗ дефолта, тумблеры взаимодействий и подтверждение прав.
 *
 * Состояния: draft (W52 пусто / W54 валидно) → uploading (W55) → posted (W56 «Видео в Тик-Токе»,
 * затем W57 «к следующему видео») → следующий ролик сбрасывает форму (W58) и сдвигает дату на +1 день.
 */

type PostStage = 'draft' | 'uploading' | 'posted';
type Privacy = 'all' | 'friends' | 'self';

const PRIVACY_ORDER: Privacy[] = ['all', 'friends', 'self'];
/** Пока кнопка «Видео в Тик-Токе» держит статус, потом сменяется на «к следующему видео» (W56→W57) */
const POSTED_STATUS_MS = 2500;
const COVER_FRAME_COUNT = 8;

/** Тумблер 24×12 (Figma 764:3856) */
function MiniToggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={cn(
        'relative h-[12px] w-[24px] shrink-0 rounded-full transition-colors',
        checked ? 'bg-accent-light' : 'bg-[rgba(246,245,253,0.3)]'
      )}
    >
      <span className={cn('absolute top-[1px] h-[10px] w-[10px] rounded-full bg-[#f6f5fd] transition-all', checked ? 'left-[13px]' : 'left-[1px]')} />
    </button>
  );
}

/** Радио приватности (Figma 764:3876): кружок 12, выбранный — залит accent */
function PrivacyRadio({ checked }: { checked: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'flex h-[12px] w-[12px] shrink-0 items-center justify-center rounded-full border transition-colors',
        checked ? 'border-accent-light' : 'border-[rgba(246,245,253,0.5)]'
      )}
    >
      {checked && <span className="h-[6px] w-[6px] rounded-full bg-accent-light" />}
    </span>
  );
}

/** Чекбокс прав (Figma 764:3892), 20×20 r5. `size` — компактный вариант для служебных галок */
function RightsCheckbox({ checked, onChange, label, size = 20 }: { checked: boolean; onChange: (v: boolean) => void; label: string; size?: number }) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="flex min-w-0 items-center gap-[10px] text-left"
    >
      <span
        style={{ width: size, height: size }}
        className={cn(
          'flex shrink-0 items-center justify-center rounded-[5px] border transition-colors',
          checked ? 'border-accent-light bg-accent-light' : 'border-[rgba(246,245,253,0.5)]'
        )}
      >
        {checked && (
          <svg viewBox="0 0 12 10" width={size * 0.55} height={size * 0.45} fill="none" aria-hidden="true">
            <path d="M1 5l3.2 3.2L11 1.4" stroke="#05010f" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </span>
      <span className="truncate leading-none text-text-80" style={{ fontSize: size >= 20 ? 16 : 15 }}>{label}</span>
    </button>
  );
}

/*
 * Пикер кадра обложки (правка заказчика): КАДРЫ СТАТИЧНЫ — равномерно разложены по всей ширине
 * пила и не двигаются. Движется САМА обводка-курсор: её тянут драгом/колесом или ставят кликом
 * по кадру, вместе с ней меняется фокус-кадр обложки (принцип курсора в баре прогресса).
 */
function CoverPicker({ src, poster, frames, value, onChange }: { src?: string | null; poster?: string | null; frames?: VideoFrame[]; value: number | null; onChange: (frame: number) => void }) {
  const stripRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const selected = value ?? 0;
  const frameW = 100 / COVER_FRAME_COUNT;

  // индекс кадра под курсором мыши — по X внутри пила
  const frameAtX = (clientX: number): number => {
    const strip = stripRef.current;
    if (!strip) return selected;
    const rect = strip.getBoundingClientRect();
    return Math.max(0, Math.min(COVER_FRAME_COUNT - 1, Math.floor(((clientX - rect.left) / rect.width) * COVER_FRAME_COUNT)));
  };

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    dragging.current = true;
    stripRef.current?.setPointerCapture(event.pointerId);
    onChange(frameAtX(event.clientX));
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    const f = frameAtX(event.clientX);
    if (f !== value) onChange(f);
  };
  const onPointerEnd = () => { dragging.current = false; };

  return (
    <div
      ref={stripRef}
      className="relative flex h-[60px] cursor-pointer overflow-hidden rounded-r15 bg-[rgba(20,14,36,0.88)] backdrop-blur-[15px] active:cursor-grabbing"
      onWheel={(event) => {
        event.preventDefault();
        const dir = (Math.abs(event.deltaY) >= Math.abs(event.deltaX) ? event.deltaY : event.deltaX) > 0 ? 1 : -1;
        onChange(Math.max(0, Math.min(COVER_FRAME_COUNT - 1, selected + dir)));
      }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerEnd}
      onPointerCancel={onPointerEnd}
    >
      {Array.from({ length: COVER_FRAME_COUNT }, (_, frame) => {
        // кадр с бэка (готовая раскадровка), иначе — прежняя перемотка <video> как фолбэк
        const ready = frames?.[frame]?.url;
        return (
          <div key={frame} aria-hidden="true" className="relative h-[60px] shrink-0 overflow-hidden" style={{ width: `${frameW}%` }}>
            {ready ? (
              <img src={ready} alt="" className="h-full w-full object-cover" />
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
                className="h-full w-full object-cover"
              />
            ) : poster ? <img src={poster} alt="" className="h-full w-full object-cover" /> : <span className="block h-full w-full bg-grad-soft-20" />}
          </div>
        );
      })}
      {/* обводка-курсор — двигается по кадрам, отвечает за выбор. Радиус = радиусу контейнера
          (r15), иначе родитель с overflow-hidden обрезает более острые углы курсора у краёв */}
      <span
        className="pointer-events-none absolute top-0 h-[60px] rounded-r15 shadow-[inset_0_0_0_2px_var(--accent-light)] transition-[left] duration-150"
        style={{ left: `${selected * frameW}%`, width: `${frameW}%` }}
        aria-hidden="true"
      />
    </div>
  );
}

/*
 * Хештег-подсказки. Кнопка «#» раньше просто дописывала решётку — толку ноль.
 * Берём базовый набор из словаря + слова из названия проекта и чипов ролика
 * (фон / стиль субтитров / хук): это единственные осмысленные слова, которые
 * у фронта есть без похода в TikTok за трендами.
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
  return out.slice(0, 8);
}

export function TikTokPostPage() {
  const { t } = useTranslation();
  const { id } = useParams();
  const [params] = useSearchParams();
  const qaPost = import.meta.env.DEV ? params.get('qaPost') : null;
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
    const generated = projectQuery.data?.project.jobs?.flatMap((job) => job.videos).filter((v) => v.status === 'COMPLETED') ?? [];
    if (generated.length || !qaPost) return generated;
    return [1, 2].map((index) => ({
      id: `qa-video-${index}`,
      index,
      status: 'COMPLETED' as const,
      progress: 100,
      source: 'Ночной город',
      subtitleStyle: 'Brat',
      hook: 'Молния',
      thumbnailUrl: '/static/assets/cover-placeholder.svg',
      downloadUrl: `/qa/video-${index}.mp4`
    }));
  }, [projectQuery.data, qaPost]);

  // ?video=N — постинг конкретной строки из батча (иконка TikTok в строке, Figma W36)
  const explicitIndex = params.get('video');
  const [index, setIndex] = useState(() => Math.max(0, Number(explicitIndex ?? 0) || 0));
  // ссылка без ?video (например «Выложить» с дашборда) должна открывать первый НЕвыложенный
  // ролик, а не первый по списку. Один раз на загрузку батча — иначе после публикации
  // эффект перекидывал бы на следующий прямо из экрана «Видео в Тик-Токе».
  const autoPicked = useRef(false);
  const [stage, setStage] = useState<PostStage>(() => qaPost === 'uploading' ? 'uploading' : qaPost === 'posted' || qaPost === 'next' ? 'posted' : 'draft');
  const [showNext, setShowNext] = useState(qaPost === 'next');
  const [caption, setCaption] = useState(qaPost && qaPost !== 'empty' ? 'Новый сниппет уже в TikTok' : '');
  const [privacy, setPrivacy] = useState<Privacy | null>(qaPost && qaPost !== 'empty' ? 'all' : null);
  const [comments, setComments] = useState(true);
  const [duet, setDuet] = useState(true);
  // По умолчанию первый кадр (0) уже выбран как обложка → можно публиковать, не «дёргая» пикер.
  const [coverFrame, setCoverFrame] = useState<number | null>(qaPost && qaPost !== 'empty' ? 3 : 0);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [rights, setRights] = useState(Boolean(qaPost && qaPost !== 'empty'));
  const [postError, setPostError] = useState('');
  /*
   * 4.1: описание для каждого ролика батча писалось с нуля — на батче 5+ это главный тормоз.
   * Галка переносит описание, приватность и тумблеры на следующий ролик; подтверждение прав
   * НЕ переносим — по гайдлайнам TikTok его надо подтверждать на каждую публикацию.
   */
  const [applyToAll, setApplyToAll] = useState(false);
  const [tagsOpen, setTagsOpen] = useState(false);

  const video = videos[index];
  const postedCount = videos.filter(isVideoPosted).length;
  /* Текущий ролик считаем выложенным независимо от свежести кэша проекта: иначе сразу после
     публикации кнопка успевала показать «к следующему видео», хотя следующего уже нет. */
  const batchDone = videos.every((item, position) => position === index || isVideoPosted(item));
  const suggestions = hashtagSuggestions(
    t('tiktok.hashtagBase').split(','),
    [projectQuery.data?.project.name, video?.source, video?.hook],
    caption
  );
  // Раскадровка под пикер обложки: кадры приходят с бэка и кладутся в стейт запроса,
  // вместо восьми <video>, перематывающих один и тот же файл.
  const framesQuery = useQuery({
    queryKey: ['video-frames', video?.id, COVER_FRAME_COUNT],
    queryFn: () => api.videoFrames(video?.id ?? '', COVER_FRAME_COUNT),
    enabled: Boolean(video?.id) && video?.status === 'COMPLETED',
    staleTime: 5 * 60_000
  });

  // Требования гайдлайнов: без прав и без явного выбора приватности публиковать нельзя
  const valid = caption.trim().length > 0 && privacy !== null && coverFrame !== null && rights;

  /*
   * Чего не хватает для публикации. Раньше кнопка просто стояла серой: четыре независимых
   * условия и ни одной подсказки — юзер не понимал, что не выбрал приватность.
   */
  const missing = [
    !caption.trim().length && t('tiktok.needCaption'),
    privacy === null && t('tiktok.needPrivacy'),
    coverFrame === null && t('tiktok.needCover'),
    !rights && t('tiktok.needRights')
  ].filter(Boolean) as string[];

  useEffect(() => {
    if (autoPicked.current || explicitIndex !== null || videos.length === 0) return;
    autoPicked.current = true;
    const first = videos.findIndex((item) => !isVideoPosted(item));
    if (first > 0) setIndex(first);
  }, [explicitIndex, videos]);

  // W56 → W57: статус «Видео в Тик-Токе» сменяется кнопкой перехода к следующему ролику
  useEffect(() => {
    if (stage !== 'posted') return;
    const timer = setTimeout(() => setShowNext(true), POSTED_STATUS_MS);
    return () => clearTimeout(timer);
  }, [stage]);

  useEffect(() => {
    if (coverFrame === null) return;
    const element = videoRef.current;
    if (!element || !Number.isFinite(element.duration) || element.duration <= 0) return;
    element.currentTime = Math.min(element.duration - 0.05, (element.duration * coverFrame) / (COVER_FRAME_COUNT - 1));
  }, [coverFrame, video?.id]);

  useEffect(() => {
    if (creatorQuery.data?.comment_disabled) setComments(false);
    if (creatorQuery.data?.duet_disabled) setDuet(false);
  }, [creatorQuery.data?.comment_disabled, creatorQuery.data?.duet_disabled]);

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
          <video
            ref={videoRef}
            src={video.downloadUrl ?? undefined}
            poster={video.thumbnailUrl ?? undefined}
            muted
            playsInline
            preload="metadata"
            onLoadedMetadata={(event) => {
              if (coverFrame === null || !Number.isFinite(event.currentTarget.duration)) return;
              event.currentTarget.currentTime = Math.min(event.currentTarget.duration - 0.05, (event.currentTarget.duration * coverFrame) / (COVER_FRAME_COUNT - 1));
            }}
            className="h-full w-full object-cover"
          />
        )}
        </div>
      </div>
    );
    return <FullscreenZone responsiveScale onCollapse={() => navigate(`/app/projects/${id}`)} left={connect} right={preview} />;
  }

  const submit = async () => {
    if (!valid || stage !== 'draft') return;
    setStage('uploading');
    setPostError('');
    try {
      const initialized = await api.postTiktok({
        projectId: id,
        videoId: video?.id,
        caption: caption.trim(),
        privacy,
        comments,
        duet,
        cover: coverFrame !== null,
        coverFrame,
        coverTimestampMs: coverFrame !== null && videoRef.current && Number.isFinite(videoRef.current.duration)
          ? Math.max(0, Math.round((videoRef.current.duration * 1000 * coverFrame) / (COVER_FRAME_COUNT - 1)))
          : 0,
        rights
      });
      if (initialized.status !== 'PUBLISH_COMPLETE') {
        let complete = false;
        for (let attempt = 0; attempt < 40; attempt += 1) {
          await new Promise((resolve) => window.setTimeout(resolve, 1500));
          const current = await api.tiktokPostStatus(initialized.publishId);
          if (current.status === 'PUBLISH_COMPLETE') {
            complete = true;
            break;
          }
          if (current.status === 'FAILED') throw new Error(current.fail_reason || t('tiktok.postError'));
        }
        if (!complete) throw new Error(t('tiktok.postError'));
      }
      setStage('posted');
      // без этого «выложено N из M» и пропуск уже выложенных считались по устаревшему проекту
      queryClient.invalidateQueries({ queryKey: ['project', id] });
    } catch (error) {
      setStage('draft');
      setPostError(error instanceof Error ? error.message : t('tiktok.postError'));
    }
  };

  /** W58: следующий ролик — форма сбрасывается (или переносится целиком, если стоит «применить ко всем»).
      Уже опубликованные ролики пропускаем (после «Выложить все» не предлагаем их снова). */
  const nextVideo = () => {
    let next = index + 1;
    while (next < videos.length && isVideoPosted(videos[next])) next += 1;
    if (next >= videos.length) {
      /*
       * Выложен последний ролик батча — ведём в аналитику, а не в список проектов.
       * Дальше человеку нужен вывод «что прострелило», а не витрина: в списке он всё равно
       * шёл искать статистику руками. Проект передаём явно — разбор считается по проекту.
       */
      navigate(batchDone ? `/app/stats?project=${id}` : `/app/projects/${id}`);
      return;
    }
    setIndex(next);
    setStage('draft');
    setShowNext(false);
    setCoverFrame(0);
    setTagsOpen(false);
    setPostError('');
    if (!applyToAll) {
      setCaption('');
      setPrivacy(null);
    }
    // права подтверждаем на каждый ролик отдельно — требование гайдлайнов TikTok
    setRights(false);
  };

  const user = meQuery.data?.user;
  const handle = meQuery.data?.tiktok?.handle ?? user?.artistNick ?? user?.name ?? '';

  const left = (
    <div className="card-2 no-scrollbar flex h-full flex-col overflow-y-auto px-[28px] pb-[40px] pt-[28px]">
      {/* аккаунт (Figma 764:3888): аватар 40 + @ник. Справа — прогресс выкладки батча:
          при пяти роликах человек терял счёт, какой он сейчас публикует. */}
      <div className="flex h-[40px] shrink-0 items-center gap-[16px]">
        <span className="h-[40px] w-[40px] shrink-0 overflow-hidden rounded-full bg-accent-20">
          {user?.avatarUrl && <img src={user.avatarUrl} alt="" className="h-full w-full object-cover" />}
        </span>
        <span className="min-w-0 flex-1 truncate text-[24px] font-[350] leading-none text-text">@{handle}</span>
        {videos.length > 1 && (
          <span
            className="shrink-0 whitespace-nowrap rounded-[10px] bg-grad-soft-20 px-[10px] py-[6px] text-[14px] leading-none text-text-80"
            title={t('tiktok.videoOfBatch', { n: index + 1, total: videos.length })}
          >
            {t('tiktok.batchProgress', { done: postedCount, total: videos.length })}
          </span>
        )}
      </div>

      {/* описание 334×147 r15 grad-soft-10 + кнопка «#» 30×30 r5 в правом нижнем углу */}
      <div className="relative mt-[20px] h-[147px] shrink-0 rounded-r15 bg-grad-soft-10 p-[20px] transition focus-within:shadow-[inset_0_0_0_1px_var(--accent-light)]">
        <textarea
          value={caption}
          onChange={(e) => setCaption(e.target.value)}
          readOnly={stage !== 'draft'}
          placeholder={t('tiktok.captionPlaceholder')}
          aria-label={t('tiktok.caption')}
          className={cn('no-scrollbar h-full w-full resize-none rounded-r10 bg-transparent pr-[40px] text-[16px] leading-normal text-text outline-none placeholder:text-text', stage !== 'draft' && 'cursor-default')}
        />
        {/* после нажатия «Выложить» описание больше не редактируется */}
        {stage === 'draft' && (
          <>
            <button
              type="button"
              onClick={() => setTagsOpen((open) => !open)}
              aria-label={t('tiktok.addHashtag')}
              aria-expanded={tagsOpen}
              className={cn(
                'absolute bottom-[20px] right-[20px] h-[30px] w-[30px] rounded-[5px] bg-grad-soft-20 text-[16px] leading-none text-text-80 transition hover:text-text',
                tagsOpen && 'text-text shadow-[inset_0_0_0_1px_var(--accent-light)]'
              )}
            >
              #
            </button>
            {/* подсказки хештегов: свои слова (название трека, фон, хук) + базовый набор */}
            {tagsOpen && (
              <div className="absolute bottom-[58px] right-0 z-[3] w-[334px] rounded-r10 bg-[#2b2145] p-[14px] shadow-soft">
                <p className="text-[13px] leading-none text-text-60">{t('tiktok.hashtagsTitle')}</p>
                <div className="mt-[10px] flex flex-wrap gap-[8px]">
                  {suggestions.length === 0 && <span className="text-[14px] leading-none text-text-60">{t('tiktok.hashtagsEmpty')}</span>}
                  {suggestions.map((tag) => (
                    <button
                      key={tag}
                      type="button"
                      onClick={() => setCaption((c) => `${c.replace(/\s+$/, '')}${c.trim() ? ' ' : ''}#${tag} `)}
                      className="rounded-[5px] bg-grad-soft-20 px-[10px] py-[6px] text-[14px] leading-none text-text-80 transition hover:text-text"
                    >
                      #{tag}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* перенос настроек на весь батч — главный ускоритель выкладки */}
      {videos.length > 1 && (
        <div className="mt-[14px] flex h-[20px] shrink-0 items-center">
          <RightsCheckbox checked={applyToAll} onChange={setApplyToAll} label={t('tiktok.applyToAll')} size={16} />
          <span className="ml-[8px] shrink-0 cursor-help text-[13px] leading-none text-text-40" title={t('tiktok.applyToAllHint')} aria-hidden="true">?</span>
        </div>
      )}

      {/* приватность 334×175 — без предвыбранного значения (требование TikTok).
          Отступы симметричны (padding 20 сверху/снизу, зазор заголовок↔список ≈ 18):
          leading-[19px] у заголовка не даёт 2-й строке раздувать блок и толкать список вниз. */}
      <div className="mt-[20px] flex h-[157px] shrink-0 flex-col rounded-r15 bg-grad-soft-10 p-[20px]" role="radiogroup" aria-label={t('tiktok.privacyTitle')}>
        <p className="w-[228px] text-[16px] leading-[19px] text-text">{t('tiktok.privacyTitle')}</p>
        <div className="mt-[18px] flex flex-col gap-[10px]">
          {PRIVACY_ORDER.map((value) => (
            (() => {
              const apiValue = value === 'all' ? 'PUBLIC_TO_EVERYONE' : value === 'friends' ? 'MUTUAL_FOLLOW_FRIENDS' : 'SELF_ONLY';
              const unavailable = Boolean(creatorQuery.data?.privacy_level_options?.length) && !creatorQuery.data!.privacy_level_options.includes(apiValue);
              return (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={privacy === value}
              onClick={() => !unavailable && setPrivacy(value)}
              disabled={unavailable}
              className={cn('flex h-[20px] items-center justify-between text-[16px] leading-none text-text-80 transition hover:text-text', unavailable && 'cursor-not-allowed opacity-35')}
            >
              {t(`tiktok.privacy.${value}`)}
              <PrivacyRadio checked={privacy === value} />
            </button>
              );
            })()
          ))}
        </div>
      </div>

      {/* настройки конфиденциальности 334×146 */}
      <div className="relative mt-[20px] h-[134px] shrink-0 rounded-r15 bg-grad-soft-10 p-[20px]">
        <p className="text-[16px] leading-none text-text">{t('tiktok.privacySettings')}</p>
        <div className="mt-[20px] flex flex-col gap-[17px]">
          <div className="flex items-start justify-between gap-[10px]">
            <span className="flex items-start gap-[10px]">
              <span className="relative mt-px h-[12px] w-[15px] shrink-0 overflow-hidden"><img src="/assets/figma/tt-privacy-icons.svg" width="15" height="44" alt="" aria-hidden className="absolute left-0 top-0 max-w-none" /></span>
              <span className="text-[16px] leading-none text-text-80">{t('tiktok.allowComments')}</span>
            </span>
            <MiniToggle checked={creatorQuery.data?.comment_disabled ? false : comments} onChange={setComments} label={t('tiktok.allowComments')} />
          </div>
          <div className="flex items-start justify-between gap-[10px]">
            <span className="flex items-start gap-[10px]">
              <span className="relative mt-px h-[18px] w-[15px] shrink-0 overflow-hidden"><img src="/assets/figma/tt-privacy-icons.svg" width="15" height="44" alt="" aria-hidden className="absolute left-0 top-[-26px] max-w-none" /></span>
              <span className="w-[199px] text-[16px] leading-[19px] text-text-80">{t('tiktok.allowDuet')}</span>
            </span>
            <MiniToggle checked={creatorQuery.data?.duet_disabled ? false : duet} onChange={setDuet} label={t('tiktok.allowDuet')} />
          </div>
        </div>
      </div>

      {/* Время публикации 334×39. Поля «21:00 | 15/07» были обманом: бэк их не читал,
          а TikTok Content Posting API отложенной публикации не даёт — ролик уходит сразу.
          Оставили честную строку-статус вместо редактируемого расписания. */}
      <div className="mt-[20px] flex h-[39px] shrink-0 items-center justify-between rounded-r15 bg-grad-soft-10 px-[20px]" title={t('tiktok.publishNowHint')}>
        <span className="text-[16px] leading-none text-text-80">{t('tiktok.publishTime')}</span>
        <span className="text-[16px] leading-none text-text">{t('tiktok.publishNow')}</span>
      </div>

      <div className="mt-[20px] shrink-0">
        <RightsCheckbox checked={rights} onChange={setRights} label={t('tiktok.rights')} />
        {postError && <p role="alert" className="mt-[10px] text-[13px] leading-[16px] text-[#ff8f9a]">{postError}</p>}
      </div>
    </div>
  );

  const right = (
    <div className="flex h-full flex-col">
      <div className="group relative h-[665px] shrink-0 overflow-hidden rounded-r15 bg-grad-soft-10">
        {video?.thumbnailUrl && <img src={video.thumbnailUrl} alt="" className="h-full w-full object-cover" />}

        {stage !== 'posted' && (
          <span className="absolute left-1/2 top-1/2 flex h-[60px] w-[60px] -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-[rgba(5,1,15,0.6)]">
            <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true"><path d="M6 3.5v13l11-6.5L6 3.5Z" fill="#f6f5fd" /></svg>
          </span>
        )}

        {/* W52: «Выбери обложку» → W54: «Обложка выбрана»; 293×60 r15, отступы 40 */}
        {stage === 'draft' && (
          <div className="absolute inset-x-[40px] bottom-[40px]">
            <CoverPicker src={video?.downloadUrl} poster={video?.thumbnailUrl} frames={framesQuery.data?.frames} value={coverFrame} onChange={setCoverFrame} />
          </div>
        )}

        {/* W56: ссылка на опубликованное видео */}
        {stage === 'posted' && (
          <a
            href={video?.downloadUrl ?? '#'}
            target="_blank"
            rel="noreferrer"
            className="absolute left-1/2 top-1/2 flex h-[45px] w-[208px] -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-r15 bg-accent-light pb-px text-[24px] font-[350] leading-none text-text transition hover:brightness-110"
          >
            {t('tiktok.openVideo')}
          </a>
        )}
      </div>

      {/* кнопка состояния 373×60 r15 grad-soft-20; активная — border 1px accent (W52 → W54) */}
      {showNext ? (
        <button
          type="button"
          onClick={nextVideo}
          className="mt-[20px] flex h-[60px] shrink-0 items-center justify-center gap-[16px] rounded-r15 border border-accent-light bg-grad-soft-20 text-[24px] font-[350] leading-none text-text-80 transition hover:text-text"
        >
          {batchDone ? t('tiktok.toStats') : t('tiktok.nextVideo')}
          <img src="/assets/figma/pd-arrow-right.svg" width="25" height="15" alt="" aria-hidden />
        </button>
      ) : (
        <>
          <button
            type="button"
            onClick={submit}
            disabled={stage !== 'draft' || !valid}
            aria-describedby={missing.length ? 'publish-missing' : undefined}
            title={missing.length ? `${t('tiktok.needTitle')} ${missing.join(', ')}` : undefined}
            className={cn(
              'mt-[20px] flex h-[60px] shrink-0 items-center justify-center gap-[12px] rounded-r15 bg-grad-soft-20 text-[24px] font-[350] leading-none text-text-80 transition',
              stage === 'draft' && valid && 'border border-accent-light hover:text-text',
              stage === 'draft' && !valid && 'cursor-not-allowed'
            )}
          >
            <img
              src={stage === 'draft' ? '/assets/figma/tt-publish-arrow.svg' : stage === 'uploading' ? '/assets/figma/tt-uploading.svg' : '/assets/figma/tt-posted.svg'}
              width="30"
              height="30"
              alt=""
              aria-hidden
            />
            {stage === 'draft' ? t('tiktok.publish') : stage === 'uploading' ? t('tiktok.uploading') : t('tiktok.posted')}
          </button>
          {stage === 'draft' && missing.length > 0 && (
            <p id="publish-missing" className="mt-[10px] shrink-0 text-center text-[15px] leading-[19px] text-text-60">
              {t('tiktok.needTitle')} {missing.join(', ')}
            </p>
          )}
        </>
      )}
    </div>
  );

  return <FullscreenZone responsiveScale onCollapse={() => navigate(`/app/projects/${id}`)} left={left} right={right} />;
}
