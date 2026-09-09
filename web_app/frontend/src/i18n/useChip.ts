import { useTranslation } from 'react-i18next';

/**
 * Перевод ОТОБРАЖЕНИЯ лейблов-значений визарда (Молния/Щелчок/Круг/Свайп/…).
 * Сами значения в сторе остаются русскими — по ним матчит бэк-резолвер и
 * effects-registry.json. Здесь только показываем перевод; fallback — сам лейбл
 * (для стилей субтитров Impulse/Brat и прочих, которых нет в словаре).
 */
export function useChip() {
  const { t } = useTranslation();
  return (label: string) => t(`chip.${label}`, { defaultValue: label });
}
