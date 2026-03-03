# MinIO Asset Mirror (S3 -> MinIO)

Goal: run local MinIO next to `tg-bot` and mirror `S3_BUCKET_ASSET_STORAGE` into a local MinIO bucket for repeated read access.

## 1) Configure `.env`

Required MinIO vars:

```env
MINIO_ROOT_USER=<set>
MINIO_ROOT_PASSWORD=<set>
MINIO_REGION=ru-1
MINIO_ENDPOINT_URL=http://127.0.0.1:19100
MINIO_BUCKET_ASSET_STORAGE=<set>
MINIO_BIND_HOST=127.0.0.1
MINIO_API_PORT=19100
MINIO_CONSOLE_PORT=19101
```

Notes:
- Use a strong password in `MINIO_ROOT_PASSWORD` (random 24+ chars).
- Default bind host is `127.0.0.1` so MinIO is not exposed directly to the internet.

Source S3 vars are reused from existing config:

```env
S3_ENDPOINT_URL=<set>
S3_ACCESS_KEY_ID=<set>
S3_SECRET_ACCESS_KEY=<set>
S3_REGION=<set>
S3_BUCKET_ASSET_STORAGE=<set>
```

## 2) Start MinIO

```bash
docker compose up -d minio
```

Console:

```text
http://127.0.0.1:19101
```

## 3) Dry run mirror

```bash
python3 scripts/s3_to_minio_once.py --create-dst-bucket --dry-run
```

## 4) Run mirror

```bash
python3 scripts/s3_to_minio_once.py --create-dst-bucket --skip-existing
```

The mirror script is:

```text
scripts/s3_to_minio_once.py
```

Default behavior:
- source bucket: `S3_BUCKET_ASSET_STORAGE`
- destination bucket: `MINIO_BUCKET_ASSET_STORAGE`
- keeps current key structure
- explicit fail on missing env/buckets

## 5) Access via test domain

If your test URL is `TG_WEBHOOK_BASE_URL=https://6188337-pz31905.twc1.net`, expose MinIO through reverse proxy on the same host/domain (instead of opening MinIO ports directly).

Example Nginx locations:

```nginx
location /minio/ {
    proxy_pass http://127.0.0.1:19101/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}

location /minio-api/ {
    proxy_pass http://127.0.0.1:19100/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    client_max_body_size 5g;
}
```
