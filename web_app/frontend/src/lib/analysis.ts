import type { AnalysisDimension, DimensionAnalysis, IterationAnalysis } from './types';
import type { TFunction } from 'i18next';

/**
 * Общая обвязка над разбором итераций (`analyze_iterations` на бэке).
 *
 * Смысл разбора: не «какая связка победила», а КАКОЙ ПАРАМЕТР ведущий. Все ролики с Brat
 * сравниваются со всеми остальными, отдельно от футажа и хуков. По каждому измерению —
 * свой вердикт, и «проверить нельзя» полезен не меньше «есть сигнал»: он говорит, что
 * менять в следующем батче.
 *
 * Живёт в lib, потому что нужен и на дашборде (карточка-тизер), и на странице аналитики.
 */

/** Подписи измерений. Значения (Brat, «Ночной город») не переводим — это лейблы стора. */
export const DIMENSION_KEY: Record<AnalysisDimension, string> = {
  background: 'stats.dimBackground',
  subtitles: 'stats.dimSubtitles',
  fx: 'stats.dimFx'
};

/** Измерение → значение `testParameter` в ручке создания итерации. */
export const DIMENSION_TEST_PARAM: Record<AnalysisDimension, 'background' | 'subtitles' | 'hooks'> = {
  background: 'background',
  subtitles: 'subtitles',
  fx: 'hooks'
};

/** Короткий вердикт для чипа: «+42%», «нужно ещё 2», «слиплось с фоном». */
export function verdictText(t: TFunction, item: DimensionAnalysis): string {
  switch (item.verdict) {
    case 'signal':
      return t('stats.verdictSignal', { lift: Math.round(item.liftPercent) });
    case 'no_difference':
      return t('stats.verdictNoDifference');
    case 'low_data':
      return t('stats.verdictLowData', { count: item.videosNeeded });
    default: {
      const other = item.blockedBy?.startsWith('entangled:')
        ? (item.blockedBy.split(':')[1] as AnalysisDimension)
        : null;
      return other
        ? t('stats.verdictEntangled', { other: t(DIMENSION_KEY[other]) })
        : t('stats.verdictSingleValue');
    }
  }
}

/** Цвет вердикта: сигнал — успех, «данных мало» — нейтрально, «проверить нельзя» — предупреждение. */
export function verdictTone(verdict: DimensionAnalysis['verdict']): string {
  if (verdict === 'signal') return '#04BA38';
  if (verdict === 'blocked') return 'var(--warning)';
  return 'var(--text-40)';
}

export function leadingDimension(analysis?: IterationAnalysis | null): DimensionAnalysis | null {
  if (!analysis?.leadingDimension) return null;
  return analysis.dimensions.find((item) => item.dimension === analysis.leadingDimension) ?? null;
}

/**
 * Что тестировать в следующем батче. Приоритет: то, что «проверить нельзя» (значения
 * слиплись или их вообще одно) — именно эту раскладку и надо развести. Потом то, где не
 * хватает роликов. Если всё уже прочитано — самое слабое из проверенных.
 */
export function nextToTest(analysis?: IterationAnalysis | null): DimensionAnalysis | null {
  if (!analysis?.dimensions.length) return null;
  const order: DimensionAnalysis['verdict'][] = ['blocked', 'low_data', 'no_difference', 'signal'];
  const sorted = [...analysis.dimensions].sort(
    (a, b) => order.indexOf(a.verdict) - order.indexOf(b.verdict) || a.confidence - b.confidence
  );
  return sorted[0] ?? null;
}

/** Средние просмотры на ролик — понятная цифра для бара (сумма по группам сравнивала бы разные размеры). */
export function averageViews(videos: number, views: number): number {
  return videos > 0 ? Math.round(views / videos) : 0;
}

/** Бары «лидер против остальных» по ведущему измерению. */
export function leaderBars(t: TFunction, item: DimensionAnalysis, minVideos: number) {
  if (!item.leader) return null;
  const others = item.values.filter((value) => value.value !== item.leader?.value && value.videos >= minVideos);
  const otherVideos = others.reduce((sum, value) => sum + value.videos, 0);
  const otherViews = others.reduce((sum, value) => sum + value.views, 0);
  return [
    { label: item.leader.value, views: averageViews(item.leader.videos, item.leader.views), winner: true },
    { label: t('stats.barOthers'), views: averageViews(otherVideos, otherViews) }
  ];
}
