from __future__ import annotations

from services.orchestrator import collection_catalog_source


class _Row(dict):
    pass


class _Connection:
    async def fetch(self, query, kind):
        assert "source = 'collection'" in query
        assert kind == "cine16x9"
        return [
            _Row(genre="cine16x9", tag="Boston"),
            _Row(genre="cine16x9", tag="JDM"),
        ]

    async def close(self):
        return None


def test_rank_catalog_discovers_folders_from_postgres(monkeypatch, tmp_path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(
        '{"collections":[{"kind":"cine16x9","folder":"Boston","label":"Бостон"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("FOOTAGE_COLLECTIONS_JSON", str(registry))
    async def _connect(*, dsn):
        assert dsn == "postgresql://test"
        return _Connection()

    import asyncpg

    monkeypatch.setattr(asyncpg, "connect", _connect, raising=False)
    catalog = collection_catalog_source.load_collection_catalog_from_postgres(
        "cine16x9", db_url="postgresql://test"
    )

    assert [b.slug for b in catalog] == ["cine16x9__Boston", "cine16x9__JDM"]
    assert [b.label for b in catalog] == ["Бостон", "JDM"]
