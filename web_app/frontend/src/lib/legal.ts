/**
 * Ссылки на юридические документы.
 *
 * По умолчанию — внутренние публичные страницы (`LegalPage`, тексты в
 * `src/data/legal-docs.ts`). Если документы когда-нибудь переедут на отдельный домен,
 * достаточно вписать URL в `frontend/.env` (VITE_LEGAL_POLICY_URL /
 * VITE_LEGAL_OFFER_URL) — менять код не нужно.
 */
export const LEGAL_LINKS = {
  policy: import.meta.env.VITE_LEGAL_POLICY_URL ?? '/legal/policy',
  offer: import.meta.env.VITE_LEGAL_OFFER_URL ?? '/legal/offer'
} as const;
