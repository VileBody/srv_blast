from __future__ import annotations

import json

from scripts.export_bigtest_render_plan_corpus import (
    export_bigtest_corpus,
    load_static_bigtest_cases,
)


def test_bigtest_exporter_writes_f1_f5_native_requests(tmp_path):
    manifest = export_bigtest_corpus(tmp_path)

    assert manifest["source"]["static_bigtest_count"] == len(load_static_bigtest_cases())
    assert manifest["coverage"]["F1"] == 1
    for family in ("F2", "F3", "F4", "F5"):
        assert manifest["coverage"][family] > 0

    requests = []
    for case in manifest["cases"]:
        request = json.loads((tmp_path / case["request"]).read_text(encoding="utf-8"))
        assert request["schema"] == "ae-native-renderer.render-request.v1"
        assert request["payloadVersion"] == "render-plan.v1"
        requests.append(request)

    ops_by_type = {
        op["type"]
        : op
        for request in requests
        for op in request.get("visualOps", [])
    }
    assert ops_by_type["hook.f1.sound.v1"]["assets"][0]["role"] == "audio"
    assert ops_by_type["hook.f1.sound.v1"]["assets"][0]["optional"] is False
    assert ops_by_type["hook.f5.cognition.v1"]["assets"][0]["role"] == "tts_audio"
    assert ops_by_type["hook.f5.cognition.v1"]["params"]["word_timings"]

    f2_shapes = {
        op["params"]["shape"]
        for request in requests
        for op in request.get("visualOps", [])
        if op["type"] == "hook.f2.object.v1"
    }
    assert {"rhomb", "square", "star1", "star2", "elipse"} <= f2_shapes
