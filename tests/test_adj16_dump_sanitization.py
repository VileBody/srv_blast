from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.footage_comp import _extract_effects_from_adjustment_dump


def test_adj16_layer_index_params_are_neutralized() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    dump_path = repo_root / "data" / "0_4.504505__Adjustment Layer 16__adjustment.json"
    dump = json.loads(dump_path.read_text(encoding="utf-8"))

    effects = _extract_effects_from_adjustment_dump(dump)

    # propertyValueType == 6421 (LAYER_INDEX) selectors from the dump must not retain a nonzero index.
    # See dump for these matchNames:
    layer_index_match_names = {
        "BCC6LensBlur-10158087",  # Host Layer
        "BCC6LensBlur-9961851",   # Z Layer
        "BCC6LensBlur-11010096",  # Matte Layer
        "BCC6LensBlur-6357009",   # PC Layer
        "S_BlurMotion-0001",      # Matte from Layer
    }

    found = set()
    for params in effects.values():
        for pd in params.values():
            if pd.match_name in layer_index_match_names:
                found.add(pd.match_name)
                assert pd.value == 0

    # Ensure we actually hit the known problematic param.
    assert "BCC6LensBlur-9961851" in found
