"""Эффекты Kant Tools (.ffx) в f3: манифест ↔ файлы ↔ контракт ↔ overlay."""
from __future__ import annotations

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


def _preset_values(js: str) -> list[bytes]:
    """presetBin-литералы JSX -> байты (ровно так их пишет File(encoding=BINARY) в AE)."""
    lits = re.findall(r'presetBin: "((?:[^"\\]|\\.)*)"', js)
    return [lit.encode("latin-1").decode("unicode_escape").encode("latin-1") for lit in lits]


def test_binary_literal_roundtrip_is_ascii_and_exact():
    data = bytes(range(256)) * 4 + b'"\\</script>\r\n\x00'
    lit = overlay.js_binary_literal(data)
    assert lit.isascii() and lit[0] == lit[-1] == '"'
    assert _preset_values(f"presetBin: {lit}") == [data]


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


def test_build_task_accepts_every_selectable_effect():
    """tasks.py проверяет effect_* запроса перед сборкой — тем же набором, что overlay и схема
    (раньше там была своя копия списка, и build падал на каждом эффекте Kant)."""
    from services.orchestrator.tasks import f3_allowed_ids

    allowed = f3_allowed_ids()
    contract = contract_ids(ROOT)
    assert allowed["hook"] == set(overlay.F3_HOOKS) == set(contract["effect_hook"])
    assert allowed["transition"] == set(overlay.F3_TRANSITIONS) == set(contract["effect_transition"])
    assert allowed["extra"] == set(overlay.F3_EXTRAS) == set(contract["effect_extra"])
    assert "of_invert_flash" in allowed["transition"]


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
    assert _preset_values(js) == [(F3_DIR / "kantfx/cc_tritone_red.ffx").read_bytes()]


def test_kant_transition_on_a_table_cut_and_kant_style_on_a_window():
    js = overlay.build_overlay_jsx(
        cut_transitions=[{"t": 1.2, "id": "sh_twitch_flicker"}, {"t": 2.5, "id": "snap_wipe"}],
        extra_ranges=[{"id": "fx_universe_vhs", "start": 0.0, "end": 3.0}],
        drop_time=4.0,
    )
    assert 'mode: "cuts"' in js and 'mode: "window"' in js
    assert sorted(_preset_values(js), key=len) == sorted(
        [(F3_DIR / "kantfx/sh_twitch_flicker.ffx").read_bytes(), (F3_DIR / "kantfx/fx_universe_vhs.ffx").read_bytes()], key=len
    )


def test_regular_effects_do_not_get_preset_keys():
    js = overlay.build_overlay_jsx(extra="xerox", transition="snap_wipe", drop_time=4.0)
    assert _preset_values(js) == []


def test_preset_outside_the_pipeline_dir_is_refused():
    with pytest.raises(RuntimeError):
        overlay._preset_kv({"id": "x", "preset": "../manifest.json"})
