"""Gate on the SHIPPED collection registry — a hand-edited file.

Its `folder` values must match S3 byte for byte and its `themes` must be real
track themes; both are typed by a human and neither fails loudly at runtime (a
misspelled theme simply never matches, a misspelled folder yields an empty
group). Catching that here is the difference between a red test and a bot menu
entry that shows nothing.
"""
from __future__ import annotations

from mlcore.footage_bucket_ranker import candidate_themes
from mlcore.footage_collection_catalog import (
    COLLECTION_FORMATS,
    COLLECTION_KINDS,
    load_collection_catalog,
    load_collection_theme_buckets,
)


def test_the_shipped_registry_loads() -> None:
    # Parsing is strict, so this also covers kind/folder/label validation.
    assert load_collection_catalog() is not None


def test_every_theme_is_a_real_track_theme() -> None:
    valid = set(candidate_themes(""))
    unknown = {
        b.slug: [t for t in b.themes if t not in valid]
        for b in load_collection_catalog()
        if any(t not in valid for t in b.themes)
    }
    assert not unknown, f"unknown themes (they would silently never match): {unknown}"


def test_slugs_and_labels_are_unique() -> None:
    cat = load_collection_catalog()
    assert len({b.slug for b in cat}) == len(cat)
    # Duplicate labels are legal but would look like a bug to the user.
    labels = [b.label for b in cat]
    assert len(set(labels)) == len(labels), "two collections share a bot button label"


def test_kinds_and_formats_are_in_the_allowed_sets() -> None:
    for b in load_collection_catalog():
        assert b.kind in COLLECTION_KINDS
        assert b.formats, f"{b.slug} has no output format"
        for f in b.formats:
            assert f in COLLECTION_FORMATS


def test_folders_carry_no_stray_whitespace_or_case_surprises() -> None:
    # The folder is compared against the S3 path; a trailing space is invisible
    # in an editor and yields an empty collection.
    for b in load_collection_catalog():
        assert b.folder == b.folder.strip()
        assert "  " not in b.folder


def test_theme_expansion_respects_declared_priority() -> None:
    # A collection that names a theme FIRST must outrank one that names it later,
    # otherwise the order inside a theme is just alphabetical.
    cat = load_collection_catalog()
    by_id = {b.bucket_id: b for b in cat}
    for theme, ids in load_collection_theme_buckets(catalog=cat).items():
        positions = [by_id[i].themes.index(theme) for i in ids]
        assert positions == sorted(positions), f"{theme}: {positions}"


def test_bucket_ids_round_trip_through_the_slot_resolver() -> None:
    # The bot ships the bucket_id as a string and the orchestrator splits it back
    # into (theme, group); Cyrillic folders and spaces must survive that.
    from mlcore.footage_batch_distribution import resolve_bucket_slot

    for b in load_collection_catalog():
        theme, group = resolve_bucket_slot(b.bucket_id, catalog=[])
        assert theme == "collection"
        assert group == b.slug
