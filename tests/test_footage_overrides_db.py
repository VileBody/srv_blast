from __future__ import annotations

from mlcore.footage_overrides_db import build_tag_overrides_doc


def test_build_doc_normalizes_and_dedups() -> None:
    doc = build_tag_overrides_doc(["Watching TV", "watching tv", "  Tender  ", ""])
    assert doc["blacklisted_tags"] == ["watching tv", "tender"]
    assert doc["tag_assignments"] == []


def test_build_doc_preserves_assignments() -> None:
    assigns = [{"tag": "x", "theme": "t", "group": "g"}]
    doc = build_tag_overrides_doc(["abstract design"], assigns)
    assert doc["blacklisted_tags"] == ["abstract design"]
    assert doc["tag_assignments"] == assigns


def test_build_doc_shape_matches_picker_expectations() -> None:
    # footage_picker._load_global_tag_overrides reads exactly these two keys.
    doc = build_tag_overrides_doc([])
    assert set(doc.keys()) == {"blacklisted_tags", "tag_assignments"}
    assert doc["blacklisted_tags"] == []
