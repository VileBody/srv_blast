# Blast Web Production

The React application at `https://app.blast808.com` is the production product.
It uses the production orchestrator, Windows render nodes, Timeweb S3, the
public bot credit database and the existing verified T-Bank webhook.

Runtime:

- `blast-web-frontend`: built Vite SPA, exposed on host loopback port 18190.
- `blast-web-api`: FastAPI production adapter, internal Docker network only.
- web state is stored in Postgres; credits and subscription state use the
  public bot's Postgres database.
- user media and render output are stored in Timeweb S3.

The canonical deployment is `.github/workflows/deploy-web-production.yml` with
`infra/runners/deploy_web_production.sh`. Its environment file stays on the
deployment host at `/home/deploy/blast_final/web_app/backend/.env.production`
with mode `600`. Secure cookies, CSRF and shared Redis rate limits are enabled;
`/api/dev/*` is disabled.
