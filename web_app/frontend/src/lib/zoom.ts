/*
 * Суммарный CSS-zoom элемента: корень приложения (AppShell) × холст визарда (WizardCanvas).
 * Координаты мыши и getBoundingClientRect — визуальные пиксели, а scrollLeft, offsetWidth и
 * left/top у fixed-элементов — CSS-пиксели самого элемента. Переводить одно в другое — делением
 * на этот множитель, а не на zoom корня: внутри визарда их два.
 */
export function cssZoom(element?: Element | null): number {
  const own = (element as (Element & { currentCSSZoom?: number }) | null | undefined)?.currentCSSZoom;
  if (typeof own === 'number' && Number.isFinite(own) && own > 0) return own;
  // браузер без currentCSSZoom: визуальная ширина к собственной
  if (element instanceof HTMLElement && element.offsetWidth > 0) return element.getBoundingClientRect().width / element.offsetWidth;
  const root = Number.parseFloat(getComputedStyle(document.documentElement).zoom);
  return Number.isFinite(root) && root > 0 ? root : 1;
}
