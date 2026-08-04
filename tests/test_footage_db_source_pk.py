# -*- coding: utf-8 -*-
"""The registry and tag tables are keyed by (source, clip_id), not clip_id alone.

Both pools share ONE numeric id space: the same Pinterest id can be a .mp4 under
the video prefix and a .jpg under the photo prefix. With clip_id as the sole
primary key a photo upsert did not collide with the video row — it OVERWROTE it
and flipped `source`, so the clip silently left the video pool. A production audit
found 221 such collisions (video S3 = 2090, video tag rows = 1869 — exactly 221
short), which is why these are invariants and not style preferences.
"""
from __future__ import annotations

import re

from mlcore import footage_assets_db, footage_tags_db


TABLES = (
    (footage_tags_db, "footage_tags"),
    (footage_assets_db, "footage_assets"),
)


def test_tables_are_keyed_by_source_and_clip_id():
    for module, table in TABLES:
        assert "PRIMARY KEY (source, clip_id)" in module.SCHEMA, table
        # the single-column key must be gone from the CREATE TABLE body
        assert not re.search(r"clip_id\s+TEXT\s+PRIMARY KEY", module.SCHEMA), table


def test_upserts_target_the_composite_key():
    """ON CONFLICT (clip_id) would still overwrite across pools even with the new
    primary key in place — it is the half of the fix that actually stops writes."""
    for module, table in TABLES:
        src = open(module.__file__, encoding="utf-8").read()
        assert "ON CONFLICT (source, clip_id) DO UPDATE SET" in src, table
        assert "ON CONFLICT (clip_id)" not in src, table


def test_pk_migration_is_guarded_so_it_stays_idempotent():
    """init_schema runs this DDL on every connect, so re-running must be a no-op:
    it may only fire while the primary key is still single-column."""
    for module, table in TABLES:
        assert f"ALTER TABLE {table} DROP CONSTRAINT {table}_pkey" in module.SCHEMA, table
        assert f"ALTER TABLE {table} ADD PRIMARY KEY (source, clip_id)" in module.SCHEMA, table
        assert "indisprimary" in module.SCHEMA, table
        assert "array_length(i.indkey::int2[], 1) = 1" in module.SCHEMA, table


def test_writes_stay_scoped_to_one_pool():
    """A cross-pool write is what destroyed the video rows, so every mutating
    statement must carry a source predicate."""
    for module, table in TABLES:
        src = open(module.__file__, encoding="utf-8").read()
        # SQL is often split across adjacent Python string literals — join them
        # back so a predicate on the next line still counts as part of the query.
        flat = re.sub(r"[\"']\s*\+?\s*[\"']", " ", src)
        for stmt in re.findall(rf"(?:UPDATE|DELETE FROM) {table}.{{0,200}}", flat, flags=re.S):
            head = stmt.split(";")[0]
            assert "source" in head, (table, head[:120])
