"""Эффекты Kant Tools (.ffx) в f3: манифест ↔ файлы ↔ контракт ↔ overlay."""
from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from mlcore.hooks.f3_effect import overlay  # noqa: E402
from validate_effects_registry import contract_ids  # noqa: E402

F3_DIR = ROOT / "mlcore/hooks/f3_effect"
MANIFEST = json.loads((F3_DIR / "manifest.json").read_text(encoding="utf-8"))
KANT = [e for e in MANIFEST["effects"] if e.get("preset")]
REGISTRY = json.loads((ROOT / "web_app/frontend/src/data/effects-registry.json").read_text(encoding="utf-8"))


def _b64_values(js: str) -> list[bytes]:
    return [base64.b64decode(m) for m in re.findall(r'presetB64: "([A-Za-z0-9+/=]+)"', js)]


def test_kant_entries_point_at_real_presets_and_the_shared_script():
    assert len(KANT) == 47
    for e in KANT:
        assert e["script"] == "kantfx/apply_kantfx.jsx"
        assert (F3_DIR / e["preset"]).is_file(), e["id"]
        assert e["mode"] == ("window" if e["group"] == "extra" else "cuts"), e["id"]
        if e["group"] == "transition":
            assert e["default_duration"] and e["default_duration"] >= 0.25, e["id"]


def test_kant_ids_are_selectable_everywhere():
    contract = contract_ids(ROOT)
    for e in KANT:
        if e["group"] == "extra":
            assert e["id"] in overlay.F3_EXTRAS
            assert e["id"] in contract["effect_extra"]
        else:
            assert e["id"] in overlay.F3_TRANSITIONS
            assert e["id"] in overlay.F3_CUT_TRANSITIONS  # монтажный стол ставит их на склейку
            assert e["id"] in contract["effect_transition"]


def test_registry_has_every_kant_effect_with_a_table_group():
    by_id = {r["manifestId"]: (group, r) for group in ("glue", "style") for r in REGISTRY[group]}
    for e in KANT:
        group, rec = by_id[e["id"]]
        assert group == ("style" if e["group"] == "extra" else "glue")
        assert rec["montageGroup"] and rec["montageGlyph"] and rec["meta"]
        assert (ROOT / "web_app/frontend/public/assets/figma" / rec["icon"]).is_file()


def test_extra_ships_the_preset_inside_the_jsx():
    js = overlay.build_overlay_jsx(extra="cc_tritone_red", drop_time=4.0)
    assert 'mode: "window"' in js
    assert _b64_values(js) == [(F3_DIR / "kantfx/cc_tritone_red.ffx").read_bytes()]


def test_kant_transition_on_a_table_cut_and_kant_style_on_a_window():
    js = overlay.build_overlay_jsx(
        cut_transitions=[{"t": 1.2, "id": "sh_twitch_flicker"}, {"t": 2.5, "id": "snap_wipe"}],
        extra_ranges=[{"id": "fx_universe_vhs", "start": 0.0, "end": 3.0}],
        drop_time=4.0,
    )
    assert 'mode: "cuts"' in js and 'mode: "window"' in js
    assert sorted(_b64_values(js), key=len) == sorted(
        [(F3_DIR / "kantfx/sh_twitch_flicker.ffx").read_bytes(), (F3_DIR / "kantfx/fx_universe_vhs.ffx").read_bytes()], key=len
    )


def test_regular_effects_do_not_get_preset_keys():
    js = overlay.build_overlay_jsx(extra="xerox", transition="snap_wipe", drop_time=4.0)
    assert _b64_values(js) == []


def test_preset_outside_the_pipeline_dir_is_refused():
    with pytest.raises(RuntimeError):
        overlay._preset_kv({"id": "x", "preset": "../manifest.json"})
