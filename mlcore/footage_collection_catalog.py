"""Standalone COLLECTION bucket catalog — untagged, folder-scoped footage pools.

A COLLECTION is the third bucket plane, next to the tag-based footage themes and
the ``visual:*`` / ``photo:*`` semantic contracts. It exists for source material
that must NOT be tagged and must NOT mix with anything else: one upload folder is
one selectable group, and a job that picks it draws clips from that folder and
from nowhere else.

Where the other planes decide membership SEMANTICALLY (tag overlap, facet
contracts), a collection decides it STRUCTURALLY: an asset belongs iff its
``(genre, tag)`` — which the ingest derives from the S3 key path
``<prefix>/<genre>/<tag>/<file>`` — equals the collection's folder. There is no
scoring inside a collection: every member is equally on-theme by construction,
so ranking degenerates to the picker's seeded order.

That structural rule is what makes the plane safe. It cannot leak INTO the
tag-based buckets (its assets carry no tags, and the plane keeps its own
inventory), and the tag-based pool cannot leak into it (a folder match is
required, not merely allowed).

Relevance to the lyrics is expressed ONCE PER COLLECTION in the registry
(``themes``: which track themes this collection suits), never per clip — that is
the whole point of the plane. A collection absent from every theme list still
appears, in registry order, in the shortlist tail.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

CATALOG_VERSION = "collection-v1-2026-08-05"

_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "data" / "footage_collections.json"

# The reserved first-level folders of the collection plane. A collection lives at
# <prefix>/<kind>/<slug>/, so the kind IS the S3 `genre` the ingest parses out.
COLLECTION_KINDS: Tuple[str, ...] = ("films", "people", "cine16x9")

# Output geometries a collection may be rendered at (see app.render_presets).
COLLECTION_FORMATS: Tuple[str, ...] = ("wide", "square", "vertical")

_BUCKET_PREFIX = "collection:"


def _n(v: Any) -> str:
    return " ".join(str(v or "").strip().lower().split())


@dataclass(frozen=True)
class CollectionBucket:
    """One upload folder, exposed as a pickable bucket.

    Duck-compatible with ``footage_bucket_catalog.Bucket`` /
    ``footage_visual_catalog.VisualContract`` so the shared Stage2B resolver and
    the picker adapter treat all three planes through one code path.
    """

    slug: str                       # "<kind>__<name>", unique across the plane
    label: str                      # RU label shown on the bot button
    kind: str                       # films | people | cine16x9  (== S3 genre)
    folder: str                     # S3 tag level — the upload folder name
    themes: Tuple[str, ...] = ()    # track themes this collection suits
    formats: Tuple[str, ...] = ()   # allowed output geometries
    description: str = ""           # RU one-liner for the shortlist caption

    @property
    def bucket_id(self) -> str:
        return f"{_BUCKET_PREFIX}{self.slug}"

    # ---- duck-compat with the other two catalog planes -------------------- #
    @property
    def theme(self) -> str:
        return "collection"

    @property
    def tags_group(self) -> str:
        return self.slug

    @property
    def mood(self) -> str:
        return ""

    @property
    def priority_tags(self) -> List[str]:
        """Identity sentinel, NOT a matching rule.

        ``FootageStyleRawFilters`` requires a non-empty ``priority_theme_tags``,
        but a collection matches on folder identity and never on tags. The picker
        short-circuits to the membership gate before any tag comparison happens,
        so this value is only ever carried, never compared. It is shaped so that
        it cannot collide with a real Qwen/Groq tag even if some future code path
        did compare it.
        """
        return [f"collection {self.kind} {self.folder}"]

    @property
    def exclude_tags(self) -> List[str]:
        return []

    @property
    def color(self) -> List[str]:
        return []

    @property
    def exclude(self) -> List[str]:
        return []

    @property
    def people(self) -> str:
        return "any"

    @property
    def default_format(self) -> str:
        return self.formats[0] if self.formats else "wide"


def _parse_row(row: Mapping[str, Any], *, index: int) -> CollectionBucket:
    kind = _n(row.get("kind"))
    if kind not in COLLECTION_KINDS:
        raise RuntimeError(
            f"footage_collections[{index}]: kind={kind!r} not in {list(COLLECTION_KINDS)}"
        )
    folder = str(row.get("folder") or "").strip()
    if not folder or "/" in folder or "\\" in folder:
        raise RuntimeError(
            f"footage_collections[{index}]: folder must be a single path segment, got {folder!r}"
        )
    label = str(row.get("label") or "").strip()
    if not label:
        raise RuntimeError(f"footage_collections[{index}]: label is required (bot button text)")

    formats = tuple(_n(x) for x in (row.get("formats") or ()) if _n(x))
    for f in formats:
        if f not in COLLECTION_FORMATS:
            raise RuntimeError(
                f"footage_collections[{index}]: format={f!r} not in {list(COLLECTION_FORMATS)}"
            )
    if not formats:
        # Films/people are shot wide; a collection that does not say otherwise
        # renders wide and square (the two formats the product offers for them).
        formats = ("wide", "square")

    themes = tuple(_n(x) for x in (row.get("themes") or ()) if _n(x))
    slug = f"{kind}__{folder}"
    return CollectionBucket(
        slug=slug,
        label=label,
        kind=kind,
        folder=folder,
        themes=themes,
        formats=formats,
        description=str(row.get("description") or "").strip(),
    )


def registry_path() -> Path:
    raw = str(os.environ.get("FOOTAGE_COLLECTIONS_JSON") or "").strip()
    return Path(raw) if raw else _REGISTRY_PATH


def load_collection_catalog(path: Path | None = None) -> List[CollectionBucket]:
    """All registered collections, in registry order.

    A MISSING registry means the plane has no collections yet (nothing uploaded)
    and yields an empty catalog — the same "empty pool is a legitimate state"
    rule the photo plane uses. A registry that exists but is malformed raises:
    that is operator error, not an empty state.
    """
    p = path if path is not None else registry_path()
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"footage collections registry is not valid JSON: {p}") from e
    rows = raw.get("collections")
    if rows is None:
        raise RuntimeError(f"footage collections registry has no 'collections' key: {p}")
    if not isinstance(rows, list):
        raise RuntimeError(f"footage collections registry 'collections' must be a list: {p}")

    out: List[CollectionBucket] = []
    seen: set = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise RuntimeError(f"footage_collections[{i}]: expected an object")
        bucket = _parse_row(row, index=i)
        if bucket.slug in seen:
            raise RuntimeError(f"footage_collections[{i}]: duplicate collection {bucket.slug!r}")
        seen.add(bucket.slug)
        out.append(bucket)
    return out


def find_collection(slug: str, *, catalog: List[CollectionBucket] | None = None) -> CollectionBucket:
    want = _n(slug)
    cat = catalog if catalog is not None else load_collection_catalog()
    for b in cat:
        if _n(b.slug) == want:
            return b
    raise RuntimeError(f"collection not found in registry: {slug!r}")


def load_collection_theme_buckets(
    *, catalog: List[CollectionBucket] | None = None
) -> Dict[str, List[str]]:
    """Track theme -> collection bucket ids, for the shortlist ranker.

    Built by inverting each collection's ``themes`` list, so the relevance
    statement lives once per collection in the registry instead of being spread
    across a separate mapping that could drift out of sync with it.

    Within one theme, collections are ordered by WHERE that theme sits in their
    own list: a collection that names a theme first is a better fit for it than
    one that names it fourth. Without this the order inside a theme fell back to
    registry order, i.e. alphabetical — so for a night-racing lyric "Великий
    Гэтсби" outranked "Токийский дрифт" purely because В precedes Т.
    """
    cat = catalog if catalog is not None else load_collection_catalog()
    ranked: Dict[str, List[Tuple[int, str]]] = {}
    for b in cat:
        for position, theme in enumerate(b.themes):
            ranked.setdefault(theme, []).append((position, b.bucket_id))
    return {
        theme: [bid for _, bid in sorted(rows, key=lambda r: (r[0], r[1]))]
        for theme, rows in ranked.items()
    }


def collections_for_kind(
    kind: str, *, catalog: List[CollectionBucket] | None = None
) -> List[CollectionBucket]:
    k = _n(kind)
    cat = catalog if catalog is not None else load_collection_catalog()
    return [b for b in cat if b.kind == k]


def is_collection_bucket_id(bucket_id: Any) -> bool:
    return str(bucket_id or "").strip().startswith(_BUCKET_PREFIX)


def evaluate(bucket: CollectionBucket, asset: Mapping[str, Any]) -> Tuple[bool, str]:
    """Membership gate for one clip: does it live in this collection's folder?

    Structural, not semantic — no tags, colors or people are consulted, because
    a collection makes no claim about its contents beyond "the operator put these
    files here together".
    """
    genre = _n(asset.get("genre"))
    folder = _n(asset.get("tag"))
    if not genre or not folder:
        return False, "unfiled"
    if genre != _n(bucket.kind):
        return False, "kind"
    if folder != _n(bucket.folder):
        return False, "folder"
    return True, "eligible"
