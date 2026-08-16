# Blast Web Preview

The archive application is deployed as an isolated preview at
`https://app.blast808.com`. It does not replace the marketing landing and does
not use the production bot/orchestrator/payment paths.

Runtime:

- `blast-web-preview-frontend`: built Vite SPA, exposed on host loopback port 18190.
- `blast-web-preview-api`: FastAPI mock backend, internal Docker network only.
- `blast-web-preview-data`: persistent SQLite and auth state.
- `blast-web-preview-uploads`: persistent user uploads.

Production-safety preview flags are explicit: secure cookies, CSRF and rate
limits are enabled, while `/api/dev/*` is disabled. The session signing secret
comes from the GitHub Actions secret `BLAST_WEB_PREVIEW_SESSION_SECRET`.
