# Legal documents — status and open items

Published documents (`landing/js/legal-documents.js`, version `1.0`, effective 3 August 2026) are
substantive final texts, not drafts: the on-page "working draft" banner and all inline
`[confirm ...]` placeholders were removed. A qualified Russian lawyer should still sign the texts
off before large-scale traffic, but nothing in them is left blank.

Documents: `terms.html`, `privacy.html`, `cookies.html`, `personal-data-consent.html`,
`offer.html`, `contacts.html` — each in RU and EN, switchable via the header language toggle
and via `?lang=ru|en`.

## Decided and written into the documents

These values are now published commitments. Change the documents if operations change.

| Topic | Published value |
| --- | --- |
| Operator | ИП Чернов Никита Романович, ИНН 623013205426, ОГРНИП 324620000005644 |
| Address | 390048, Рязань, ул. Васильевская, д. 18, кв. 60 |
| Support / data / claims contact | support@blast808.com, +7 (910) 572-49-67, @impulsemarketing |
| Support response | 1 business day |
| Data subject request response | 10 business days (+5 extension) — ст. 20 152-ФЗ |
| Data deletion after consent withdrawal | 30 days — ст. 21 152-ФЗ |
| Source audio / lyrics / images retention | 90 days after order fulfilment |
| Generated videos retention | 90 days after delivery |
| Telegram profile + order history | 12 months after last interaction |
| Logs (execution + security) | 12 months |
| Support correspondence | 12 months after closure |
| Payment / accounting records | 5 years — 402-ФЗ, НК РФ |
| Consent records | term of consent + 3 years |
| Backups | 30 days |
| TikTok tokens | encrypted; deleted on revoke/disconnect or 12 months inactivity |
| TikTok data deletion after request | 30 days |
| Order turnaround | ~60 min typical, 3 business days maximum |
| Refunds | pro rata to undelivered videos, paid out within 10 calendar days — ст. 31/32 ЗоЗПП |
| Subscription cancellation | `/cancelsubscription` in the bot, effective end of paid period |
| Tax regime | УСН, VAT not charged, fiscal receipt via T-Bank (54-ФЗ) |
| Age | 18+, 14–18 with legal representative consent |
| Localization | RU databases; cross-border transfer limited to user materials + technical metadata |
| Processors named | Telegram, АО «Т-Банк», Russian cloud provider, Google LLC (Gemini API), OpenRouter |

Tariffs published in the offer match `services/tg_bot_public/app.py::_PKG_TEXTS`:
Trial 990 ₽ / 5 videos; Blast 1 990 ₽ per month / 100 videos; Glow 7 990 ₽ / 400 videos, 10 tracks,
CapCut template; Impulse 29 990 ₽ / 1 year, unlimited within fair use, 24 tracks.

## Open items — operations must match the published text

1. **Roskomnadzor notification (ст. 22 152-ФЗ) and the cross-border transfer notification
   (ст. 12).** The privacy policy states that cross-border transfer is carried out "with
   notification to the supervisory authority". File the notifications if not already filed.
2. **Consent capture in the bot.** `personal-data-consent.html` states that consent is given by a
   separate, non-pre-ticked affirmative action and that the operator records user id, document
   version and timestamp. This flow does not yet exist in `services/tg_bot_public` — build it.
3. **Retention enforcement.** The 90-day / 12-month periods above must be enforced by an actual
   cleanup job over S3 objects, Postgres rows and logs. `services/orchestrator/cleanup.py` and the
   bot's tmp reapers cover only short-lived local files today.
4. **Free trial count.** The landing headline promises 3 free videos; `INITIAL_CREDITS` defaults to
   `2` (`services/tg_bot_public/config.py`). Set the env to 3 or change the headline — the offer
   itself no longer hardcodes a number.
5. **Instagram link in the landing footer.** Under Russian law, a mention of Instagram requires the
   notice that it belongs to Meta, recognized as an extremist organization and banned in Russia.
   Either add the notice or drop the link.
6. **Processor contracts.** ч. 3 ст. 6 152-ФЗ requires written instructions to processors. Confirm
   the T-Bank merchant agreement, the cloud provider contract and the terms under which Gemini /
   OpenRouter are used (paid tier, no training on submitted data — as claimed in clause 5.3).
7. **Incident procedure.** Clause 12.3 commits to 24h/72h Roskomnadzor notification. Assign an
   owner and write the runbook.

## Before submitting the TikTok app for review

- Specify the exact products (Login Kit, Content Posting API, Display API) and the scope list;
  the privacy policy names `open_id`, `union_id`, display name, avatar, video list and stats —
  keep the requested scopes within that list or widen the policy first.
- Build the integration end to end. TikTok rejects clients whose website does not demonstrate the
  described integration; the current site describes but does not yet implement it.
- Implement the UX requirements already promised in `terms.html` clause 8: content preview,
  editable caption and hashtags, no default privacy or interaction settings, no watermark,
  commercial content disclosure toggle, and the exact declaration
  "By posting, you agree to TikTok's Music Usage Confirmation"
  (with Branded Content Policy added when the branded content toggle is on).
- Implement account disconnect in the bot — clause 11.6 of the privacy policy promises it.
- Verify the redirect URI and domain, and check that the public data deletion route
  (support@blast808.com) is reachable from the privacy policy without login. It is.
- Respect unaudited client limits: max 5 users per 24h, accounts private at posting time,
  `SELF_ONLY` viewership.
