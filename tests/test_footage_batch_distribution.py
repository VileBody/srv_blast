from __future__ import annotations

import pytest

from mlcore.footage_batch_distribution import (
    distribute_buckets,
    distribute_slots,
    resolve_bucket_slot,
)
from mlcore.footage_bucket_catalog import Bucket

_A = "heartbreak_minor:winter_isolation"
_B = "aggression_minor:chaos_elements"
_C = "romance_major:nature_sunset"


def test_one_bucket_n_takes() -> None:
    assert distribute_buckets([_A], 5) == [_A, _A, _A, _A, _A]


def test_k_equals_n_one_each() -> None:
    assert distribute_buckets([_A, _B, _C], 3) == [_A, _B, _C]


def test_k_less_than_n_cycles() -> None:
    # 2 selected, 5 videos -> 1,2,1,2,1
    assert distribute_buckets([_A, _B], 5) == [_A, _B, _A, _B, _A]


def test_k_greater_than_n_uses_top_n() -> None:
    # selected 1 and 3, only 1 video -> top (first) selected
    assert distribute_buckets([_A, _C], 1) == [_A]
    assert distribute_buckets([_A, _B, _C], 2) == [_A, _B]


def test_zero_videos_empty() -> None:
    assert distribute_buckets([_A], 0) == []


def test_empty_selection_raises() -> None:
    with pytest.raises(ValueError):
        distribute_buckets([], 3)
    with pytest.raises(ValueError):
        distribute_buckets(["", "  "], 3)


def test_resolve_slot_from_catalog_and_fallback() -> None:
    cat = [Bucket(bucket_id=_A, theme="heartbreak_minor", tags_group="winter_isolation",
                  mood="minor", priority_tags=["snow"])]
    assert resolve_bucket_slot(_A, catalog=cat) == ("heartbreak_minor", "winter_isolation")
    # not in catalog -> split on ':'
    assert resolve_bucket_slot("foo_minor:bar", catalog=cat) == ("foo_minor", "bar")
    with pytest.raises(ValueError):
        resolve_bucket_slot("no-colon-id", catalog=cat)


def test_distribute_slots_end_to_end() -> None:
    cat = [
        Bucket(bucket_id=_A, theme="heartbreak_minor", tags_group="winter_isolation", mood="minor", priority_tags=["snow"]),
        Bucket(bucket_id=_B, theme="aggression_minor", tags_group="chaos_elements", mood="minor", priority_tags=["fire"]),
    ]
    out = distribute_slots([_A, _B], 3, catalog=cat)
    assert out == [("heartbreak_minor", "winter_isolation"),
                   ("aggression_minor", "chaos_elements"),
                   ("heartbreak_minor", "winter_isolation")]
