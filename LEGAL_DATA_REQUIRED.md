# Legal documents — status and open items

Documents in `landing/js/legal-documents.js` (version `1.0`, effective 3 August 2026) are
substantive final texts, not drafts: the on-page "working draft" banner and all inline
`[confirm ...]` placeholders were removed. A qualified Russian lawyer should still sign the texts
off before large-scale traffic, but nothing in them is left blank.

## MERGE GATE — read before merging to main

The privacy policy and the consent form now state that **no cross-border transfer of personal data
is carried out** (privacy sec. 8, consent sec. 7) and no longer name Google LLC or OpenRouter as
recipients. Merging to `main` publishes these statements on blast808.com. They are not true of the
pipeline as configured in this repository today, so the following must be done first:

1. **User content must stop leaving Russia.** `.env.example` has `LLM_PROVIDER_MODE=gemini` with
   `GEMINI_MODEL_STAGE1` doing ASR — the user's audio file itself is uploaded to Google. Stage 2
   subtitles and footage are on Gemini too. OpenRouter is not a fix: it is also a US company.
   A Russian or self-hosted ASR/LLM path is required.
2. **F5 TTS.** `GEMINI_MODEL_F5_TTS` must be off or replaced (`F5_HOOK_DEVICE` empty disables it).
3. **Consent is collected on the website.** Consent sec. 9.1 says the affirmative action happens at
   registration on blast808.com. Do not merge before that site is live and records user id,
   document version and timestamp. Deliberate decision: no consent screen is added to the Telegram
   bot; bot processing relies on contract performance (п. 5 ч. 1 ст. 6), which needs no consent.
4. **Website legal pages must be synchronized** with these texts — they were written against the
   previous landing version.

Telegram remains in the loop. Privacy sec. 8.2 takes the position that Telegram is a communication
channel chosen by the user rather than an onward transfer by the operator. That position is
defensible and commonly taken, but it is a position, not a settled point — worth one hour of a
lawyer's time before merge.

Only the Art. 22 notification is planned. The Art. 12 cross-border notification becomes mandatory
again the moment the TikTok integration goes live (TikTok processes in Singapore and the US);
privacy sec. 8.3 already says so.

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
| Source audio / lyrics / images retention | 30 days after order fulfilment |
| Generated videos retention | 30 days after delivery |
| Telegram profile + order history | 12 months after last interaction |
| Logs (execution + security) | 3 months |
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
| Localization | RU databases and compute; no cross-border transfer declared |
| Processors named | Russian cloud provider, АО «Т-Банк», Telegram (user's own channel) |

Tariffs published in the offer match `services/tg_bot_public/app.py::_PKG_TEXTS`:
Trial 990 ₽ / 5 videos; Blast 1 990 ₽ per month / 100 videos; Glow 7 990 ₽ / 400 videos, 10 tracks,
CapCut template; Impulse 29 990 ₽ / 1 year, unlimited within fair use, 24 tracks.

## Open items — operations must match the published text

1. **Roskomnadzor notification (ст. 22 152-ФЗ).** File before processing continues. The form has a
   cross-border transfer block — it can only be answered "not carried out" once the merge gate
   above is satisfied.
2. **Consent capture on the website.** Record user id, document version (`1.0`) and timestamp, plus
   a withdrawal record. Cheap safety net for users who reach the bot directly by link: one
   informational `/start` message linking the policy and the offer — disclosure, not a consent
   button.
3. **Retention enforcement.** The 30-day / 3-month periods above must be enforced by an actual
   cleanup job over S3 objects, Postgres rows and logs. `services/orchestrator/cleanup.py` and the
   bot's tmp reapers cover only short-lived local files today.
4. **Free trial count.** Set to 5 everywhere: landing headline, `INITIAL_CREDITS` default and
   `.env.example`. The production `.env` on the server must be updated too — it overrides the
   default. Note that the paid `Blast Trial` plan (990 ₽) also delivers 5 videos, so it is now
   redundant; decide whether to drop it from `_PKG_TEXTS` and from the offer.
5. **Processor contracts.** ч. 3 ст. 6 152-ФЗ requires written instructions to processors. Confirm
   the T-Bank merchant agreement, the cloud provider contract and the terms under which Gemini /
   OpenRouter are used (paid tier, no training on submitted data — as claimed in clause 5.3).
6. **Incident procedure.** Clause 12.3 commits to 24h/72h Roskomnadzor notification. Assign an
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
