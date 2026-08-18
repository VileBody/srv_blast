# Brief: build preview reels for the film collections

## What you are doing

Every group in the bot's footage shortlist shows a short example reel above its
button, so the user picks by seeing the footage rather than by reading a name.
The 9:16 vibes and the photo vibes already have theirs. The **film collections**
do not — twelve of them are live in the bot right now with a button and no reel.

Build those twelve reels and register them so the bot serves them.

Scope is films only. `cine16x9` (one collection) and `people` (no uploads yet)
come later; do not build them unless asked.

## The machinery already exists

`scripts/build_bucket_previews.py` does the whole job and already understands the
collection plane. You are running it and verifying the result, not writing it.
If you find yourself adding a new render path, stop — that is a sign of a wrong
turn.

    python scripts/build_bucket_previews.py --media collection --all

What one run does per bucket: picks representative clips with the production
picker → builds a montage JSX → renders it on the node → uploads the mp4 to S3 →
sends it to the backlog chat to capture Telegram `file_id`s → writes an entry to
the store.

Useful flags:

| Flag | Why |
|---|---|
| `--only <bucket_id>` | one bucket; start here |
| `--dry-run` | clip selection only, no render/S3/Telegram |
| `--no-telegram` | render + S3, skip file_id capture |
| `--register-only` | send already-rendered mp4s and capture file_ids |
| `--force` | rebuild a bucket that already has a usable preview |
| `--limit N` | first N buckets |

`--all` is deliberately required for a full sweep; without it the script refuses
to touch every bucket at once.

## Where things live

- Catalog: `mlcore/footage_collection_catalog.py`, registry `data/footage_collections.json`
- Pool: `data/collection_inventory.json` (env `COLLECTION_INVENTORY_JSON`)
- Montage template: `templates/bucket_preview/montage_template.jsx`
- Store you are producing: `data/collection_bucket_previews.json`
- Bot reads it via `_bucket_preview_file_id` in both `services/tg_bot_*/app.py`

Store entry shape (one per bucket_id):

    bucket_id, label, description, s3_url, file_id, file_id_public,
    clip_ids[], status (ok|thin|error), built_at

Defaults: 5 clips per reel, ~1.5s each, a bucket with fewer than 3 usable clips
is marked `thin` and skipped rather than rendered.

## Environment

Needed: `AE_NODE_URL`, `S3_BUCKET_ASSET_STORAGE` (or `FOOTAGE_PREVIEW_S3_BUCKET`),
S3 credentials, `COLLECTION_INVENTORY_JSON`, and for registration `TG_BOT_TOKEN`
plus `FOOTAGE_PREVIEW_BACKLOG_CHAT_ID` (or `MANAGER_CHAT_ID`).

`file_id_public` needs `TG_PREVIEW_SOURCE_BOT_TOKEN` — Telegram file ids are
per-bot, so the public bot cannot send a file id captured by the team bot. **A
store with only `file_id` filled leaves the public bot showing nothing**, which
looks identical to "previews not built". Check both fields.

## Order of work

1. `--dry-run --only` on one bucket. Confirm the selected clips are all from that
   film and none from another.
2. Full run on the same one bucket. Watch the reel: it should read as that film.
3. The remaining eleven.
4. Verify the store: twelve entries, `status: ok`, both file_id fields non-empty.
5. Open the bot, go «Футажи» → «Фильмы», confirm a reel appears above every
   button.

Step 5 is the acceptance test. The store existing is not the same as the bot
serving it.

## What to watch for

**Clips are ~3.5s and the reel wants 1.5s each** — fine, but the montage may look
choppier than the vibe reels built from longer material. If it reads badly, say
so before building all twelve; do not silently change the timing.

**Folder names are Cyrillic and capitalised in S3** (`Реквием по мечте`) while
the registry spells them lowercase. That mismatch is handled — membership is
matched case-insensitively — but it has already caused two production failures,
so if something resolves to an empty pool, look there first.

**AE fails on non-ASCII local paths.** Media file names are ASCII by
construction, but if you add any path of your own, keep it ASCII.

**These are 16:9 sources rendered into a vertical frame**, so they are
centre-cropped hard. That is expected until the frames feature lands. Judge the
reel on "does this look like the film", not on the crop.

## Done means

- `data/collection_bucket_previews.json` has twelve `ok` entries, both file_id
  fields populated;
- the bot shows a reel for every film button;
- committed on a short-lived branch with a PR (main is protected), CI green.

Report per bucket: clips chosen, status, and anything that looked wrong in the
reel. If a bucket comes out `thin`, do not force it — say which one and why.
