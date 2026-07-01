"""footage_config.py runs as a container PRESTART gate (`python footage_config.py
&& <server>`). A missing static index (regenerable cache, not tracked in git)
must NOT crash the container — it degrades gracefully so the service boots and
activation can build the pool.
"""
from __future__ import annotations

from pathlib import Path

import footage_config


def test_main_does_not_crash_when_index_missing(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "static_assets_index_1to1.json"
    assert not missing.exists()
    monkeypatch.setenv("STATIC_ASSETS_INDEX_JSON", str(missing))
    monkeypatch.setenv("MEDIA_TYPE", "video")

    # must return cleanly (no FileNotFoundError / SystemExit)
    footage_config.main()

    out = capsys.readouterr().out
    assert "static assets index missing" in out.lower()
    assert "booting anyway" in out.lower()


def test_main_builds_when_index_present(tmp_path, monkeypatch):
    idx = tmp_path / "static_assets_index_1to1.json"
    idx.write_text('{"assets": []}', encoding="utf-8")
    inv = tmp_path / "footage_inventory.json"
    bundle = tmp_path / "descriptions_bundle.json"
    monkeypatch.setenv("STATIC_ASSETS_INDEX_JSON", str(idx))
    monkeypatch.setenv("FOOTAGE_INVENTORY_OUT", str(inv))
    monkeypatch.setenv("DESCRIPTIONS_BUNDLE_OUT", str(bundle))
    monkeypatch.setenv("MEDIA_TYPE", "video")
    monkeypatch.setenv("MODE", "dev")

    footage_config.main()  # empty pool builds an empty inventory without error
    assert inv.exists()
