"""Shared collection-catalog discovery for bot-facing orchestrator routes."""
from __future__ import annotations

import asyncio


def load_collection_catalog_from_postgres(kind: str, *, db_url: str) -> list:
    """Build a collection catalog from the durable shared asset registry.

    Activation may run on any worker node, while rank-buckets may be served by
    another API node. The activation JSON is node-local; footage_assets is the
    shared record activation already replaces, so it is the valid discovery
    source for a bot-facing catalog.
    """
    dsn = str(db_url or "").strip()
    if not dsn:
        raise RuntimeError("Postgres not configured for collection catalog")

    async def _fetch() -> list[tuple[str, str]]:
        import asyncpg  # type: ignore

        conn = await asyncpg.connect(dsn=dsn)
        try:
            rows = await conn.fetch(
                """
                SELECT DISTINCT genre, tag
                FROM footage_assets
                WHERE source = 'collection'
                  AND lower(genre) = lower($1)
                  AND btrim(tag) <> ''
                ORDER BY genre, tag
                """,
                str(kind or "").strip(),
            )
            return [(str(row["genre"]), str(row["tag"])) for row in rows]
        finally:
            await conn.close()

    from mlcore.footage_collection_catalog import collections_for_kind, load_collection_catalog

    folders = asyncio.run(_fetch())
    return collections_for_kind(
        kind,
        catalog=load_collection_catalog(discovered_folders=folders),
    )
