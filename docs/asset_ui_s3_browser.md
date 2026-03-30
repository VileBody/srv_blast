# S3 Asset UI (Preview + Upload + Soft Delete)

`asset-ui` is a FastAPI web interface for `S3_BUCKET_ASSET_STORAGE`.

## Runtime env

Required:

- `S3_ENDPOINT_URL`
- `S3_ACCESS_KEY_ID`
- `S3_SECRET_ACCESS_KEY`
- `S3_REGION`
- `S3_BUCKET_ASSET_STORAGE`
- `ASSET_UI_PORT`
- `ASSET_UI_UPLOAD_MAX_MB`
- `ASSET_UI_TRASH_PREFIX`
- `ASSET_UI_PRESIGN_TTL_S`

## Docker compose

Service is defined in root `docker-compose.yml`:

- container listens on `ASSET_UI_PORT` (example: `8100`)
- host bind is fixed to `127.0.0.1:18173`

Start:

```bash
docker compose up -d asset-ui
```

## Reverse proxy + Basic Auth

Expose only through reverse proxy and protect with Basic Auth:

```nginx
location /asset-ui/ {
    auth_basic "Asset UI";
    auth_basic_user_file /etc/nginx/.htpasswd_asset_ui;

    proxy_pass http://127.0.0.1:18173/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

If you want to expose it as part of admin zone, use `/admin/assets/` and the
same Basic Auth file as `/admin/`:

```nginx
location /admin/assets/ {
    auth_basic "Blast Backoffice";
    auth_basic_user_file /etc/nginx/.htpasswd_backoffice;

    proxy_pass http://127.0.0.1:18173/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Create credentials:

```bash
sudo htpasswd -c /etc/nginx/.htpasswd_asset_ui admin
```

Reload Nginx:

```bash
sudo nginx -t && sudo systemctl reload nginx
```
