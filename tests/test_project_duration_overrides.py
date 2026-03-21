from __future__ import annotations

from app.project_builder import _apply_comp_duration_overrides
from app.project_config import AE_PROJECT


def test_main_and_text_comp_durations_follow_actual_comp_duration() -> None:
    comps = [
        dict(AE_PROJECT["main_comp"]),
        dict(AE_PROJECT["text_comp"]),
        dict(AE_PROJECT["mine_comp"]),
    ]
    comp_dur = 17.18

    out = _apply_comp_duration_overrides(
        comps=comps,
        main_comp_name="Comp 1",
        text_comp_name="Текст",
        comp_dur=comp_dur,
    )

    by_name = {str(c["name"]): c for c in out}
    main = by_name["Comp 1"]
    text = by_name["Текст"]

    assert abs(float(main["dur"]) - comp_dur) <= 1e-6
    assert abs(float(main["workAreaDuration"]) - comp_dur) <= 1e-6
    assert abs(float(text["dur"]) - comp_dur) <= 1e-6
    assert abs(float(text["workAreaDuration"]) - comp_dur) <= 1e-6
