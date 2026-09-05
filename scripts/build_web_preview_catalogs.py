#!/usr/bin/env python3
"""Собрать каталоги превью для web_app из ЖИВЫХ источников бота.

Превью в проекте живут как Telegram `file_id` (их снимали `register_hook_previews.py`
и `build_bucket_previews.py`). Сайту file_id бесполезен — нужен HTTP(S)-объект,
поэтому здесь: `file_id → getFile → download → S3 → каталог`.

## Планы (ровно те, что показывает бот)

    plane        живой каталог                                   стор превью
    ----------   ---------------------------------------------   -----------------------------
    vibes 9:16   mlcore.footage_visual_catalog.load_visual_catalog()   footage_bucket_previews.json
    cine16x9     data/footage_collections.json (kind=cine16x9)         collection_bucket_previews.json
    films        data/footage_collections.json (kind=films)            collection_bucket_previews.json
    photo        mlcore.photo_bucket_catalog.load_photo_catalog()      photo_bucket_previews.json
    subtitle     core.subtitles_mode (режимы)                          hook_previews.json (subtitles:*)
    fx           реестр эффектов + литералы SendAudioS3Request          hook_previews.json (effect_*/motion:/shape:)

Списки берутся ИЗ ЖИВЫХ каталогов, а не из сторов превью: в
`footage_bucket_previews.json` лежит 81 запись, из которых актуальны 23 —
остальные 58 это прошлая (theme:tags_group) таксономия. Показать их на сайте =
показать то, чего в подборе больше нет.

`data/footage_bucket_previews_site.json` НЕ используется: те же 23 id, но пустые
file_id/s3_url, и ни бот, ни оркестратор его не читают — брошенный дубль.

## Как выбор доезжает до рендера

`bucket_id` не абстрактный ключ, он раскладывается в поля `SendAudioS3Request`:

    visual:<slug>          → rotation_theme="visual",     rotation_tags_group=<slug>
    collection:<kind>__<f> → rotation_theme="collection", rotation_tags_group=<folder>
    photo:<slug>           → bg_mode="photo" + та же пара
    render_preset            для коллекций = default_format из footage_collections.json,
                             иначе "vertical" (см. _render_preset_for_bucket в боте)

Эти поля скрипт кладёт в каталог рядом с превью (`selector`), чтобы бэкенду не
пришлось выводить их заново — см. §«Что менять в production_backend» в хендоффе.

## Запуск

    PYTHONIOENCODING=utf-8 python scripts/build_web_preview_catalogs.py --dry-run

Боевой (нужен токен бота, чьи file_id_public лежат в сторах, и ключи S3):

    export TG_PREVIEW_SOURCE_BOT_TOKEN=... S3_ENDPOINT_URL=... S3_ACCESS_KEY_ID=... \
           S3_SECRET_ACCESS_KEY=... S3_REGION=... S3_BUCKET_ASSET_STORAGE=... \
           S3_WEB_ASSET_PREFIX=app/blast808
    python scripts/build_web_preview_catalogs.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
COLLECTIONS = DATA / "footage_collections.json"

PREVIEW_STORES = {
    "video": DATA / "footage_bucket_previews.json",
    "photo": DATA / "photo_bucket_previews.json",
    "collection": DATA / "collection_bucket_previews.json",
    "hook": DATA / "hook_previews.json",
}

# Отображаемое имя стиля субтитров -> mode id пайплайна (core.subtitles_mode).
# Ключи должны совпадать с WEB_SUBTITLE_MODE_MAP_JSON.
SUBTITLE_MODES = {
    "Brat": "brat_5th",
    "Trendy": "trendy_5th",
    "Impulse": "impulse_2nd",
    "Jakson": "scenes_3rd",
    "Tape": "template_4th",
}


class BuildError(RuntimeError):
    pass


@dataclass
class Entry:
    plane: str                              # vibes | cine16x9 | films | photo | subtitle | fx
    id: str                                 # bucket_id / mode id / fx id — стабильный ключ
    name: str                               # подпись на сайте
    selector: dict[str, Any] = field(default_factory=dict)
    store: str = "video"                    # какой стор превью спрашивать
    preview_key: str = ""                   # ключ внутри стора (обычно == id)
    s3_url: str = ""
    local_path: Path | None = None
    file_id: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def resolvable(self) -> bool:
        return bool(self.s3_url or self.local_path or self.file_id)

    @property
    def source(self) -> str:
        if self.s3_url:
            return "s3"
        if self.local_path:
            return "local"
        return "telegram"


def _load(path: Path) -> Any:
    if not path.exists():
        raise BuildError(f"нет файла-источника: {path}")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


_STORE_CACHE: dict[str, dict[str, Any]] = {}


def _store(name: str) -> dict[str, Any]:
    if name not in _STORE_CACHE:
        payload = _load(PREVIEW_STORES[name])
        _STORE_CACHE[name] = payload.get("previews") or {}
    return _STORE_CACHE[name]


def _attach_preview(entry: Entry) -> Entry:
    """Найти видео примера. Три источника, в порядке предпочтения:

    1. `s3_url` из стора — коллекции рендерились через ноду и уже лежат в S3;
    2. ЛОКАЛЬНЫЙ файл — вайбы (`outputs/bucket_previews_v2/site`) и примеры
       эффектов/субтитров (`Desktop/АЕ/...`) рендерились локально, и в сторе у них
       остался только `file_id`;
    3. Telegram `file_id` — последний вариант.

    Порядок не случайный: file_id ПРИВЯЗАН К БОТУ, который его снял. Токен бота
    верификации на этих id отвечает `wrong file_id` (проверено). То есть без
    токена паблик-бота третий путь недоступен вообще, а первые два — доступны
    всегда. Отсутствие всех трёх — не ошибка сборки, а строка в отчёте.
    """
    entry.local_path = _local_source(entry)
    rec = _store(entry.store).get(entry.preview_key or entry.id)
    if not isinstance(rec, dict):
        if not entry.local_path:
            entry.notes.append(f"нет записи в {PREVIEW_STORES[entry.store].name}")
        return entry
    entry.s3_url = str(rec.get("s3_url") or "").strip()
    entry.file_id = (
        str(rec.get("file_id_public") or "").strip()
        or str(rec.get("file_id") or "").strip()
    )
    if not entry.resolvable:
        entry.notes.append("нет ни локального файла, ни s3_url, ни file_id")
    return entry


# Локальные рендеры превью. Вайбы и фото собирались "local-only run" —
# `build_bucket_previews.py` в этой ветке пишет `entry.s3_url = ""` и оставляет
# только file_id, а сам mp4 кладёт сюда. Вариант `site` отличается от `bot`
# оформлением, и сайту нужен именно он.
def _outputs_roots() -> tuple[Path, ...]:
    """Где искать `outputs/`.

    `outputs/` не в гите, поэтому в worktree его нет — а рендеры лежат в основном
    чекауте. Основной worktree находим через git, а не хардкодим путь.
    """
    roots = []
    override = os.getenv("BLAST_OUTPUTS_ROOT")
    if override:
        roots.append(Path(override))
    roots.append(ROOT / "outputs")
    try:
        # НЕ text=True: git отдаёт путь в UTF-8, а на этой машине дефолтная
        # локаль cp1251 — «Пользователь» превращается в мусор и путь не находится.
        import subprocess

        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=ROOT, capture_output=True, timeout=15,
        ).stdout.decode("utf-8", "replace").strip()
        if common:
            roots.append(Path(common).parent / "outputs")
    except Exception:
        pass
    return tuple(dict.fromkeys(r for r in roots if r.is_dir()))


VIBE_PREVIEW_SUBDIRS = (
    # `site` отличается от `bot` оформлением — сайту нужен именно он.
    Path("bucket_previews_v2") / "site",
    Path("bucket_previews_v2") / "bot",
    Path("bucket_previews"),
)

# Примеры хуков/эффектов/субтитров лежат папками с исходниками AE. Карту
# "ключ -> (папка, файл)" не дублируем: она уже есть в register_hook_previews.py
# и разъехаться с ней нельзя — там же её единственный смысл.
_HOOK_EXAMPLES: dict[str, Path] | None = None


def _hook_examples() -> dict[str, Path]:
    global _HOOK_EXAMPLES
    if _HOOK_EXAMPLES is None:
        import importlib.util

        script = ROOT / "scripts" / "register_hook_previews.py"
        spec = importlib.util.spec_from_file_location("_rhp", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        root = Path(os.getenv("BLAST_EXAMPLES_ROOT") or module.DEFAULT_EXAMPLES_ROOT)
        _HOOK_EXAMPLES = {
            key: root / folder / name for key, (folder, name, _label) in module.EXAMPLES.items()
        }
    return _HOOK_EXAMPLES


def _local_source(entry: Entry) -> Path | None:
    """Локальный mp4 примера, если он есть на этой машине."""
    if entry.store == "hook":
        candidate = _hook_examples().get(entry.preview_key or entry.id)
        return candidate if candidate and candidate.exists() else None
    # ТОЛЬКО vibes: фото-бакеты называются теми же хвостами (`photo:forest_fog_dark`
    # и `visual:forest_fog_dark`), и общий поиск подсунул бы фото-опции превью
    # футажа. Своих локальных рендеров у фото на этой машине нет.
    if entry.plane == "vibes":
        # Файлы названы по bucket_id: `visual:x` -> `x.mp4` (v2) либо
        # `theme__group.mp4` (старая раскладка).
        _, _, tail = entry.id.partition(":")
        names = (f"{tail}.mp4", f"{entry.id.replace(':', '__')}.mp4")
        for outputs in _outputs_roots():
            for subdir in VIBE_PREVIEW_SUBDIRS:
                for name in names:
                    candidate = outputs / subdir / name
                    if candidate.exists():
                        return candidate
    return None


def _slug(value: str) -> str:
    """Ключ S3 и имя файла из bucket_id.

    ГОЧА: у фильмов папка русская (`collection:films__брат`), и голая замена
    не-ASCII на дефис схлопывает ВСЕ двенадцать в один-два одинаковых имени —
    файлы затирают друг друга, а в S3 остаётся один объект на всю коллекцию.
    Поэтому, если что-то было заменено, к слагу добавляется короткий хэш
    исходного id: имена остаются читаемыми и при этом гарантированно разные.
    """
    raw = str(value)
    out = []
    lossy = False
    for ch in raw:
        if ch.isalnum() and ch.isascii():
            out.append(ch.lower())
        elif ch in "-_":
            out.append(ch)
        else:
            out.append("-")
            lossy = lossy or not ch.isascii()
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    if lossy:
        import hashlib

        slug = f"{slug}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:8]}".strip("-")
    if not slug:
        raise BuildError(f"пустой slug из {value!r}")
    return slug


# --------------------------------------------------------------------------- #
# Планы
# --------------------------------------------------------------------------- #
def collect_vibes() -> list[Entry]:
    """Вертикальный семантический футаж 9:16 — то, что бот зовёт «вайбы»."""
    from mlcore.footage_visual_catalog import load_visual_catalog

    entries = []
    for bucket in load_visual_catalog():
        entries.append(
            _attach_preview(
                Entry(
                    plane="vibes",
                    id=bucket.bucket_id,
                    name=str(bucket.label),
                    selector={
                        "rotationTheme": str(bucket.theme),
                        "rotationTagsGroup": str(bucket.tags_group),
                        "renderPreset": "vertical",
                        "bgMode": "footage",
                    },
                    store="video",
                )
            )
        )
    return entries


def collect_collections(kind: str) -> list[Entry]:
    """Коллекции: cine16x9 (16:9) и films (фильмы). Папка = группа, формат свой.

    Каталог грузим штатным загрузчиком, а не разбором JSON: у записи без `formats`
    формат добирается из DEFAULT_FORMATS_BY_KIND (films → vertical, cine16x9 →
    wide), и повторять эту логику руками — прямой путь отдать 16:9 в вертикаль.
    `default_format` здесь тот же, что читает `_render_preset_for_bucket` в боте.
    """
    from mlcore.footage_collection_catalog import collections_for_kind

    entries = []
    for coll in collections_for_kind(kind):
        folder = str(coll.folder)
        bucket_id = f"collection:{kind}__{folder}"
        entries.append(
            _attach_preview(
                Entry(
                    plane=kind,
                    id=bucket_id,
                    name=str(coll.label or folder),
                    selector={
                        "rotationTheme": "collection",
                        "rotationTagsGroup": folder,
                        "renderPreset": str(coll.default_format),
                        "bgMode": "footage",
                    },
                    store="collection",
                    preview_key=bucket_id,
                )
            )
        )
    return entries


def collect_photo() -> list[Entry]:
    from mlcore.photo_bucket_catalog import load_photo_catalog

    entries = []
    for bucket in load_photo_catalog():
        entries.append(
            _attach_preview(
                Entry(
                    plane="photo",
                    id=bucket.bucket_id,
                    name=str(bucket.label),
                    selector={
                        "rotationTheme": str(bucket.theme),
                        "rotationTagsGroup": str(bucket.tags_group),
                        # Фото-флоу — отдельная геометрия 4:3, её задаёт bg_mode,
                        # а не render_preset (см. SendAudioS3Request.bg_mode).
                        "renderPreset": "vertical",
                        "bgMode": "photo",
                    },
                    store="photo",
                )
            )
        )
    return entries


def collect_subtitles() -> list[Entry]:
    entries = []
    for name, mode_id in SUBTITLE_MODES.items():
        entries.append(
            _attach_preview(
                Entry(
                    plane="subtitle",
                    id=mode_id,
                    name=name,
                    selector={"subtitlesMode": mode_id},
                    store="hook",
                    preview_key=f"subtitles:{mode_id}",
                )
            )
        )
    return entries


# hook_previews-ключ -> поле SendAudioS3Request. Литералы схемы — источник правды
# по допустимым значениям, поэтому категория задаёт поле, а id идёт как есть.
_FX_FIELD_BY_CATEGORY = {
    "effect_hook": "effectHook",
    "effect_transition": "effectTransition",
    "effect_extra": "effectExtra",
    "motion": "f4Device",
    "shape": "f2Shape",
}


def collect_fx() -> list[Entry]:
    """Хуки / переходы / стили / движение / шейпы — всё, что записано в hook_previews."""
    entries = []
    for key, rec in _store("hook").items():
        category, _, fx_id = str(key).partition(":")
        if category == "subtitles":
            continue  # это отдельный план
        field_name = _FX_FIELD_BY_CATEGORY.get(category)
        if not field_name:
            raise BuildError(f"неизвестная категория превью {key!r} — обнови _FX_FIELD_BY_CATEGORY")
        entries.append(
            _attach_preview(
                Entry(
                    plane="fx",
                    id=f"{category}__{fx_id}",
                    name=str(rec.get("label") or fx_id),
                    selector={field_name: fx_id},
                    store="hook",
                    preview_key=key,
                )
            )
        )
    return entries


# --------------------------------------------------------------------------- #
# Telegram -> S3
# --------------------------------------------------------------------------- #
class Uploader:
    def __init__(self, *, tg_token: str, bucket: str, prefix: str) -> None:
        import boto3
        import requests
        from botocore.config import Config

        # trust_env=False — как в register_hook_previews.py: системный SOCKS-прокси
        # Windows ломает прямые вызовы, а Telegram/S3 доступны напрямую.
        self._session = requests.Session()
        self._session.trust_env = False
        self._token = tg_token
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._s3 = boto3.client(
            "s3",
            endpoint_url=_env("S3_ENDPOINT_URL"),
            aws_access_key_id=_env("S3_ACCESS_KEY_ID"),
            aws_secret_access_key=_env("S3_SECRET_ACCESS_KEY"),
            region_name=_env("S3_REGION"),
            config=Config(signature_version="s3v4"),
        )

    def key_for(self, entry: Entry) -> str:
        return f"{self._prefix}/previews/{entry.plane}/{_slug(entry.id)}.mp4"

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._s3.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            if str(exc.response.get("Error", {}).get("Code")) in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def download(self, file_id: str) -> bytes:
        info = self._session.get(
            f"https://api.telegram.org/bot{self._token}/getFile",
            params={"file_id": file_id},
            timeout=60,
        )
        info.raise_for_status()
        payload = info.json()
        if not payload.get("ok"):
            raise BuildError(f"telegram getFile не ok: {payload}")
        file_path = str((payload.get("result") or {}).get("file_path") or "").strip()
        if not file_path:
            raise BuildError(f"telegram getFile без file_path: {payload}")
        blob = self._session.get(
            f"https://api.telegram.org/file/bot{self._token}/{file_path}", timeout=300
        )
        blob.raise_for_status()
        return blob.content

    def read_local(self, path: Path) -> bytes:
        return path.read_bytes()

    def upload(self, key: str, body: bytes) -> None:
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=body, ContentType="video/mp4")

    def locator(self, key: str) -> str:
        return f"s3://{self._bucket}/{key}"


def _env(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise BuildError(f"нужна переменная окружения {name}")
    return value


def build(entries: list[Entry], uploader: Uploader | None, *, force: bool) -> tuple[list[dict], list[Entry]]:
    catalog: list[dict[str, Any]] = []
    skipped: list[Entry] = []
    for entry in entries:
        if not entry.resolvable:
            skipped.append(entry)
            continue
        if entry.s3_url:
            preview_url = entry.s3_url
        elif uploader is None:
            preview_url = f"s3://<bucket>/<prefix>/previews/{entry.plane}/{_slug(entry.id)}.mp4"
        else:
            key = uploader.key_for(entry)
            if force or not uploader.exists(key):
                body = (
                    uploader.read_local(entry.local_path)
                    if entry.local_path
                    else uploader.download(entry.file_id)
                )
                uploader.upload(key, body)
                print(f"  ↑ {key}  ({entry.source})")
            else:
                print(f"  = {key}")
            preview_url = uploader.locator(key)
        catalog.append(
            {
                "id": entry.id,
                "name": entry.name,
                "plane": entry.plane,
                "previewUrl": preview_url,
                "selector": entry.selector,
                "score": 1.0,
            }
        )
    return catalog, skipped


def _fetch_only(planes: dict[str, list[Entry]], out_dir: Path, *, force: bool) -> int:
    """Скачать все превью в папку. Без S3, только Telegram и локальные копии.

    Отдельный режим, потому что ключи S3 и токен бота живут в разных местах: файлы
    можно собрать и проверить заранее, а залить потом — с машины, где есть доступ
    к бакету.
    """
    import requests

    token = _env("TG_PREVIEW_SOURCE_BOT_TOKEN")
    session = requests.Session()
    session.trust_env = False  # системный SOCKS-прокси ломает прямой вызов Telegram
    failed = 0
    for plane, entries in planes.items():
        plane_dir = out_dir / plane
        plane_dir.mkdir(parents=True, exist_ok=True)
        ok = 0
        for entry in entries:
            target = plane_dir / f"{_slug(entry.id)}.mp4"
            if target.exists() and not force:
                ok += 1
                continue
            try:
                if entry.s3_url:
                    # видео уже в S3 (коллекции) — качать копию сюда незачем
                    ok += 1
                    continue
                if entry.local_path:
                    target.write_bytes(entry.local_path.read_bytes())
                elif entry.file_id:
                    info = session.get(
                        f"https://api.telegram.org/bot{token}/getFile",
                        params={"file_id": entry.file_id}, timeout=60,
                    ).json()
                    if not info.get("ok"):
                        raise BuildError(str(info.get("description") or info))
                    path = str((info.get("result") or {}).get("file_path") or "")
                    blob = session.get(
                        f"https://api.telegram.org/file/bot{token}/{path}", timeout=300
                    )
                    blob.raise_for_status()
                    target.write_bytes(blob.content)
                else:
                    raise BuildError("нет источника")
                ok += 1
            except Exception as exc:
                failed += 1
                print(f"  ! {plane}/{entry.id}: {exc}")
        print(f"[{plane}] готово {ok} из {len(entries)}")
    print(f"\nСкачано в {out_dir}")
    if failed:
        print(f"НЕ УДАЛОСЬ: {failed}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="ничего не качать и не лить")
    parser.add_argument("--out", default="web_app/backend/data/web_preview_catalogs")
    parser.add_argument("--force", action="store_true", help="перезалить, даже если объект уже в S3")
    parser.add_argument(
        "--fetch-dir",
        default="",
        help="скачать превью в папку и НЕ трогать S3 (нужен только TG_PREVIEW_SOURCE_BOT_TOKEN). "
             "Полезно, чтобы проверить, что все file_id живы, до того как появятся ключи S3.",
    )
    args = parser.parse_args()

    planes = {
        "vibes": collect_vibes(),
        "cine16x9": collect_collections("cine16x9"),
        "films": collect_collections("films"),
        "photo": collect_photo(),
        "subtitle": collect_subtitles(),
        "fx": collect_fx(),
    }

    if args.fetch_dir:
        return _fetch_only(planes, Path(args.fetch_dir), force=args.force)

    uploader = None
    if not args.dry_run:
        uploader = Uploader(
            tg_token=_env("TG_PREVIEW_SOURCE_BOT_TOKEN"),
            bucket=_env("S3_BUCKET_ASSET_STORAGE"),
            prefix=_env("S3_WEB_ASSET_PREFIX"),
        )

    catalogs: dict[str, list[dict]] = {}
    incomplete = False
    for plane, entries in planes.items():
        catalogs[plane], skipped = build(entries, uploader, force=args.force)
        # Разбивка по источнику важна практически: `telegram` — единственный,
        # который нельзя выполнить без токена паблик-бота.
        by_source: dict[str, int] = {}
        for entry in entries:
            if entry.resolvable:
                by_source[entry.source] = by_source.get(entry.source, 0) + 1
        breakdown = ", ".join(f"{name}={count}" for name, count in sorted(by_source.items()))
        print(f"[{plane}] в каталоге {len(catalogs[plane])} из {len(entries)}  [{breakdown}]")
        for entry in skipped:
            incomplete = True
            print(f"    - БЕЗ ПРЕВЬЮ {entry.id} «{entry.name}»: {'; '.join(entry.notes)}")

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    for plane, items in catalogs.items():
        (out_dir / f"{plane}.json").write_text(
            json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def compact(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    # Футаж-каталог сайта = три плана одним списком: сайт показывает их как три
    # вкладки, а бэкенду важен только selector внутри записи.
    footage = catalogs["vibes"] + catalogs["cine16x9"] + catalogs["films"]
    fragment = "\n".join(
        [
            f"WEB_SUBTITLE_MODE_MAP_JSON={compact({e['name']: e['selector']['subtitlesMode'] for e in catalogs['subtitle']})}",
            f"WEB_FOOTAGE_CATALOG_JSON={compact(footage)}",
            f"WEB_PHOTO_CATALOG_JSON={compact(catalogs['photo'])}",
            f"WEB_SUBTITLE_CATALOG_JSON={compact(catalogs['subtitle'])}",
            f"WEB_FX_CATALOG_JSON={compact(catalogs['fx'])}",
        ]
    )
    (out_dir / "env_fragment.txt").write_text(fragment + "\n", encoding="utf-8")

    print(f"\nЗаписано в {out_dir}")
    if args.dry_run:
        print("  (--dry-run: previewUrl — плейсхолдеры, в S3 ничего не залито)")
    if incomplete:
        print("\nЧасть опций без превью — им нужно снять пример (register_hook_previews.py /")
        print("build_bucket_previews.py) ИЛИ не показывать их на сайте. Список выше.")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BuildError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        sys.exit(2)
