from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


TimingRule = Literal["Dynamic Contrast", "Lyrical Phrases"]


_EPS = 1e-6


def _validate_sorted_unique_non_negative(points: List[float], *, field_name: str) -> List[float]:
    out = [float(x) for x in points]
    prev = None
    for idx, val in enumerate(out):
        if val < 0.0:
            raise ValueError(f"{field_name}[{idx}] must be >= 0")
        if prev is not None and val <= prev + _EPS:
            raise ValueError(f"{field_name} must be strictly increasing and unique")
        prev = val
    return out


class RawTimingBuckets(BaseModel):
    kick_bass: List[float] = Field(default_factory=list)
    snare_clap: List[float] = Field(default_factory=list)
    vocal_phrases: List[float] = Field(default_factory=list)
    semantic_peaks: List[float] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "RawTimingBuckets":
        self.kick_bass = _validate_sorted_unique_non_negative(self.kick_bass, field_name="kick_bass")
        self.snare_clap = _validate_sorted_unique_non_negative(self.snare_clap, field_name="snare_clap")
        self.vocal_phrases = _validate_sorted_unique_non_negative(self.vocal_phrases, field_name="vocal_phrases")
        self.semantic_peaks = _validate_sorted_unique_non_negative(self.semantic_peaks, field_name="semantic_peaks")
        return self


class Stage2TimingAnalysisPayload(BaseModel):
    selected_rule: TimingRule
    reason: str = Field(min_length=1)
    raw_timings: RawTimingBuckets


class Stage2TimingCutsPayload(BaseModel):
    applied_rule: TimingRule
    final_cut_timings: List[float] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "Stage2TimingCutsPayload":
        self.final_cut_timings = _validate_sorted_unique_non_negative(
            self.final_cut_timings,
            field_name="final_cut_timings",
        )
        if not self.final_cut_timings:
            raise ValueError("final_cut_timings must contain at least one cut point")
        return self


class SwitchTimingPayload(BaseModel):
    clip_start_abs: float = Field(ge=0.0)
    clip_end_abs: float = Field(ge=0.0)
    fast_start_seconds: float = Field(ge=0.0)
    bpm: Optional[float] = None
    switch_points_abs: List[float] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "SwitchTimingPayload":
        cs = float(self.clip_start_abs)
        ce = float(self.clip_end_abs)
        if ce <= cs + _EPS:
            raise ValueError("clip_end_abs must be > clip_start_abs")
        if float(self.fast_start_seconds) > (ce - cs) + _EPS:
            raise ValueError("fast_start_seconds must be <= clip duration")
        if self.bpm is not None and float(self.bpm) <= 0.0:
            raise ValueError("bpm must be > 0 when provided")

        pts = _validate_sorted_unique_non_negative(self.switch_points_abs, field_name="switch_points_abs")
        for idx, p in enumerate(pts):
            if p <= cs + _EPS or p >= ce - _EPS:
                raise ValueError(f"switch_points_abs[{idx}] must be strictly inside clip window")
        self.switch_points_abs = pts
        return self


def normalize_switch_points(
    *,
    raw_cut_timings: List[float],
    clip_start_abs: float,
    clip_end_abs: float,
    merge_gap_sec: float = 0.2,
    min_segment_sec: float = 0.3,
    compact_short_segments: bool = False,
) -> List[float]:
    """
    Keep only internal cut points, merge near-duplicates, and enforce min segment duration.
    """
    cs = float(clip_start_abs)
    ce = float(clip_end_abs)
    if ce <= cs + _EPS:
        raise ValueError("Invalid clip window for normalize_switch_points")
    if merge_gap_sec < 0.0:
        raise ValueError("merge_gap_sec must be >= 0")
    if min_segment_sec <= 0.0:
        raise ValueError("min_segment_sec must be > 0")

    candidates = sorted(float(x) for x in raw_cut_timings)
    inside = [x for x in candidates if x > cs + _EPS and x < ce - _EPS]

    merged: List[float] = []
    for x in inside:
        if not merged:
            merged.append(x)
            continue
        if (x - merged[-1]) < float(merge_gap_sec) - _EPS:
            continue
        merged.append(x)

    if compact_short_segments:
        compacted: List[float] = []
        prev = cs
        for x in merged:
            if (x - prev) < float(min_segment_sec) - _EPS:
                continue
            compacted.append(x)
            prev = x

        # Ensure the last kept cut does not create an invalid tail.
        while compacted and (ce - compacted[-1]) < float(min_segment_sec) - _EPS:
            compacted.pop()

        if merged and not compacted:
            raise ValueError(
                "final_cut_timings violates min segment "
                f"{min_segment_sec}s: all points were dropped after compaction"
            )
        return compacted

    prev = cs
    for idx, x in enumerate(merged):
        if (x - prev) < float(min_segment_sec) - _EPS:
            raise ValueError(
                f"final_cut_timings violates min segment {min_segment_sec}s at index={idx}: prev={prev:.3f} cur={x:.3f}"
            )
        prev = x

    if (ce - prev) < float(min_segment_sec) - _EPS:
        raise ValueError(
            f"final_cut_timings violates min tail segment {min_segment_sec}s: prev={prev:.3f} clip_end={ce:.3f}"
        )

    return merged
