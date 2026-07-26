# PHOTO flow: rollout checklist

The photo flow stays behind `PHOTO_FLOW_ENABLED`. Roll out the team bot first;
the public bot must remain explicitly disabled until the team smoke passes.

## 1. Candidate build

- Commit source and tests on the feature branch.
- Run the photo/framing/picker/bot mirror test matrix.
- Run team/public parity against `origin/main`.
- Build candidate orchestrator, worker and bot images from the same revision.
- Do not switch production containers yet.

## 2. Photo base backfill

- Activate/index the S3 photo pool; require `index_failed=0`.
- Run semantic tagging for untagged photos.
- Run framing + quality backfill for every pickable photo.
- Export the new photo snapshot.
- Verify in Postgres:

```sql
SELECT
    count(*) AS total,
    count(*) FILTER (WHERE framing <> '{}'::jsonb) AS framed,
    count(*) FILTER (WHERE framing->'quality' IS NOT NULL) AS quality_scored,
    count(*) FILTER (
        WHERE framing->'quality'->>'reject' = 'true'
    ) AS quality_rejected
FROM footage_tags
WHERE source = 'photo';
```

Required result: every pickable photo is framed and quality-scored. The
rejected share should remain close to 10-15%; investigate a large drift before
enabling the bot.

## 3. Fail-closed readiness

Photo readiness is required by default for every prod-path deploy. Run:

```bash
python -m services.orchestrator.picker_readiness \
  --pools video,photo \
  --photo-required
```

Required result:

- video pool is still healthy;
- photo pool has at least 50 pickable assets;
- every active `photo:*` bucket has at least 5 quality-passed candidates;
- all 17 active photo buckets are present.

If readiness fails, do not switch containers. The currently running production
revision remains active; fix/backfill the candidate data and rerun the gate.

## 4. Telegram preview registry

- Build all 17 photo previews from quality-passed photos.
- Upload them to the team bot and store their Telegram `file_id`.
- Produce `data/photo_bucket_previews.json` with exactly the 17 active
  `photo:*` IDs.
- Include the registry in the bot image.
- Public `file_id_public` can be added now, but public flow remains disabled.

## 5. Team-bot smoke

- Keep public `PHOTO_FLOW_ENABLED=0`.
- Enable photo flow only for the team bot.
- Run one job:

```text
track -> lyrics -> Pictures -> photo vibe ->
night vision -> flash -> 1 version -> generation
```

Verify:

- ranking and selection contain only `photo:*` assets;
- request has `bg_mode=photo`, `photo_style=night_vision`,
  `photo_transition=flash`;
- selected assets have `source=photo`, framing and passed quality metadata;
- AE entry comp is `Photo Render`;
- result is H.264 at 1920x1440;
- the user receives the download link;
- one footage job and one solid-color job still complete normally.

## 6. Public rollout

- Require several successful team jobs.
- Rerun fail-closed readiness.
- Confirm all public preview IDs.
- Enable the public flag for an allowlisted smoke.
- Open the flow to all users only after that smoke completes.
