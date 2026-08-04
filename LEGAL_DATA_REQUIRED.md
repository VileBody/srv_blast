# Legal data required before final legal approval

The published pages are working drafts. A qualified Russian lawyer must review the final texts and the actual data flow before they are treated as final legal documents or submitted for TikTok review.

## Operator and contacts

- Confirm the operator/contractor name currently present in the repository: ИП Чернов Никита Романович.
- Confirm INN `623013205426` and OGRNIP `324620000005644`.
- Confirm the postal/legal address currently present in the repository.
- Confirm `support@blast808.com`, `+7 (910) 572-49-67`, and `@impulsemarketing` as public support contacts.
- Confirm who handles privacy and data-subject requests and the expected response process.

## Service and payments

- Confirm whether payments are currently accepted through the site, the Telegram bot, or both.
- Confirm the legal name of the payment provider (`АО «Т-Банк»` is used in current files), accepted methods, receipt/fiscalization flow and merchant agreement.
- Confirm current tariffs, deliverables, subscription renewal/cancellation rules and whether taxes are included.
- Resolve the mismatch between the landing promise of 3 free videos and the existing offer's paid `Blast Trial` plan with 5 videos.
- Confirm cancellation eligibility, refund method and legally compliant refund timing.
- Confirm when generation is deemed started and when the service is deemed delivered.

## Personal data and infrastructure

- Produce an authoritative data-flow map covering the website, Telegram bot, orchestrator, storage, generation providers, support and payment provider.
- List every processor/subprocessor, legal entity, processing purpose, hosting country/region and contract or other legal basis.
- Confirm whether personal data is localized in Russia and whether cross-border transfers occur; document required notifications and legal grounds.
- Confirm retention and deletion periods for Telegram profile data, source audio, lyrics/text, generated videos, prompts, logs, support correspondence and payment/accounting records.
- Confirm backup retention and deletion behavior.
- Confirm security contacts, incident response, access controls and user identity verification for data requests.
- Determine whether Roskomnadzor notification or updates are required before production processing.

## Consent and communications

- Add an explicit, unticked consent action at the actual data collection point in the Telegram bot or other user flow.
- Record consent version, timestamp, user identifier and withdrawal events.
- Determine whether separate consent for marketing messages is required. No marketing consent flow has been added because the current landing contains no evidence that such messages are sent.
- Confirm age limits and whether minors may use the service.

## TikTok integration

- Specify the exact TikTok product(s): Login Kit, Content Posting API, Display API, Share Kit, or other.
- List requested scopes and justify each one.
- Document all TikTok data received, storage location, retention, deletion and user revocation flow.
- Confirm redirect URIs, verified domains/URL prefixes and the public data-deletion procedure.
- Prepare a working end-to-end integration and review video; the current website alone does not demonstrate a TikTok integration.
- Verify that the public website meets TikTok's “fully developed website” criterion and is not rejected as a landing-only site.

## Cookies and tracking

- No analytics or marketing scripts were found in `landing/` at implementation time.
- Before adding any analytics or marketing vendor, document the vendor, cookie/local-storage names, purpose, lifetime, recipients and countries, then load it only through the matching consent category.
- Revisit consent when the category list or vendor set changes and increment the consent version.