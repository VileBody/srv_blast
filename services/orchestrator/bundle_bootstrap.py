# services/orchestrator/bundle_bootstrap.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import os
import time

from mlcore.descriptions_bundle import build_descriptions_bundle_from_inventory


@dataclass(frozen=True)
class BundleBootstrapResult:
    ok: bool
    action: str  # "created" | "rebuilt" | "kept" | "failed"
    bundle_path: Path
    inventory_path: Path
    reason: str = ""


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except Exception:
        return 0.0


def ensure_descriptions_bundle(
    *,
    inventory_json: Path,
    bundle_path: Path,
    max_assets: Optional[int] = None,
    force_rebuild: bool = False,
) -> BundleBootstrapResult:
    """
    Ensure a SINGLE global descriptions bundle exists and is up-to-date.

    Rebuild conditions:
      - bundle missing
      - inventory newer than bundle
      - force_rebuild=True
    """
    try:
        inventory_json = inventory_json.resolve()
        bundle_path = bundle_path.resolve()
        bundle_path.parent.mkdir(parents=True, exist_ok=True)

        if not inventory_json.exists():
            return BundleBootstrapResult(
                ok=False,
                action="failed",
                bundle_path=bundle_path,
                inventory_path=inventory_json,
                reason=f"inventory_missing: {inventory_json}",
            )

        inv_m = _mtime(inventory_json)
        bun_m = _mtime(bundle_path)

        need = force_rebuild or (not bundle_path.exists()) or (inv_m > bun_m + 0.0001)

        if not need:
            return BundleBootstrapResult(
                ok=True,
                action="kept",
                bundle_path=bundle_path,
                inventory_path=inventory_json,
                reason="bundle_up_to_date",
            )

        build_descriptions_bundle_from_inventory(
            inventory_json=inventory_json,
            out_path=bundle_path,
            max_assets=max_assets,
        )

        action = "created" if bun_m <= 0.0 else "rebuilt"
        return BundleBootstrapResult(
            ok=True,
            action=action,
            bundle_path=bundle_path,
            inventory_path=inventory_json,
            reason="rebuilt" if action == "rebuilt" else "created",
        )

    except Exception as e:
        return BundleBootstrapResult(
            ok=False,
            action="failed",
            bundle_path=bundle_path,
            inventory_path=inventory_json,
            reason=f"exception: {e!r}",
        )
