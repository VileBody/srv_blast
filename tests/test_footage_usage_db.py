"""Tests for the pure recency/coldness helpers of mlcore/footage_usage_db.py
(the cross-user cooldown ledger). asyncpg I/O is exercised in prod, not here."""
from __future__ import annotations

from mlcore import footage_usage_db as udb


def test_recency_index_newest_first_distinct():
    idx = udb.recency_index(["c1", "c2", "c1", "c3"])  # c1 repeated -> first wins
    assert idx == {"c1": 0, "c2": 1, "c3": 2}


def test_coldness_absent_is_coldest():
    idx = udb.recency_index(["hot", "warm"])  # window of 2 recent
    window = 30
    # never-served clip is coldest (best to pick)
    assert udb.coldness("never", idx, window=window) > udb.coldness("hot", idx, window=window)
    assert udb.coldness("never", idx, window=window) > udb.coldness("warm", idx, window=window)
    # older in-window (warm, index 1) colder than freshest (hot, index 0)
    assert udb.coldness("warm", idx, window=window) > udb.coldness("hot", idx, window=window)
    assert udb.coldness("hot", idx, window=window) == 0.0


def test_coldness_orders_band_least_recently_used_first():
    # band of 4 clips; two were served recently (a newest, b older), c/d never
    idx = udb.recency_index(["a", "b"])
    band = ["a", "b", "c", "d"]
    # picker orders by coldness DESC -> fresh/never first, hottest last
    ordered = sorted(band, key=lambda c: -udb.coldness(c, idx, window=30))
    assert ordered[-1] == "a"          # most recently served -> picked last
    assert ordered[-2] == "b"          # next most recent
    assert set(ordered[:2]) == {"c", "d"}  # never-served -> preferred


def test_recency_index_empty():
    assert udb.recency_index([]) == {}
