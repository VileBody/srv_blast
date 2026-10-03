#!/usr/bin/env python3
"""Заранее готовит лёгкие копии превью каталогов сайта (вайбы, фото, стили, эффекты, рамки).

Зачем. Каталоги сайта отдают не сырые AE-рендеры, а копии через прослойку
(`/api/wizard/media/preview/...`, см. web_app/backend/app/media_proxy.py). Копия делается
по первому запросу; без прогрева первый посетитель после выкатки или перезаливки превью
ждал бы ffmpeg на каждой карточке. Скрипт делает то же, что сделал бы первый запрос:
превью + заставку для роликов, уменьшенную копию для картинок.

Идемпотентно: имя копии = оригинал + его ETag, готовые пропускаются (HEAD в S3). Перезалили
превью (новый ETag) — повторный запуск сделает только новые.

Запуск на сервере, внутри контейнера веба (там env каталогов и S3, PYTHONPATH на бэк):
    docker exec -i <web-контейнер> python - --dry-run < scripts/prewarm_web_previews.py
    docker exec -i <web-контейнер> python - < scripts/prewarm_web_previews.py
Локально из репо (нужен тот же env, что у продакшн-бэка): python scripts/prewarm_web_previews.py
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:  # из репо; в контейнере (python - < файл) __file__ нет, бэк уже в PYTHONPATH
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web_app" / "backend"))
except NameError:
    pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="только показать, каких копий нет")
    parser.add_argument("--workers", type=int, default=2, help="параллельных ffmpeg (CPU веба делится с сайтом)")
    args = parser.parse_args(argv)

    from app import media_proxy
    from app.production_backend import get_backend

    backend = get_backend()
    store = backend.media_store()
    sources = backend.catalog_sources()
    versions = backend.catalog_versions(sources)

    jobs = []  # (подпись, есть ли уже, как сделать)
    for locator in sources:
        version = versions[locator]
        fetch = (lambda loc: (lambda path: backend.download_locator(loc, path)))(locator)
        if Path(locator).suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"}:
            clip = media_proxy.preview_name(locator, version)
            poster = media_proxy.preview_poster_name(locator, version)
            ready = store.has(clip) and store.has(poster)

            def build(clip=clip, poster=poster, fetch=fetch, locator=locator) -> None:
                media_proxy.build_preview(store, clip, fetch)
                # заставка — из готовой копии (источник по ссылке не понадобится)
                media_proxy.build_preview_poster(store, clip, poster, backend.source_url(locator))
        else:
            name = media_proxy.image_name(locator, version)
            ready = store.has(name)

            def build(name=name, fetch=fetch) -> None:
                media_proxy.build_image(store, name, fetch)
        jobs.append((locator, ready, build))

    missing = [(loc, build) for loc, ready, build in jobs if not ready]
    print(f"превью: всего {len(jobs)}, готово {len(jobs) - len(missing)}, сделать {len(missing)}")
    if args.dry_run:
        for loc, _ in missing:
            print(f"  нет копии: {loc}")
        return 0

    failed: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(build): loc for loc, build in missing}
        for future in as_completed(futures):
            loc = futures[future]
            try:
                future.result()
                print(f"  ✓ {loc}")
            except Exception as exc:  # причина — оператору, остальные копии продолжаем
                failed.append((loc, str(exc)))
                print(f"  ✗ {loc}: {exc}", file=sys.stderr)
    if failed:
        print(f"не удалось: {len(failed)} — эти карточки будут ждать ffmpeg (или падать) у посетителей", file=sys.stderr)
        return 1
    print("готово")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
