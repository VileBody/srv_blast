# Landing Assets

Static design assets for the landing page live in this directory (`png/svg`).

Video files are not tracked in git:

- Local pattern ignored: `landing/assets/*.mp4`
- Runtime source for hero/examples videos: `landing/js/media-config.js`
- Current production media prefix: `https://s3.twcstorage.ru/f7cef916-asset-storage/landing/blast808/media/v1`

If media is updated:

1. Re-encode web-friendly `.mp4` files (h264 + `+faststart`).
2. Upload to S3 with `public-read` and long cache (`immutable`).
3. Update key mapping in `landing/js/media-config.js`.
