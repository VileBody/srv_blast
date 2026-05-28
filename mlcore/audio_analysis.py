"""
Audio analysis for hook/drop detection inside a user-selected focus clip.

Pathfinder module. Not yet wired into the Stage2 pipeline — used standalone
via the CLI below to tune heuristics on real tracks. See memory
project-hook-audio-analysis for the roadmap (phase A → phase B integration).

All output timestamps are absolute (relative to the source file, NOT to the
focus clip), so downstream consumers (AE JSX builder, switch_timing
normalizer) can use them directly.

v4 changes vs v3:
- Density curve: per-window composite of spectral_flatness, spectral_bandwidth,
  RMS energy, and onset rate. Tells how "dense" the mix is at each moment.
- Sections: density curve discretized into low/mid/high regions, with the
  region containing drop_t auto-labeled "drop". Each section carries a
  suggested max_cuts_per_sec cap for downstream LLM/JSX consumers.
- Expected focus clip duration is ~10-22s (hook working window), not full
  track. Density windowing tuned for this scale.

CLI:
    python -m mlcore.audio_analysis path/to/track.mp3 \
        --start 0 --end 22 \
        --out local_test/audio_analysis/out/track.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, List, Literal, Optional

if TYPE_CHECKING:
    import numpy as np


ANALYSIS_VERSION = "v7"

# --- core extraction params -------------------------------------------------
DEFAULT_SR = 22050
DEFAULT_HOP = 512                  # ~23 ms at sr=22050
DEFAULT_FRAME = 2048
LOW_BAND_HZ = (20.0, 200.0)
MID_BAND_HZ = (200.0, 2000.0)
HIGH_BAND_HZ = (2000.0, 8000.0)

# --- drop detection ---------------------------------------------------------
DROP_TOPK = 5
DROP_DEDUP_SEC = 0.5
DROP_W_RMS = 1.0
DROP_W_FLUX = 0.8
DROP_W_LOWJUMP = 1.2
SPECTRAL_PEAKS_TOPK = 8
BPM_DOUBLING_THRESHOLD = 180.0     # > this → halve
BPM_HALVING_THRESHOLD = 90.0       # < this → double (symmetric guard)
BEAT_SNAP_TOLERANCE_SEC = 0.12
BEAT_SNAP_BONUS = 1.15
CONF_LOGISTIC_CENTER = 6.0
CONF_LOGISTIC_SLOPE = 0.4

# --- density + sections -----------------------------------------------------
DENSITY_WIN_SEC = 0.5              # window size for per-frame density sample
DENSITY_HOP_SEC = 0.25             # stride between samples (2 Hz curve)
DENSITY_SMOOTH_TAPS = 3            # moving-avg taps to denoise curve
DENSITY_W_FLATNESS = 0.30
DENSITY_W_BANDWIDTH = 0.20
DENSITY_W_RMS = 0.30
DENSITY_W_ONSET_RATE = 0.20
DENSITY_LOW_THR = 0.40             # < low: "low" section
DENSITY_HIGH_THR = 0.70            # > high: "high" (used only when no drop_t)
DENSITY_HIGH_SUSTAIN_THR = 0.85    # post-drop: stricter "high" — only extra-dense
DROP_WINDOW_SEC = 3.0              # "meat-grinder" window right after drop_t
SECTION_MIN_DURATION_SEC = 0.8     # absorb shorter regions into neighbors
# Suggested cut rates per section label (cuts per second). LLM/JSX may treat
# these as caps, not mandates. Tune from real-track audits.
CUT_RATE_BY_LABEL = {
    "low":   0.30,
    "mid":   0.70,
    "high":  1.40,
    "drop":  1.70,
    "build": 0.55,  # v7: lowered from 0.90 to keep long pre-drop runs cheap
}


@dataclass
class DropCandidate:
    t: float
    confidence: float
    score_raw: float
    score_adj: float
    snapped_to_beat: bool
    source: str


@dataclass
class SpectralPeak:
    t: float
    band: Literal["low", "mid", "high"]
    magnitude: float


@dataclass
class DensitySample:
    t: float
    density: float                 # 0..1
    flatness: float
    bandwidth_norm: float
    rms_norm: float
    onset_rate: float              # onsets per second in window


@dataclass
class Section:
    t_start: float
    t_end: float
    label: Literal["low", "mid", "high", "drop", "build"]
    mean_density: float
    peak_density: float
    max_cuts_per_sec: float


@dataclass
class FocusClipMeta:
    start_abs: float
    end_abs: float
    duration: float


@dataclass
class HookAnalysis:
    analysis_version: str
    params_hash: str
    audio_path: str
    sr: int
    focus_clip: FocusClipMeta
    bpm: float
    bpm_raw: float
    bpm_doubled: bool
    beats: List[float]
    downbeats: List[float]
    onsets: List[float]
    drop_candidates: List[DropCandidate]
    spectral_peaks: List[SpectralPeak]
    density_curve: List[DensitySample]
    sections: List[Section]
    energy_envelope_hop_sec: float
    energy_envelope: List[float] = field(default_factory=list)


def _load_librosa():
    try:
        import librosa  # type: ignore
        return librosa
    except Exception as e:
        raise RuntimeError(
            "librosa is required for audio analysis. "
            "Install dependency and rebuild runtime image."
        ) from e


def _params_hash() -> str:
    parts = [
        ANALYSIS_VERSION,
        f"sr={DEFAULT_SR}", f"hop={DEFAULT_HOP}", f"frame={DEFAULT_FRAME}",
        f"low={LOW_BAND_HZ}", f"mid={MID_BAND_HZ}", f"high={HIGH_BAND_HZ}",
        f"w_rms={DROP_W_RMS}", f"w_flux={DROP_W_FLUX}", f"w_lowjump={DROP_W_LOWJUMP}",
        f"dedup={DROP_DEDUP_SEC}", f"bpm_thr={BPM_DOUBLING_THRESHOLD}",
        f"snap_tol={BEAT_SNAP_TOLERANCE_SEC}", f"snap_bonus={BEAT_SNAP_BONUS}",
        f"conf_c={CONF_LOGISTIC_CENTER}", f"conf_k={CONF_LOGISTIC_SLOPE}",
        f"d_win={DENSITY_WIN_SEC}", f"d_hop={DENSITY_HOP_SEC}",
        f"d_w_flat={DENSITY_W_FLATNESS}", f"d_w_bw={DENSITY_W_BANDWIDTH}",
        f"d_w_rms={DENSITY_W_RMS}", f"d_w_onset={DENSITY_W_ONSET_RATE}",
        f"d_lo={DENSITY_LOW_THR}", f"d_hi={DENSITY_HIGH_THR}",
        f"d_hi_sus={DENSITY_HIGH_SUSTAIN_THR}", f"drop_win={DROP_WINDOW_SEC}",
        f"sec_min={SECTION_MIN_DURATION_SEC}",
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _logistic_conf(score: float) -> float:
    x = (score - CONF_LOGISTIC_CENTER) * CONF_LOGISTIC_SLOPE
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def _load_clip(audio_path: Path, clip_start_abs: float, clip_end_abs: float, sr: int):
    librosa = _load_librosa()
    p = Path(audio_path).expanduser().resolve()
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"audio_path missing: {p}")
    if clip_start_abs < 0.0:
        raise ValueError("clip_start_abs must be >= 0")
    if clip_end_abs <= clip_start_abs:
        raise ValueError("clip_end_abs must be > clip_start_abs")
    duration = float(clip_end_abs) - float(clip_start_abs)
    y, sr_out = librosa.load(
        str(p), sr=int(sr), mono=True,
        offset=float(clip_start_abs), duration=duration,
    )
    if y.size < int(sr) // 2:
        raise ValueError(f"focus clip too short: {y.size} samples at sr={sr_out}")
    return y, int(sr_out)


def _band_mask(freqs, lo: float, hi: float):
    return (freqs >= lo) & (freqs < hi)


def _detect_beats(y, sr: int, clip_start_abs: float):
    librosa = _load_librosa()
    import numpy as np
    tempo, beat_frames = librosa.beat.beat_track(
        y=y, sr=sr, hop_length=DEFAULT_HOP, units="frames",
    )
    tempo_arr = np.asarray(tempo).reshape(-1)
    if tempo_arr.size == 0 or float(tempo_arr[0]) <= 0.0:
        raise RuntimeError(f"invalid BPM from librosa: {tempo!r}")
    bpm_raw = float(tempo_arr[0])
    beat_times_rel = librosa.frames_to_time(beat_frames, sr=sr, hop_length=DEFAULT_HOP)
    beats_abs_all = [float(t) + float(clip_start_abs) for t in beat_times_rel.tolist()]

    bpm_doubled = bpm_raw > BPM_DOUBLING_THRESHOLD
    bpm_halved = bpm_raw < BPM_HALVING_THRESHOLD
    if bpm_doubled:
        bpm = bpm_raw / 2.0
        beats_abs = beats_abs_all[::2]
    elif bpm_halved:
        # double the BPM; interpolate missing beats by inserting midpoints
        bpm = bpm_raw * 2.0
        interp: List[float] = []
        for i in range(len(beats_abs_all)):
            interp.append(beats_abs_all[i])
            if i + 1 < len(beats_abs_all):
                interp.append((beats_abs_all[i] + beats_abs_all[i + 1]) / 2.0)
        beats_abs = interp
    else:
        bpm = bpm_raw
        beats_abs = beats_abs_all
    downbeats_abs = beats_abs[::4]
    return bpm, bpm_raw, (bpm_doubled or bpm_halved), beats_abs, downbeats_abs


def _detect_onsets(y, sr: int, clip_start_abs: float) -> List[float]:
    librosa = _load_librosa()
    onset_frames = librosa.onset.onset_detect(
        y=y, sr=sr, hop_length=DEFAULT_HOP, backtrack=True, units="frames",
    )
    times_rel = librosa.frames_to_time(onset_frames, sr=sr, hop_length=DEFAULT_HOP)
    return [float(t) + float(clip_start_abs) for t in times_rel.tolist()]


def _detect_spectral_peaks(y, sr: int, clip_start_abs: float) -> List[SpectralPeak]:
    librosa = _load_librosa()
    import numpy as np
    from scipy.signal import find_peaks  # type: ignore

    S = np.abs(librosa.stft(y, n_fft=DEFAULT_FRAME, hop_length=DEFAULT_HOP))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=DEFAULT_FRAME)
    times_rel = librosa.frames_to_time(np.arange(S.shape[1]), sr=sr, hop_length=DEFAULT_HOP)

    peaks: List[SpectralPeak] = []
    for name, (lo, hi) in [("low", LOW_BAND_HZ), ("mid", MID_BAND_HZ), ("high", HIGH_BAND_HZ)]:
        mask = _band_mask(freqs, lo, hi)
        if not mask.any():
            continue
        band_energy = S[mask, :].sum(axis=0)
        if band_energy.max() <= 0:
            continue
        norm = band_energy / float(band_energy.max())
        min_dist = int(round(0.5 * sr / DEFAULT_HOP))
        idxs, props = find_peaks(norm, height=0.4, distance=max(1, min_dist))
        for i, mag in zip(idxs.tolist(), props["peak_heights"].tolist()):
            peaks.append(SpectralPeak(
                t=float(times_rel[i]) + float(clip_start_abs),
                band=name,  # type: ignore[arg-type]
                magnitude=float(mag),
            ))
    peaks.sort(key=lambda p: p.magnitude, reverse=True)
    return peaks[:SPECTRAL_PEAKS_TOPK]


def _detect_drop_candidates(y, sr: int, clip_start_abs: float, beats_abs: List[float]):
    librosa = _load_librosa()
    import numpy as np
    from scipy.signal import find_peaks  # type: ignore

    rms = librosa.feature.rms(y=y, frame_length=DEFAULT_FRAME, hop_length=DEFAULT_HOP)[0]
    flux = librosa.onset.onset_strength(y=y, sr=sr, hop_length=DEFAULT_HOP)
    S = np.abs(librosa.stft(y, n_fft=DEFAULT_FRAME, hop_length=DEFAULT_HOP))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=DEFAULT_FRAME)
    low_mask = _band_mask(freqs, *LOW_BAND_HZ)
    low_energy = S[low_mask, :].sum(axis=0) if low_mask.any() else np.zeros(S.shape[1])

    n = min(len(rms), len(flux), len(low_energy))
    rms = rms[:n]; flux = flux[:n]; low_energy = low_energy[:n]
    win = max(1, int(round(0.5 * sr / DEFAULT_HOP)))

    def pos_jump(arr):
        prev = np.concatenate([np.full(win, arr[0]), arr[:-win]])
        return np.maximum(arr - prev, 0.0)

    def zscore(arr):
        std = float(arr.std())
        if std < 1e-9:
            return np.zeros_like(arr)
        return (arr - float(arr.mean())) / std

    z_rms = zscore(pos_jump(rms))
    z_flux = zscore(flux)
    z_low = zscore(pos_jump(low_energy))
    score = DROP_W_RMS * z_rms + DROP_W_FLUX * z_flux + DROP_W_LOWJUMP * z_low

    times_rel = librosa.frames_to_time(np.arange(n), sr=sr, hop_length=DEFAULT_HOP)
    min_dist = max(1, int(round(DROP_DEDUP_SEC * sr / DEFAULT_HOP)))
    height_thr = float(np.percentile(score, 85.0))
    idxs, props = find_peaks(score, height=height_thr, distance=min_dist)
    if idxs.size == 0:
        idxs = np.array([int(np.argmax(score))])
        heights = np.array([float(score[idxs[0]])])
    else:
        heights = props["peak_heights"]

    raw_pairs = []
    for idx, raw in zip(idxs.tolist(), heights.tolist()):
        t_abs = float(times_rel[idx]) + float(clip_start_abs)
        snapped = False
        if beats_abs:
            nearest = min(beats_abs, key=lambda b: abs(b - t_abs))
            if abs(nearest - t_abs) <= BEAT_SNAP_TOLERANCE_SEC:
                t_abs = nearest
                snapped = True
        adj = float(raw) * (BEAT_SNAP_BONUS if snapped else 1.0)
        comps = []
        if z_rms[idx] > 0.5:  comps.append("rms_jump")
        if z_flux[idx] > 0.5: comps.append("flux")
        if z_low[idx] > 0.5:  comps.append("low_band")
        raw_pairs.append((idx, t_abs, float(raw), adj, snapped, "+".join(comps) or "composite"))

    raw_pairs.sort(key=lambda p: p[3], reverse=True)
    raw_pairs = raw_pairs[:DROP_TOPK]

    candidates: List[DropCandidate] = []
    for _idx, t_abs, raw, adj, snapped, source in raw_pairs:
        candidates.append(DropCandidate(
            t=round(t_abs, 3),
            confidence=round(_logistic_conf(adj), 3),
            score_raw=round(raw, 4),
            score_adj=round(adj, 4),
            snapped_to_beat=snapped,
            source=source,
        ))

    dedup: List[DropCandidate] = []
    for c in candidates:
        if any(abs(c.t - d.t) < DROP_DEDUP_SEC for d in dedup):
            continue
        dedup.append(c)

    hop_sec = float(DEFAULT_HOP) / float(sr)
    return dedup, [round(float(v), 4) for v in rms.tolist()], hop_sec


def _compute_density_curve(y, sr: int, clip_start_abs: float, onsets_abs: List[float]) -> List[DensitySample]:
    """
    Sample density at DENSITY_HOP_SEC strides. Each sample combines:
      - spectral flatness (0=tonal, 1=noisy)
      - spectral bandwidth (normalized to Nyquist)
      - RMS energy (normalized to max in clip)
      - onset rate inside the window
    """
    librosa = _load_librosa()
    import numpy as np

    win_samples = int(DENSITY_WIN_SEC * sr)
    hop_samples = int(DENSITY_HOP_SEC * sr)
    total = len(y)
    if total < win_samples:
        return []

    # precompute frame-level features at native hop, then aggregate per window
    flatness = librosa.feature.spectral_flatness(y=y, n_fft=DEFAULT_FRAME, hop_length=DEFAULT_HOP)[0]
    bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr, n_fft=DEFAULT_FRAME, hop_length=DEFAULT_HOP)[0]
    rms = librosa.feature.rms(y=y, frame_length=DEFAULT_FRAME, hop_length=DEFAULT_HOP)[0]
    nyquist = float(sr) / 2.0
    bandwidth_norm = np.clip(bandwidth / nyquist, 0.0, 1.0)
    rms_max = float(rms.max()) if rms.max() > 0 else 1.0
    rms_norm = rms / rms_max
    frame_hop_sec = float(DEFAULT_HOP) / float(sr)

    samples: List[DensitySample] = []
    t = 0
    while t + win_samples <= total:
        win_t_start_rel = t / float(sr)
        win_t_end_rel = (t + win_samples) / float(sr)
        f_start = int(win_t_start_rel / frame_hop_sec)
        f_end = max(f_start + 1, int(win_t_end_rel / frame_hop_sec))
        f_end = min(f_end, len(flatness))

        flat_v = float(flatness[f_start:f_end].mean()) if f_end > f_start else 0.0
        bw_v   = float(bandwidth_norm[f_start:f_end].mean()) if f_end > f_start else 0.0
        rms_v  = float(rms_norm[f_start:f_end].mean()) if f_end > f_start else 0.0

        abs_lo = win_t_start_rel + clip_start_abs
        abs_hi = win_t_end_rel + clip_start_abs
        n_onsets = sum(1 for o in onsets_abs if abs_lo <= o < abs_hi)
        onset_rate = float(n_onsets) / DENSITY_WIN_SEC
        onset_rate_norm = min(onset_rate / 8.0, 1.0)  # 8 onsets/sec = saturation

        density = (
            DENSITY_W_FLATNESS * flat_v
            + DENSITY_W_BANDWIDTH * bw_v
            + DENSITY_W_RMS * rms_v
            + DENSITY_W_ONSET_RATE * onset_rate_norm
        )
        density = max(0.0, min(1.0, density))

        samples.append(DensitySample(
            t=round((win_t_start_rel + win_t_end_rel) / 2.0 + clip_start_abs, 3),
            density=round(density, 3),
            flatness=round(flat_v, 3),
            bandwidth_norm=round(bw_v, 3),
            rms_norm=round(rms_v, 3),
            onset_rate=round(onset_rate, 2),
        ))
        t += hop_samples

    # smooth with moving average to denoise
    if len(samples) >= DENSITY_SMOOTH_TAPS:
        taps = DENSITY_SMOOTH_TAPS
        smoothed_d = []
        for i in range(len(samples)):
            lo = max(0, i - taps // 2)
            hi = min(len(samples), i + taps // 2 + 1)
            smoothed_d.append(sum(s.density for s in samples[lo:hi]) / (hi - lo))
        for s, d in zip(samples, smoothed_d):
            s.density = d  # leave unrounded until after renormalization

    # per-clip normalization: stretch [p10, p90] → [0, 1] so thresholds
    # (low/mid/high) compare relative intensity within THIS clip, not
    # against absolute librosa values (which vary by mix/master).
    arr = sorted(s.density for s in samples)
    if len(arr) >= 4:
        p10 = arr[max(0, int(len(arr) * 0.10))]
        p90 = arr[min(len(arr) - 1, int(len(arr) * 0.90))]
        span = max(p90 - p10, 1e-6)
        for s in samples:
            s.density = round(max(0.0, min(1.0, (s.density - p10) / span)), 3)
    else:
        for s in samples:
            s.density = round(s.density, 3)

    return samples


def _segment_sections(density_curve: List[DensitySample], drop_t: Optional[float]) -> List[Section]:
    """
    Zone-aware labeling rules:
      pre-drop  (t < drop_t)                      → low or mid; "high" forbidden
      drop window (drop_t ≤ t < drop_t + 3s)      → always "drop"
      sustain   (t ≥ drop_t + 3s)                 → low / mid / high (strict thr)
      build     last pre-drop section adj. to drop_t → relabeled
    No drop_t: pure low/mid/high by absolute thresholds.
    """
    if not density_curve:
        return []

    def label_for(t: float, d: float) -> str:
        if drop_t is None:
            if d < DENSITY_LOW_THR:  return "low"
            if d > DENSITY_HIGH_THR: return "high"
            return "mid"
        if t < drop_t:
            # pre-drop: high is logically impossible (no drop yet) → cap at mid
            return "low" if d < DENSITY_LOW_THR else "mid"
        if t < drop_t + DROP_WINDOW_SEC:
            # mandatory "meat-grinder" window after the drop
            return "drop"
        # post-drop sustain: only truly extra-dense reads as high
        if d < DENSITY_LOW_THR:           return "low"
        if d > DENSITY_HIGH_SUSTAIN_THR:  return "high"
        return "mid"

    half_hop = DENSITY_HOP_SEC / 2.0
    sample_labels = [label_for(s.t, s.density) for s in density_curve]

    raw: List[Section] = []
    cur_label = sample_labels[0]
    cur_start = density_curve[0].t - half_hop
    cur_samples: List[DensitySample] = [density_curve[0]]
    for s, lab in zip(density_curve[1:], sample_labels[1:]):
        if lab == cur_label:
            cur_samples.append(s)
        else:
            t_end = s.t - half_hop
            mean_d = sum(x.density for x in cur_samples) / len(cur_samples)
            peak_d = max(x.density for x in cur_samples)
            raw.append(Section(
                t_start=round(cur_start, 3), t_end=round(t_end, 3),
                label=cur_label,  # type: ignore[arg-type]
                mean_density=round(mean_d, 3), peak_density=round(peak_d, 3),
                max_cuts_per_sec=CUT_RATE_BY_LABEL.get(cur_label, 0.7),
            ))
            cur_label = lab
            cur_start = t_end
            cur_samples = [s]
    t_end = density_curve[-1].t + half_hop
    mean_d = sum(x.density for x in cur_samples) / len(cur_samples)
    peak_d = max(x.density for x in cur_samples)
    raw.append(Section(
        t_start=round(cur_start, 3), t_end=round(t_end, 3),
        label=cur_label,  # type: ignore[arg-type]
        mean_density=round(mean_d, 3), peak_density=round(peak_d, 3),
        max_cuts_per_sec=CUT_RATE_BY_LABEL.get(cur_label, 0.7),
    ))

    # absorb sub-minimum-duration sections (do not absorb "drop" — it's exact)
    merged: List[Section] = []
    for sec in raw:
        too_short = (sec.t_end - sec.t_start) < SECTION_MIN_DURATION_SEC
        if merged and too_short and sec.label != "drop" and merged[-1].label != "drop":
            prev = merged[-1]
            new_dur = sec.t_end - prev.t_start
            prev_dur = prev.t_end - prev.t_start
            this_dur = sec.t_end - sec.t_start
            merged[-1] = Section(
                t_start=prev.t_start, t_end=sec.t_end,
                label=prev.label,
                mean_density=round(
                    (prev.mean_density * prev_dur + sec.mean_density * this_dur) / new_dur, 3
                ),
                peak_density=max(prev.peak_density, sec.peak_density),
                max_cuts_per_sec=prev.max_cuts_per_sec,
            )
        else:
            merged.append(sec)

    # promote last pre-drop section (adjacent to drop) to "build"
    if drop_t is not None:
        for i, sec in enumerate(merged):
            if sec.label == "drop":
                if i > 0:
                    prev = merged[i - 1]
                    if prev.label != "drop":
                        merged[i - 1] = Section(
                            t_start=prev.t_start, t_end=prev.t_end,
                            label="build",
                            mean_density=prev.mean_density,
                            peak_density=prev.peak_density,
                            max_cuts_per_sec=CUT_RATE_BY_LABEL["build"],
                        )
                break

    return merged


def analyze_focus_clip(
    *,
    audio_path: Path,
    clip_start_abs: float,
    clip_end_abs: float,
    sr: int = DEFAULT_SR,
    include_envelope: bool = False,
) -> HookAnalysis:
    y, sr_out = _load_clip(Path(audio_path), clip_start_abs, clip_end_abs, sr)
    bpm, bpm_raw, bpm_doubled, beats_abs, downbeats_abs = _detect_beats(y, sr_out, clip_start_abs)
    onsets_abs = _detect_onsets(y, sr_out, clip_start_abs)
    spectral_peaks = _detect_spectral_peaks(y, sr_out, clip_start_abs)
    drops, envelope, hop_sec = _detect_drop_candidates(y, sr_out, clip_start_abs, beats_abs)
    density_curve = _compute_density_curve(y, sr_out, clip_start_abs, onsets_abs)
    drop_t_for_sections = drops[0].t if drops else None
    sections = _segment_sections(density_curve, drop_t_for_sections)

    return HookAnalysis(
        analysis_version=ANALYSIS_VERSION,
        params_hash=_params_hash(),
        audio_path=str(Path(audio_path).expanduser().resolve()),
        sr=sr_out,
        focus_clip=FocusClipMeta(
            start_abs=float(clip_start_abs),
            end_abs=float(clip_end_abs),
            duration=float(clip_end_abs - clip_start_abs),
        ),
        bpm=round(float(bpm), 2),
        bpm_raw=round(float(bpm_raw), 2),
        bpm_doubled=bool(bpm_doubled),
        beats=[round(t, 3) for t in beats_abs],
        downbeats=[round(t, 3) for t in downbeats_abs],
        onsets=[round(t, 3) for t in onsets_abs],
        drop_candidates=drops,
        spectral_peaks=spectral_peaks,
        density_curve=density_curve,
        sections=sections,
        energy_envelope_hop_sec=round(hop_sec, 4),
        energy_envelope=envelope if include_envelope else [],
    )


def to_jsonable(obj: HookAnalysis) -> dict:
    """Serialize HookAnalysis to a JSON-safe dict. Stable across versions of
    the dataclass — extra fields just appear in the dict."""
    d = asdict(obj)
    d["focus_clip"] = asdict(obj.focus_clip)
    d["drop_candidates"] = [asdict(c) for c in obj.drop_candidates]
    d["spectral_peaks"] = [asdict(p) for p in obj.spectral_peaks]
    d["density_curve"] = [asdict(s) for s in obj.density_curve]
    d["sections"] = [asdict(s) for s in obj.sections]
    return d


# Backward-compat alias for any caller that imported the underscore name.
_to_jsonable = to_jsonable


def _main():
    ap = argparse.ArgumentParser(description="Hook/drop audio analysis (pathfinder)")
    ap.add_argument("audio_path", type=Path)
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--sr", type=int, default=DEFAULT_SR)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--envelope", action="store_true")
    args = ap.parse_args()

    result = analyze_focus_clip(
        audio_path=args.audio_path,
        clip_start_abs=args.start,
        clip_end_abs=args.end,
        sr=args.sr,
        include_envelope=args.envelope,
    )
    payload = json.dumps(_to_jsonable(result), ensure_ascii=False, indent=2)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
        print(f"[ok] wrote {args.out}")
    else:
        sys.stdout.write(payload + "\n")


if __name__ == "__main__":
    _main()
