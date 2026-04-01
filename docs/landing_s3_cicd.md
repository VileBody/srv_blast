# Landing: S3 + CI/CD + Timeweb DNS

## Summary

Landing is deployed as static files to `S3_BUCKET_ASSET_STORAGE`.
Source of truth is tracked files in `landing/` (`index.html`, `css/`, `js/`, `assets/`, `fonts/`).

`.rar` archives in `landing/` are local import artifacts only and are not tracked/deployed.

## Prefix layout

Base prefix (GitHub variable): `LANDING_S3_BASE_PREFIX`.
Recommended value: `landing/blast808`.

Deploy targets:

- Preview: `landing/blast808/previews/<branch>-<short_sha>/`
- Release: `landing/blast808/releases/<full_sha>/`
- Live: `landing/blast808/live/`

Release policy:

- Push to `main`: `validate -> release -> promote to live`
- Push to non-main branch: `validate -> preview`
- Manual `workflow_dispatch`: `preview | release | promote | rollback`

## GitHub workflow

Workflow file: `.github/workflows/deploy-landing-s3.yml`.

Required repository **secrets**:

- `S3_ENDPOINT_URL`
- `S3_ACCESS_KEY_ID`
- `S3_SECRET_ACCESS_KEY`
- `S3_REGION`
- `S3_BUCKET_ASSET_STORAGE`

Required repository **variables**:

- `LANDING_S3_BASE_PREFIX` (example: `landing/blast808`)
- `LANDING_MAIN_BRANCH` (example: `main`)
- `LANDING_PUBLIC_URL` (optional, default: `https://blast808.com`)
- `LANDING_PUBLIC_URL_WWW` (optional, default: `https://www.blast808.com`)

## Validation and deploy scripts

- `scripts/landing_validate.py`
  - checks `landing/index.html` exists
  - checks all local HTML/CSS references resolve to existing files
  - fails if `.rar` is found in deploy tree
  - local helper flag: `--allow-local-rar` (for local import-only archives)

- `scripts/landing_s3_deploy.py`
  - supports modes: `preview`, `release`, `promote`, `rollback`
  - performs 1:1 sync (upload/update + delete extra keys)
  - sets `Content-Type` and `Cache-Control`
  - can run dry-run and smoke checks (presigned HTTP GET for index + assets)

- `scripts/landing_public_check.py`
  - verifies live public domains after promote/rollback
  - fails if required marker is missing from returned HTML

## One-time Timeweb DNS/TLS setup

1. In Timeweb object storage/CDN panel, create public endpoint bound to `landing/blast808/live/`.
2. Attach domains:
   - `blast808.com`
   - `www.blast808.com`
3. In Timeweb DNS, create records exactly as provided by custom-domain wizard (usually CNAME/ALIAS).
4. Enable TLS certificates for both domains in Timeweb panel.

After that, production updates are done by CI/CD only (promote to `live`).

## Optional local import flow from archive

Example (if `unrar` is available):

```bash
mkdir -p landing/tmp
unrar x "landing/ассеты.rar" landing/tmp/
# then copy selected files into tracked landing/{assets,fonts,...} and remove tmp
```

`landing/*.rar` and `landing/tmp/` remain ignored by git.
