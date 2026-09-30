import { WarmupInput } from './WarmupInput';
import { ChangeEvent, CSSProperties, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { useChip } from '../../i18n/useChip';
import { api } from '../../lib/api';
import { cn } from '../../lib/cn';
import { SvgMaskIcon } from '../layout/SvgMaskIcon';
import { useDragScroll } from './useDragScroll';
import { PillsFooter } from './WizardFrame';
import { HookConfig, HookKind, HOOK_LABELS, hookComplete, hookPills, useWizardStore } from '../../stores/wizardStore';
import { dropToSeconds, normalizeDropTime, timingToSeconds } from './useFragmentAudio';
import { ActionGuideOverlay } from '../guidance/ActionGuideOverlay';
import { useGuideDismiss, useMarkGuideSeen } from '../guidance/useGuideDismiss';
import { useGuideLiveDismissed } from '../guidance/guideLiveState';
import { useScrollGuideIntoView } from '../guidance/useScrollGuideIntoView';
import { fxLabGuideId, LabTypeList, useFxLab, useFxLabTourProgress } from './FxLab';
import {
  ChipRow, configuredPreviewId, EffectPreview, HOOK_TYPES, hookSteps, SlowShutterExtendToggle, StyleScopeToggle, selectedStyles, toggleStyle
} from './hookCatalog';


/*
 * Этап «Хук» (Figma W18 → W24/32 → W25/34 → W26/28/29/30 → W27 → W31):
 * единый тайминг дропа (чипы во всю высоту панели), строки типов 620×80 со скроллом
 * под градиентный оверлей, «?»-подсказки плашками внутри строк (тексты из макета),
 * настройка в рабочей зоне; «Эффекты» — шаги с подтверждением галочкой.
 */

function maskTiming(raw: string): string {
  const digits = raw.replace(/\D/g, '').slice(0, 6);
  return digits.replace(/(\d{2})(?=\d)/g, '$1:');
}

/** Свой тайминг дропа не может превышать длительность трека (правка ревью) */
function clampDrop(value: string, durationS?: number): string {
  const masked = maskTiming(value);
  if (!durationS) return masked;
  const m = /^(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(masked);
  if (!m) return masked;
  const sec = Number(m[1]) * 60 + Number(m[2]) + Number(m[3] ?? 0) / 100;
  if (sec <= durationS) return masked;
  const mm = String(Math.floor(durationS / 60)).padStart(2, '0');
  const ss = String(Math.floor(durationS % 60)).padStart(2, '0');
  return `${mm}:${ss}`;
}

/** Мини-визуал первой подсказки хука: тайминг дропа — точка на дорожке. */
/**
 * Переиспользует визуальный язык самой первой подсказки (тайминг отрывка на треке):
 * два тайминг-пила + тонкая шкала. Волну убрал — не читалась. Здесь не диапазон,
 * а ОДНА точка (момент дропа) — вместо отдельной палочки+подписи сам пил «drop»
 * сидит прямо на шкале и служит ручкой: он ближе к началу и он же дёргается
 * (тот же принцип, что и в тайминге трека: анимируем ровно то, что тянут).
 */
function HookDropGuideVisual() {
  return (
    <div className="flex w-full items-center justify-center gap-[9px]" aria-hidden="true">
      <span className="flex h-[38px] w-[58px] shrink-0 items-center justify-center whitespace-nowrap rounded-[8px] bg-white/[0.1] text-[15px] font-[400] leading-none text-white">
        <span className="action-guide-optical-text action-guide-timing-text">00:14</span>
      </span>
      <span className="relative h-[4px] min-w-0 flex-1 rounded-full bg-white/25">
        <span className="guide-track-drop-pill action-guide-optical-text absolute left-[26%] top-1/2 whitespace-nowrap rounded-[5px] bg-accent-light px-[7px] py-[3px] text-[8px] font-[400] text-white">drop</span>
      </span>
      <span className="flex h-[38px] w-[58px] shrink-0 items-center justify-center whitespace-nowrap rounded-[8px] bg-white/[0.1] text-[15px] font-[400] leading-none text-white">
        <span className="action-guide-optical-text action-guide-timing-text">00:20</span>
      </span>
    </div>
  );
}

/** Мини-визуал третьей подсказки хука: превью слева, «настройки» строчками справа. */
/** Точь-в-точь композиция SubtitleStyleGuideVisual (3 карточки, средняя выбрана),
 * только вместо иконки «T» — иконка стиля: и там, и там про выбор варианта из ряда. */
function HookWorkzoneGuideVisual() {
  // Три РАЗНЫЕ иконки — ровно то, что перечислено в тексте подсказки (склейка/стиль/превью),
  // а не одна и та же картинка трижды.
  const icons = ['/assets/figma/combo-transition.svg', '/assets/figma/combo-style.svg', '/assets/figma/btn-play.svg'];
  return (
    <div className="grid w-full grid-cols-3 gap-[7px]" aria-hidden="true">
      {icons.map((src, index) => (
        <span
          key={src}
          className={cn(
            'guide-mode-reveal relative flex h-[46px] items-center justify-center overflow-hidden rounded-[8px] bg-gradient-to-b from-[#42335e] to-[#181126]',
            index === 0 ? 'guide-mode-delay-1' : index === 1 ? 'guide-mode-delay-2' : 'guide-mode-delay-3',
            index === 1 && 'ring-2 ring-inset ring-accent-light'
          )}
        >
          <img src={src} width="18" height="18" alt="" className="opacity-90" />
        </span>
      ))}
    </div>
  );
}

/**
 * Мини-визуал «Выбери тип хука»: центральный пил — сплошной (не полупрозрачный,
 * иначе боковые пилы под ним просвечивают) и крутит ТРИ иконки кросс-фейдом
 * (guide-hook-icon-a/b/c, один кадр-keyframe + отрицательные animation-delay —
 * без дублирования кейфреймов), показывая, что слот подставляет РАЗНЫЕ типы хука.
 */
function HookTypeGuideVisual() {
  return (
    <div className="flex w-full items-center justify-center" aria-hidden="true">
      <span className="guide-mode-reveal guide-mode-delay-1 relative z-[1] mr-[-14px] flex h-[52px] w-[44px] shrink-0 items-center justify-center rounded-[11px] bg-white/[0.06] opacity-55">
        <SvgMaskIcon src="/assets/figma/hook-sound.svg" style={{ width: 12, height: 14, color: 'rgba(255,255,255,.7)' }} />
      </span>
      {/*
        Три слоя, каждый со своим transform-заданием (иначе бегущая анимация просто
        затирает предыдущую на том же свойстве):
        1) внешний — только позиционирование в ряду + одноразовое появление;
        2) средний — ВИДИМЫЙ пил (фон/тень/рамка) — это он трясётся, не иконка;
        3) внутренний слой иконок — только opacity-кроссфейд, transform не трогает.
      */}
      <span className="guide-mode-reveal guide-mode-delay-2 relative z-[2] flex h-[66px] w-[60px] shrink-0 items-center justify-center">
        <span className="guide-hook-shake relative flex h-full w-full items-center justify-center overflow-hidden rounded-[14px] bg-[#6850b7] shadow-[0_10px_22px_rgba(5,1,15,.4),inset_0_0_0_1.5px_var(--accent-light)]">
          <span className="guide-hook-icon-a absolute inset-0 flex items-center justify-center">
            <SvgMaskIcon src="/assets/figma/icon-bolt.svg" style={{ width: 15, height: 18, color: '#fff' }} />
          </span>
          <span className="guide-hook-icon-b absolute inset-0 flex items-center justify-center">
            <SvgMaskIcon src="/assets/figma/hook-object.svg" style={{ width: 20, height: 20, color: '#fff' }} />
          </span>
          <span className="guide-hook-icon-c absolute inset-0 flex items-center justify-center">
            <SvgMaskIcon src="/assets/figma/hook-effects.svg" style={{ width: 20, height: 20, color: '#fff' }} />
          </span>
        </span>
      </span>
      <span className="guide-mode-reveal guide-mode-delay-3 relative z-[1] ml-[-14px] flex h-[52px] w-[44px] shrink-0 items-center justify-center rounded-[11px] bg-white/[0.06] opacity-55">
        <SvgMaskIcon src="/assets/figma/hook-thought.svg" style={{ width: 12, height: 13, color: 'rgba(255,255,255,.7)' }} />
      </span>
    </div>
  );
}

export function StageHooks() {
  const { t } = useTranslation();
  const chip = useChip();
  const hooks = useWizardStore((state) => state.hooks);
  const setHooks = useWizardStore((state) => state.setHooks);
  const clearHook = useWizardStore((state) => state.clearHook);
  const track = useWizardStore((state) => state.track);
  const timingFrom = useWizardStore((state) => state.timingFrom);
  const timingTo = useWizardStore((state) => state.timingTo);
  const dropGuideTargetRef = useRef<HTMLDivElement>(null);
  const typeGuideTargetRef = useRef<HTMLDivElement>(null);
  // typeGuideDismissed объявлен первым: idle-условие drop-гайда («мы ещё не ушли
  // дальше по цепочке») на него ссылается. visible=false у type: пререквизит
  // «drop уже закрыт» тут не собрать (dropGuideDismissed объявлен НИЖЕ) — иначе
  // при уже заданном dropTime (у юзера с готовой задачей) оба гайда, drop и
  // type, всплыли бы разом — раньше их разводило только значение dropTime
  // (пусто/не пусто), теперь оба не гейтятся задачей и без явной
  // последовательности пересекаются. Показ отмечаем отдельно после
  // showTypeGuide (см. ниже), когда dropGuideDismissed уже посчитан.
  // Режим вариантов FX (по умолчанию): тот же дроп и тип, но как шаги 1–2 его общего тура
  // (свои id и нумерация — см. FX_LAB_TOUR).
  const fxLab = useFxLab();
  const labProgress = useFxLabTourProgress();
  const typeGuideId = fxLab ? fxLabGuideId('type') : 'hook-type';
  const dropGuideId = fxLab ? fxLabGuideId('drop') : 'hook-drop';
  // Вариантов прототипа в hooks.kind нет — «тип ещё не выбран» там = ни одного варианта.
  const labVariantCount = useWizardStore((state) => state.fxVariants.filter((v) => !v.draft).length);
  const [typeGuideDismissed, setTypeGuideDismissed] = useGuideDismiss(typeGuideId, Boolean(hooks.dropTime) && (fxLab ? labVariantCount === 0 : !hooks.kind), false);
  const [dropGuideDismissed, setDropGuideDismissed] = useGuideDismiss(dropGuideId, !hooks.dropTime && !typeGuideDismissed, true);
  const showDropGuide = !dropGuideDismissed;
  // Четвёртый шаг (кнопка «Таймлайн») есть только там, где есть сама кнопка.
  const hookGuideTotal = 3;
  const showTypeGuide = Boolean(hooks.dropTime) && dropGuideDismissed && !typeGuideDismissed;
  useMarkGuideSeen(typeGuideId, Boolean(hooks.dropTime) && dropGuideDismissed);
  // В прототипе шаг закрывается самим действием, как в туре таймлайна: выбрал дроп
  // (сменил, а не пришёл с уже выбранным) — шаг 1 пройден; завёл первый вариант — шаг 2.
  const prevDropRef = useRef(hooks.dropTime);
  const prevVariantCountRef = useRef(labVariantCount);
  useEffect(() => {
    if (fxLab && showDropGuide && hooks.dropTime && hooks.dropTime !== prevDropRef.current) setDropGuideDismissed(true);
    if (fxLab && showTypeGuide && labVariantCount > prevVariantCountRef.current) setTypeGuideDismissed(true);
    prevDropRef.current = hooks.dropTime;
    prevVariantCountRef.current = labVariantCount;
  });
  useScrollGuideIntoView(showDropGuide, dropGuideTargetRef);
  useScrollGuideIntoView(showTypeGuide, typeGuideTargetRef);
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: 15_000 });
  // Окно отрывка — часть ключа: выбрал другой кусок трека → другие кандидаты дропа.
  // Сохранённый режим тайминга может быть старым, поэтому готовность определяют сами
  // валидные границы — ровно те значения, которые отправляются в API.
  const clipFromS = timingToSeconds(timingFrom);
  const clipToS = timingToSeconds(timingTo);
  const clipReady = clipFromS !== null && clipToS !== null && clipToS > clipFromS;
  const dropsQuery = useQuery({
    queryKey: ['drops', track?.id, timingFrom, timingTo],
    queryFn: () => api.drops(track!.id, timingFrom, timingTo),
    enabled: meQuery.isSuccess && Boolean(meQuery.data.capabilities?.analyzedDrops) && Boolean(track) && clipReady,
  });
  const [customDrop, setCustomDrop] = useState(false);
  const [dropError, setDropError] = useState(false);
  const [hint, setHint] = useState<HookKind | null>(null);
  // Список типов листается сам: края тают там, где за ними есть ещё строки
  const [typeFade, setTypeFade] = useState({ top: false, bottom: false });
  const syncTypeFade = () => {
    const el = typeGuideTargetRef.current;
    if (!el) return;
    const next = { top: el.scrollTop > 2, bottom: el.scrollTop + el.clientHeight < el.scrollHeight - 2 };
    setTypeFade((prev) => (prev.top === next.top && prev.bottom === next.bottom ? prev : next));
  };
  // высота списка меняется после первого замера (раскрылся тип, добавился вариант) — сверяемся после каждой отрисовки
  useEffect(syncTypeFade);
  useEffect(() => {
    const el = typeGuideTargetRef.current;
    if (!el) return undefined;
    syncTypeFade();
    // высота списка меняется при раскрытии типа и добавлении вариантов — следим за содержимым
    const observer = new ResizeObserver(syncTypeFade);
    observer.observe(el);
    if (el.firstElementChild) observer.observe(el.firstElementChild);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Кандидаты ВНЕ отрывка не предлагаем: выбрав такой, человек упирался в неактивное
  // «Продолжить» без объяснения (dropReady в визарде требует дроп внутри окна).
  const drops = (dropsQuery.data?.drops ?? []).filter((d) => {
    const s = dropToSeconds(normalizeDropTime(d.time));
    return s !== null && clipFromS !== null && clipToS !== null && s >= clipFromS && s <= clipToS;
  });
  // Сохранённый дроп вылетел из окна (окно поменяли после выбора) — говорим об этом сразу
  const storedDropS = dropToSeconds(hooks.dropTime);
  const storedDropOutside = storedDropS !== null && clipReady && (storedDropS < clipFromS! || storedDropS > clipToS!);
  // В сторе тайминг всегда трёхчастный, в списке — «mm:ss»: сравниваем в одной форме
  const customActive = Boolean(hooks.dropTime && !drops.some((d) => normalizeDropTime(d.time) === hooks.dropTime));
  // Пока анализ идёт, ряд занимают заглушки: иначе человек видит один «Свой вариант»
  // и уходит вписывать тайминг руками, не дождавшись кандидатов.
  const dropsLoading = drops.length === 0 && clipReady && dropsQuery.isFetching;

  return (
    <>
      <div className="w12-sec">
        <div className="w12-sec-head">
          <h2>
            <span className="w12-mi w12-cap w12-heavy w12-ttl-ic" aria-hidden="true" style={{ '--m': 'url(/assets/wizard/ic-bolt.svg)', '--r': 0.65 } as CSSProperties} />
            <span className="w12-l">{t('wizard.fx.dropTitle')}</span>
          </h2>
        </div>
        {/*
          Пока кандидатов нет, ряд состоит из одной кнопки «свой тайминг», и это
          читается как сломанный экран. Строка объясняет, ЧТО происходит:
          считаем / нужен отрывок / анализ не удался — во всех случаях руками
          тайминг ввести можно, и это надо сказать вслух.
        */}
        {drops.length === 0 && (
          <p className="w12-sec-note">
            {!clipReady ? t('wizard.fx.dropNeedsClip') : dropsQuery.isFetching ? t('wizard.fx.dropAnalyzing') : t('wizard.fx.dropManualOnly')}
          </p>
        )}
        {/* Тайминг дропа — сегменты, как режимы шага «Фон»; последний — свой тайминг */}
        <div ref={dropGuideTargetRef} className="w12-drops">
          {dropsLoading && [0, 1, 2].map((index) => <span key={index} className="w12-drop-opt w12-skel" aria-hidden="true"><i /></span>)}
          {drops.map((drop) => (
            <button
              key={drop.time}
              type="button"
              className="w12-drop-opt"
              aria-pressed={hooks.dropTime === normalizeDropTime(drop.time)}
              onClick={() => { setDropError(false); setCustomDrop(false); setHooks({ dropTime: normalizeDropTime(drop.time) }); }}
            >
              <span className="w12-l w12-num">{drop.time}</span>
              <small className="w12-num">{Math.round(drop.confidence * 100)}%{drop.best ? ' ★' : ''}</small>
            </button>
          ))}
          {customDrop ? (
            <input
              autoFocus
              className="w12-drop-opt w12-drop-input w12-num"
              placeholder="00:00:00"
              aria-label={t('wizard.fx.customDrop')}
              defaultValue={customActive ? hooks.dropTime : ''}
              onChange={(e: ChangeEvent<HTMLInputElement>) => { e.target.value = clampDrop(e.target.value, track?.durationS); }}
              onBlur={(e) => {
                const value = clampDrop(e.target.value, track?.durationS);
                // Пустое поле — осознанный сброс дропа (стереть и начать заново), а не опечатка:
                // раньше пустое значение просто игнорировалось и старый дроп молча оставался
                // в сторе — стереть тайминг из интерфейса было нечем.
                if (!value) {
                  setDropError(false);
                  setHooks({ dropTime: '' });
                  setCustomDrop(false);
                  return;
                }
                const seconds = dropToSeconds(value);
                const valid = seconds !== null && clipFromS !== null && clipToS !== null && seconds >= clipFromS && seconds <= clipToS;
                setDropError(!valid);
                if (valid) setHooks({ dropTime: value });
                setCustomDrop(false);
              }}
              onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
            />
          ) : (
            <button type="button" className="w12-drop-opt w12-drop-custom" aria-pressed={customActive} onClick={() => setCustomDrop(true)}>
              <span className="w12-l w12-num">{customActive ? hooks.dropTime : t('wizard.fx.customDrop')}</span>
            </button>
          )}
        </div>
        {(dropError || storedDropOutside) && <p role="alert" className="w12-miss">{t('wizard.fx.dropOutsideClip')}</p>}
      </div>

      <ActionGuideOverlay
        open={showDropGuide}
        targetRef={dropGuideTargetRef}
        title={t('wizard.fx.guideDropTitle')}
        text={t('wizard.fx.guideDropText')}
        dismissLabel={t('wizard.fx.guideNext')}
        progressLabel={fxLab ? labProgress('drop') : t('wizard.guideProgress', { current: 1, total: hookGuideTotal })}
        onDismiss={() => setDropGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<HookDropGuideVisual />}
      />

      {/* Типы занимают остаток карточки и листаются сами — дроп и заголовок стоят на месте */}
      <div className="w12-sec w12-fill">
        <div className="w12-sec-head">
          <h2><span className="w12-l">{t('wizard.fx.typeTitle')}</span></h2>
        </div>
        <div ref={typeGuideTargetRef} className="w12-fx-scroll" data-fade-t={typeFade.top || undefined} data-fade-b={typeFade.bottom || undefined} onScroll={syncTypeFade}>
          {fxLab ? <LabTypeList locked={!hooks.dropTime} /> : (
            <div className="w12-fx-types">
              {HOOK_TYPES.map((item) => {
                const active = hooks.kind === item.kind;
                const configured = hookPills(hooks).some((pill) => pill.kind === item.kind);
                // «Без хука» не использует дроп, поэтому его можно настроить сразу.
                const locked = !hooks.dropTime && item.kind !== 'none';
                return (
                  <div key={item.kind} className={cn('w12-fx-type', (active || configured) && 'w12-on', active && 'w12-open', locked && 'w12-locked')}>
                    <button
                      type="button"
                      disabled={locked}
                      className="w12-fx-head"
                      // Повторный клик по выбранному/настроенному типу снимает его: раньше хук,
                      // раз попав в подсветку, отцепиться уже не мог и уезжал в генерацию.
                      onClick={() => { if (active || configured) clearHook(item.kind); else setHooks({ kind: item.kind }); }}
                      aria-pressed={active || configured}
                    >
                      <SvgMaskIcon src={item.icon} className="w12-fx-ic" style={{ width: item.iconW, height: item.iconH }} />
                      <span className="w12-fx-name w12-l">{chip(HOOK_LABELS[item.kind])}</span>
                      <span
                        role="button"
                        tabIndex={0}
                        className="w12-help-dot"
                        onMouseEnter={() => setHint(item.kind)}
                        onMouseLeave={() => setHint(null)}
                        onClick={(e) => { e.stopPropagation(); setHint(hint === item.kind ? null : item.kind); }}
                        aria-label={t('wizard.fx.whatIs', { label: chip(HOOK_LABELS[item.kind]) })}
                      >
                        <span className="w12-l">?</span>
                      </span>
                    </button>
                    {hint === item.kind && <span className="w12-fx-hint">{t(item.hint)}</span>}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      <ActionGuideOverlay
        open={showTypeGuide}
        targetRef={typeGuideTargetRef}
        title={t('wizard.fx.guideTypeTitle')}
        text={t('wizard.fx.guideTypeText')}
        dismissLabel={t('wizard.fx.guideNext')}
        progressLabel={fxLab ? labProgress('type') : t('wizard.guideProgress', { current: 2, total: hookGuideTotal })}
        onDismiss={() => setTypeGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<HookTypeGuideVisual />}
      />
    </>
  );
}

const KIND_ORDER: HookKind[] = ['warmup', 'object', 'effects', 'motion', 'thought', 'none'];

/** Следующий ЕЩЁ НЕ настроенный тип хука — им и оперирует «+» (как в футере визарда). */
function nextFreeKind(hooks: { configs: Partial<Record<HookKind, HookConfig>> }, current?: HookKind): HookKind | undefined {
  return KIND_ORDER.find((k) => k !== current && !hookComplete(k, hooks.configs[k]));
}

/**
 * Фуллскрин FX-зона (Figma W40): та же начинка, пересобранная под широкий экран.
 * Футер уехал в хедер: «Набор эффектов» = пилюли настроенных FX (60px, как в футере) + «+».
 * ПЕРВЫЙ контейнер — настройка ТЕКУЩЕГО типа хука (f1..f5: звук/объект/FX/движение/мысль),
 * два нижних — склейка и стилизация. Склейка/стиль пишутся в конфиг ТЕКУЩЕГО хука,
 * то есть у каждого хука своя пара — это и даёт уникальность вариаций.
 * Геометрия: контейнеры 390×160 и 390×175 (шаг 195), плеер 373×665 + «Продолжить» 373×60.
 */

export function HooksWorkZone({ ready, canContinue, loading, onBack, onNext }: { ready: boolean; canContinue: boolean; loading?: boolean; onBack: () => void; onNext: () => void }) {
  const { t } = useTranslation();
  const chip = useChip();
  const hooks = useWizardStore((state) => state.hooks);
  const setHooks = useWizardStore((state) => state.setHooks);
  const pillsScroll = useDragScroll();
  const [step, setStep] = useState(0);
  const workzoneGuideTargetRef = useRef<HTMLDivElement>(null);

  const kind = hooks.kind;
  const config = (kind && hooks.configs[kind]) || {};
  // Третий гайд хука: тип уже выбран. Раньше подсказка 2/3 (hook-type, в
  // StageHooks) сама пряталась условием !hooks.kind — теперь она гейтится
  // только своим dismissed, и её можно оставить открытой, выбрав тип кликом
  // мимо кнопки подсказки, поэтому ждём его ЖИВОГО dismissed явно — иначе
  // hook-type (левая панель) и этот гайд (правая) всплывают одновременно.
  const typeGuideDismissed = useGuideLiveDismissed('hook-type');
  const [workzoneGuideDismissed, setWorkzoneGuideDismissed] = useGuideDismiss('hook-workzone', Boolean(kind), Boolean(kind) && typeGuideDismissed);
  const showWorkzoneGuide = Boolean(kind) && typeGuideDismissed && !workzoneGuideDismissed;
  useScrollGuideIntoView(showWorkzoneGuide, workzoneGuideTargetRef);
  /*
   * Смена типа хука начинает его настройку с первого шага. Без сброса переключение
   * с недонастроенных «Эффектов» на «Звук» открывало бы сразу шаг «стиль».
   * Если у нового хука первый шаг уже заполнен — сразу ведём на первый незаполненный.
   */
  useEffect(() => {
    if (!kind) return;
    const filled = hookSteps(kind).findIndex((item) => !((hooks.configs[kind] || {})[item.key]));
    setStep(filled < 0 ? 0 : filled);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind]);
  const configuredPills = hookPills(hooks);
  const pills = kind && !configuredPills.some((pill) => pill.kind === kind)
    ? [...configuredPills, { kind, label: HOOK_LABELS[kind] }]
    : configuredPills;
  const nextKind = nextFreeKind(hooks, kind);

  /*
   * Единый мастер настройки хука: шаг 1 — сам хук, шаг 2 — склейка, шаг 3 — стиль.
   * Раньше так работали только «Эффекты», а остальные типы показывали один список и
   * считались настроенными: склейку и стиль им можно было выбрать лишь в фуллскрине.
   * Определение шагов общее с фуллскрином (см. hookSteps), разъехаться им негде.
   */
  const steps = kind ? hookSteps(kind) : [];
  const stepIndex = Math.min(step, Math.max(0, steps.length - 1));
  const stepDef = steps[stepIndex];
  const stepValue = stepDef ? (config[stepDef.key] as string | undefined) : undefined;
  const styleValues = selectedStyles(config);
  const previewId = kind ? configuredPreviewId(kind, config, stepDef) : undefined;
  const canAdvance = Boolean(stepDef && (stepDef.key === 'effectStyle' ? styleValues.length : stepValue)) && stepIndex < steps.length - 1;

  const confirmButton = canAdvance && (
    /* Подтверждение шага галочкой (Figma W28) — переход только по клику */
    <button
      type="button"
      aria-label={t('wizard.fx.confirmStep')}
      className="absolute right-0 top-0 z-[2] flex h-[52px] w-[52px] shrink-0 items-center justify-center rounded-r15 bg-text transition hover:opacity-90 active:scale-95"
      onClick={() => setStep(stepIndex + 1)}
    >
      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5" stroke="var(--accent)" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
    </button>
  );

  /** Шаг «Звук» — не выбор из списка, а загрузка своего файла. */
  const soundStep = <WarmupInput />;

  const settings = kind && stepDef && (
    <div className="shrink-0 rounded-r15 bg-grad-soft-10 p-space-5">
      <div className="mb-space-4 flex items-center justify-between gap-[10px]">
        <p className="wizard-body min-w-0 truncate">{t(stepDef.title)}</p>
        {stepDef.key === 'effectStyle' && kind !== 'none' && <StyleScopeToggle config={config} onPick={(full) => setHooks({ config: { effectStyleFull: full } })} />}
        {stepDef.key === 'effectHook' && config.effectHook === 'Слоу-шаттер' && <SlowShutterExtendToggle config={config} onPick={(value) => setHooks({ config: { effectHookExtend: value } })} />}
        <span className="flex shrink-0 items-center gap-space-1 rounded-r40 bg-accent-20 px-space-2 py-space-1 text-[14px] text-text-80">
          <button type="button" aria-label={t('wizard.fx.prevStep')} disabled={stepIndex === 0} className="disabled:opacity-40" onClick={() => setStep(stepIndex - 1)}>‹</button>
          {stepIndex + 1}/{steps.length}
          <button type="button" aria-label={t('wizard.fx.nextStep')} disabled={stepIndex === steps.length - 1} className="disabled:opacity-40" onClick={() => setStep(stepIndex + 1)}>›</button>
        </span>
      </div>
      {/* Галочка — overlay поверх ленты справа: лента уходит ПОД неё, правый фейд встаёт перед */}
      <div className="relative">
        {stepDef.options.length === 0 ? (
          <div className={cn(canAdvance && 'pr-[64px]')}>{soundStep}</div>
        ) : (
          <ChipRow
            options={stepDef.options}
            value={stepValue}
            values={stepDef.key === 'effectStyle' ? styleValues : undefined}
            onPick={(option) => setHooks({ config: stepDef.key === 'effectStyle' ? toggleStyle(config, option) : { [stepDef.key]: option } })}
            rightGap={canAdvance ? 64 : 0}
          />
        )}
        {confirmButton}
      </div>
    </div>
  );

  return (
    <aside className="w12-col-aside">
      <div ref={workzoneGuideTargetRef} className="w12-card w12-aside">
        <div className="flex shrink-0 items-center justify-between gap-space-3">
          <h2 className="w12-h2 whitespace-nowrap">{t('wizard.workZone')}</h2>
        </div>

        <div className="relative min-h-0 flex-1 overflow-hidden rounded-r15 bg-grad-soft-10 max-md:aspect-[9/16] max-md:w-full">
          {kind && <EffectPreview previewId={previewId} />}
          <span className="dash-panel-plain pointer-events-none absolute inset-0 z-[3]" aria-hidden="true" />
          {!kind ? (
            <div className="flex h-full items-center justify-center p-space-5">
              <p className="wizard-body max-w-[223px] text-center">{t('wizard.fx.empty')}</p>
            </div>
          ) : null}
        </div>

        {settings}
      </div>

      <ActionGuideOverlay
        open={showWorkzoneGuide}
        targetRef={workzoneGuideTargetRef}
        title={t('wizard.fx.guideWorkzoneTitle')}
        text={t('wizard.fx.guideWorkzoneText')}
        dismissLabel={t('wizard.fx.guideDismiss')}
        progressLabel={t('wizard.guideProgress', { current: 3, total: 3 })}
        onDismiss={() => setWorkzoneGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<HookWorkzoneGuideVisual />}
      />

      <PillsFooter
        pills={pills.map((pill) => ({
          key: pill.kind,
          label: chip(pill.label),
          icon: <SvgMaskIcon src="/assets/figma/icon-bolt.svg" style={{ width: 12, height: 18, color: 'var(--accent-light)' }} />
        }))}
        activeKey={kind}
        emptyLabel={t('wizard.fx.add')}
        onPill={(key) => setHooks({ kind: key as HookKind })}
        onPlus={() => { if (nextKind) setHooks({ kind: nextKind }); }}
        plusDisabled={!nextKind}
        ready={ready}
        canContinue={canContinue}
        loading={loading}
        onBack={onBack}
        onNext={onNext}
        dragScroll={pillsScroll}
      />
    </aside>
  );
}
