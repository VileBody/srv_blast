"""
Audio analysis for hook/drop detection inside a user-selected focus clip.

Wired into the Stage2 pipeline via STAGE2_TIMING_MODE="hook_aware"
(`mlcore.gemini_orchestrator`). Also runnable standalone via the CLI below
to tune heuristics on real tracks. See memory project-hook-audio-analysis
for the roadmap (phase A done → AE-FX → phase B deterministic bypass).

All output timestamps are absolute (relative to the source file, NOT to the
focus clip), so downstream consumers (AE JSX builder, switch_timing
normalizer) can use them directly.

v8 changes vs v7:
- Onset classification (Phase A.5): each detected onset gets a frequency-band
  type label — kick / body / snare / transient / hat — based on which band
  dominates a ±30ms window around the onset, normalized to the track's
  per-band baseline. Output as parallel field `onsets_classified[]`. The
  flat `onsets[]` array is preserved for backward compat.

v7 changes vs v6:
- build cut-rate cap lowered 0.90 → 0.55 (long pre-drop runs were too dense).

v6 zone-aware section labeling:
  pre-drop      → low or mid (no "high" before drop_t)
  drop window   → "drop" for 3s after drop_t
  sustain       → low / mid / high (strict 0.85 threshold)
  build         → section adjacent to drop_t

CLI:
    python -m mlcore.audio_analysis path/to/track.mp3 \
        --start 0 --end 22 \
        --out local_test/audio_analysis/out/track.json

Implementation note:
    Do not import librosa here. In prod it pulls numba guvectorize code while
    Celery uses prefork workers; this has caused both native SIGSEGV and numba
    compile-time crashes. Decode with ffmpeg, then use numpy/scipy only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Tuple

if TYPE_CHECKING:
    import numpy as np


ANALYSIS_VERSION = "v8"

# --- core extraction params -------------------------------------------------
DEFAULT_SR = 22050
DEFAULT_HOP = 512                  # ~23 ms at sr=22050
DEFAULT_FRAME = 2048
LOW_BAND_HZ = (20.0, 200.0)
MID_BAND_HZ = (200.0, 2000.0)
HIGH_BAND_HZ = (2000.0, 8000.0)

# --- drop detection ---------------------------------------------------------
DROP_TOPK = 12  # was 5; keep a fuller pool so the bot can auto-walk to a later
                # drop for F4/F5 (which need pre-roll) even when the top few are
                # all early. The user-facing picker still shows only the top 3.
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
DENSITY_WIN_SEC = 0.5
DENSITY_HOP_SEC = 0.25
DENSITY_SMOOTH_TAPS = 3
DENSITY_W_FLATNESS = 0.30
DENSITY_W_BANDWIDTH = 0.20
DENSITY_W_RMS = 0.30
DENSITY_W_ONSET_RATE = 0.20
DENSITY_LOW_THR = 0.40
DENSITY_HIGH_THR = 0.70
DENSITY_HIGH_SUSTAIN_THR = 0.85
DROP_WINDOW_SEC = 3.0
SECTION_MIN_DURATION_SEC = 0.8
CUT_RATE_BY_LABEL = {
    "low":   0.30,
    "mid":   0.70,
    "high":  1.40,
    "drop":  1.70,
    "build": 0.55,
}

# --- onset classification (v8) ----------------------------------------------
# Bands chosen by typical instrumental signatures in pop/rap/EDM mixes.
# Order matters only for stable lookup; the classifier picks whichever band
# is dominant at the onset moment relative to its track-wide baseline.
ONSET_BANDS_HZ: List[Tuple[str, float, float]] = [
    ("kick",      60.0,    150.0),   # bass drum fundamental
    ("body",      150.0,   500.0),   # bass, vocal low end, low toms
    ("snare",     500.0,   2000.0),  # snare body, vocal consonants
    ("transient", 2000.0,  6000.0),  # claps, percussion attacks, gun-shot-like FX
    ("hat",       6000.0,  12000.0), # hi-hats, cymbals, sibilants
]
ONSET_WIN_SEC = 0.030              # ±30 ms window around onset for spectrum
ONSET_DOMINANCE_MIN = 0.5          # below this normalized energy → "unknown"
ONSET_CONF_RATIO_SAT = 3.0         # dominant/second ratio that maps to conf=1.0


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
    density: float
    flatness: float
    bandwidth_norm: float
    rms_norm: float
    onset_rate: float


@dataclass
class Section:
    t_start: float
    t_end: float
    label: Literal["low", "mid", "high", "drop", "build"]
    mean_density: float
    peak_density: float
    max_cuts_per_sec: float


@dataclass
class OnsetEvent:
    t: float
    type: str                       # one of ONSET_BANDS_HZ keys or "unknown"
    confidence: float               # 0..1, how dominant the chosen band is
    band_energies: Dict[str, float] # per-band energy, normalized to track baseline


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
    onsets_classified: List[OnsetEvent]
    drop_candidates: List[DropCandidate]
    spectral_peaks: List[SpectralPeak]
    density_curve: List[DensitySample]
    sections: List[Section]
    energy_envelope_hop_sec: float
    energy_envelope: List[float] = field(default_factory=list)


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
        f"onset_bands={[b[0] for b in ONSET_BANDS_HZ]}",
        f"onset_win={ONSET_WIN_SEC}", f"onset_dom={ONSET_DOMINANCE_MIN}",
        f"onset_csat={ONSET_CONF_RATIO_SAT}",
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _logistic_conf(score: float) -> float:
    x = (score - CONF_LOGISTIC_CENTER) * CONF_LOGISTIC_SLOPE
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def _load_clip(audio_path: Path, clip_start_abs: float, clip_end_abs: float, sr: int):
    import numpy as np

    p = Path(audio_path).expanduser().resolve()
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"audio_path missing: {p}")
    if clip_start_abs < 0.0:
        raise ValueError("clip_start_abs must be >= 0")
    if clip_end_abs <= clip_start_abs:
        raise ValueError("clip_end_abs must be > clip_start_abs")
    duration = float(clip_end_abs) - float(clip_start_abs)

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{float(clip_start_abs):.6f}",
        "-t",
        f"{duration:.6f}",
        "-i",
        str(p),
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        str(int(sr)),
        "-f",
        "f32le",
        "-",
    ]
    proc = subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"ffmpeg audio decode failed rc={proc.returncode}: {stderr[-1000:]}"
        )
    y = np.frombuffer(proc.stdout, dtype=np.float32).copy()
    if y.size < int(sr) // 2:
        raise ValueError(f"focus clip too short: {y.size} samples at sr={sr}")
    return y, int(sr)


def _band_mask(freqs, lo: float, hi: float):
    return (freqs >= lo) & (freqs < hi)


def _frame_signal(y, frame_length: int = DEFAULT_FRAME, hop_length: int = DEFAULT_HOP):
    import numpy as np

    if y.ndim != 1:
        raise ValueError("audio signal must be mono 1-D array")
    if y.size < frame_length:
        y = np.pad(y, (0, frame_length - y.size), mode="constant")
    n_frames = 1 + max(0, (y.size - frame_length) // hop_length)
    if n_frames <= 0:
        return np.zeros((0, frame_length), dtype=np.float32)
    shape = (n_frames, frame_length)
    strides = (y.strides[0] * hop_length, y.strides[0])
    return np.lib.stride_tricks.as_strided(y, shape=shape, strides=strides)


def _frame_times(n_frames: int, sr: int):
    import numpy as np

    centers = np.arange(int(n_frames), dtype=np.float64) * float(DEFAULT_HOP)
    centers += float(DEFAULT_FRAME) / 2.0
    return centers / float(sr)


def _mag_spectrogram(y, sr: int):
    import numpy as np

    frames = _frame_signal(y)
    if frames.size == 0:
        freqs = np.fft.rfftfreq(DEFAULT_FRAME, d=1.0 / float(sr))
        return np.zeros((freqs.size, 0), dtype=np.float32), freqs
    window = np.hanning(DEFAULT_FRAME).astype(np.float32)
    S = np.abs(np.fft.rfft(frames * window, n=DEFAULT_FRAME, axis=1)).T
    freqs = np.fft.rfftfreq(DEFAULT_FRAME, d=1.0 / float(sr))
    return S.astype(np.float32, copy=False), freqs


def _frame_rms(y):
    import numpy as np

    frames = _frame_signal(y)
    if frames.size == 0:
        return np.zeros(0, dtype=np.float32)
    return np.sqrt(np.mean(np.square(frames.astype(np.float32)), axis=1))


def _spectral_flux(S):
    import numpy as np

    if S.shape[1] == 0:
        return np.zeros(0, dtype=np.float32)
    log_s = np.log1p(S.astype(np.float32, copy=False))
    diff = np.diff(log_s, axis=1, prepend=log_s[:, :1])
    flux = np.maximum(diff, 0.0).sum(axis=0)
    return flux.astype(np.float32, copy=False)


def _zscore(arr):
    import numpy as np

    arr = np.asarray(arr, dtype=np.float32)
    if arr.size == 0:
        return arr
    std = float(arr.std())
    if std < 1e-9:
        return np.zeros_like(arr)
    return (arr - float(arr.mean())) / std


def _detect_beats(y, sr: int, clip_start_abs: float):
    import numpy as np

    S, _freqs = _mag_spectrogram(y, sr)
    flux = _spectral_flux(S)
    if flux.size < 4:
        raise RuntimeError("invalid BPM: onset envelope is too short")

    env = _zscore(flux)
    env = np.maximum(env, 0.0)
    if float(env.max(initial=0.0)) <= 0.0:
        raise RuntimeError("invalid BPM: onset envelope is flat")

    hop_sec = float(DEFAULT_HOP) / float(sr)
    min_bpm = 45.0
    max_bpm = 220.0
    min_lag = max(1, int(round(60.0 / (max_bpm * hop_sec))))
    max_lag = min(env.size - 1, int(round(60.0 / (min_bpm * hop_sec))))
    if max_lag <= min_lag:
        raise RuntimeError("invalid BPM: clip too short for beat estimation")

    best_lag = 0
    best_score = -1.0
    for lag in range(min_lag, max_lag + 1):
        a = env[:-lag]
        b = env[lag:]
        if a.size == 0 or b.size == 0:
            continue
        score = float(np.dot(a, b)) / float(a.size)
        if score > best_score:
            best_lag = lag
            best_score = score
    if best_lag <= 0 or best_score <= 0.0:
        raise RuntimeError("invalid BPM: no positive beat autocorrelation")

    bpm_raw = 60.0 / (float(best_lag) * hop_sec)

    bpm_doubled = bpm_raw > BPM_DOUBLING_THRESHOLD
    bpm_halved = bpm_raw < BPM_HALVING_THRESHOLD
    if bpm_doubled:
        bpm = bpm_raw / 2.0
    elif bpm_halved:
        bpm = bpm_raw * 2.0
    else:
        bpm = bpm_raw

    period = 60.0 / float(bpm)
    frame_times = _frame_times(env.size, sr)
    anchor_idx = int(np.argmax(env))
    anchor_rel = float(frame_times[anchor_idx])
    while anchor_rel - period >= 0.0:
        anchor_rel -= period
    clip_duration = float(len(y)) / float(sr)
    beats_abs = []
    t = anchor_rel
    while t <= clip_duration + (period * 0.5):
        if t >= 0.0:
            beats_abs.append(float(t) + float(clip_start_abs))
        t += period

    downbeats_abs = beats_abs[::4]
    return bpm, bpm_raw, (bpm_doubled or bpm_halved), beats_abs, downbeats_abs


def _detect_onsets(y, sr: int, clip_start_abs: float) -> List[float]:
    import numpy as np
    from scipy.signal import find_peaks  # type: ignore

    S, _freqs = _mag_spectrogram(y, sr)
    flux = _spectral_flux(S)
    if flux.size == 0:
        return []
    env = np.maximum(_zscore(flux), 0.0)
    if float(env.max(initial=0.0)) <= 0.0:
        return []

    min_dist = max(1, int(round(0.08 * float(sr) / float(DEFAULT_HOP))))
    height_thr = max(0.25, float(np.percentile(env, 70.0)))
    prominence = max(0.05, float(env.std()) * 0.15)
    idxs, _props = find_peaks(
        env,
        height=height_thr,
        prominence=prominence,
        distance=min_dist,
    )
    if idxs.size == 0:
        return []

    rms = _frame_rms(y)
    times_rel = _frame_times(env.size, sr)
    out: List[float] = []
    last_t = -999.0
    for idx in idxs.tolist():
        back_lo = max(0, int(idx) - 3)
        back_hi = min(rms.size, int(idx) + 1)
        if back_hi > back_lo:
            local = rms[back_lo:back_hi]
            idx = back_lo + int(np.argmin(local))
        t = float(times_rel[int(idx)]) + float(clip_start_abs)
        if t - last_t < 0.03:
            continue
        out.append(t)
        last_t = t
    return out


def _classify_onsets(
    y, sr: int, clip_start_abs: float, onset_times_abs: List[float],
) -> List[OnsetEvent]:
    """
    For each onset, compute spectrum in a ±ONSET_WIN_SEC window (FFT on the
    short chunk), sum energy per band, and normalize by the track-wide mean
    of that band's per-frame energy. The dominant (highest normalized) band
    wins. Confidence is derived from the dominant/second ratio.
    """
    if not onset_times_abs:
        return []
    import numpy as np

    # Track-wide per-band baseline energy (mean of band-summed STFT magnitude).
    S, freqs = _mag_spectrogram(y, sr)
    band_means: Dict[str, float] = {}
    for name, lo, hi in ONSET_BANDS_HZ:
        mask = _band_mask(freqs, lo, hi)
        if mask.any():
            band_energy = S[mask, :].sum(axis=0)
            band_means[name] = max(float(band_energy.mean()), 1e-9)
        else:
            band_means[name] = 1e-9

    win_samples = int(round(ONSET_WIN_SEC * 2.0 * sr))  # full window = ±win
    half_win = max(1, win_samples // 2)
    freqs_rfft = np.fft.rfftfreq(DEFAULT_FRAME, d=1.0 / sr)

    classified: List[OnsetEvent] = []
    for t_abs in onset_times_abs:
        t_rel = t_abs - clip_start_abs
        center_sample = int(round(t_rel * sr))
        lo_s = max(0, center_sample - half_win)
        hi_s = min(len(y), center_sample + half_win)
        chunk_len = hi_s - lo_s
        if chunk_len < 16:
            classified.append(OnsetEvent(
                t=round(t_abs, 3), type="unknown",
                confidence=0.0, band_energies={},
            ))
            continue

        # zero-pad to DEFAULT_FRAME for consistent frequency bins
        chunk = y[lo_s:hi_s].astype(np.float32)
        window = np.hanning(chunk_len).astype(np.float32)
        windowed = chunk * window
        if chunk_len < DEFAULT_FRAME:
            padded = np.zeros(DEFAULT_FRAME, dtype=np.float32)
            padded[:chunk_len] = windowed
            spectrum_full = np.abs(np.fft.rfft(padded))
        else:
            spectrum_full = np.abs(np.fft.rfft(windowed[:DEFAULT_FRAME]))

        band_energies_norm: Dict[str, float] = {}
        for name, lo, hi in ONSET_BANDS_HZ:
            mask = _band_mask(freqs_rfft, lo, hi)
            energy = float(spectrum_full[mask].sum()) if mask.any() else 0.0
            band_energies_norm[name] = energy / band_means[name]

        max_norm = max(band_energies_norm.values()) if band_energies_norm else 0.0
        if max_norm < ONSET_DOMINANCE_MIN:
            classified.append(OnsetEvent(
                t=round(t_abs, 3), type="unknown",
                confidence=0.0,
                band_energies={k: round(v, 3) for k, v in band_energies_norm.items()},
            ))
            continue

        sorted_energies = sorted(band_energies_norm.values(), reverse=True)
        dominant_name = max(band_energies_norm.items(), key=lambda kv: kv[1])[0]
        if len(sorted_energies) >= 2 and sorted_energies[1] > 1e-6:
            ratio = sorted_energies[0] / sorted_energies[1]
            conf = max(0.0, min(1.0, (ratio - 1.0) / (ONSET_CONF_RATIO_SAT - 1.0)))
        else:
            conf = 1.0

        classified.append(OnsetEvent(
            t=round(t_abs, 3),
            type=dominant_name,
            confidence=round(conf, 3),
            band_energies={k: round(v, 3) for k, v in band_energies_norm.items()},
        ))

    return classified


def _detect_spectral_peaks(y, sr: int, clip_start_abs: float) -> List[SpectralPeak]:
    import numpy as np
    from scipy.signal import find_peaks  # type: ignore

    S, freqs = _mag_spectrogram(y, sr)
    times_rel = _frame_times(S.shape[1], sr)

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
    import numpy as np
    from scipy.signal import find_peaks  # type: ignore

    rms = _frame_rms(y)
    S, freqs = _mag_spectrogram(y, sr)
    flux = _spectral_flux(S)
    low_mask = _band_mask(freqs, *LOW_BAND_HZ)
    low_energy = S[low_mask, :].sum(axis=0) if low_mask.any() else np.zeros(S.shape[1])

    n = min(len(rms), len(flux), len(low_energy))
    rms = rms[:n]; flux = flux[:n]; low_energy = low_energy[:n]
    win = max(1, int(round(0.5 * sr / DEFAULT_HOP)))

    def pos_jump(arr):
        prev = np.concatenate([np.full(win, arr[0]), arr[:-win]])
        return np.maximum(arr - prev, 0.0)

    z_rms = _zscore(pos_jump(rms))
    z_flux = _zscore(flux)
    z_low = _zscore(pos_jump(low_energy))
    score = DROP_W_RMS * z_rms + DROP_W_FLUX * z_flux + DROP_W_LOWJUMP * z_low

    times_rel = _frame_times(n, sr)
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
    import numpy as np

    win_samples = int(DENSITY_WIN_SEC * sr)
    hop_samples = int(DENSITY_HOP_SEC * sr)
    total = len(y)
    if total < win_samples:
        return []

    S, freqs = _mag_spectrogram(y, sr)
    eps = 1e-9
    S_safe = S + eps
    flatness = np.exp(np.mean(np.log(S_safe), axis=0)) / np.mean(S_safe, axis=0)
    mag_sum = S_safe.sum(axis=0)
    centroid = (freqs[:, None] * S_safe).sum(axis=0) / mag_sum
    bandwidth = np.sqrt((((freqs[:, None] - centroid) ** 2) * S_safe).sum(axis=0) / mag_sum)
    rms = _frame_rms(y)
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
        onset_rate_norm = min(onset_rate / 8.0, 1.0)

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

    if len(samples) >= DENSITY_SMOOTH_TAPS:
        taps = DENSITY_SMOOTH_TAPS
        smoothed_d = []
        for i in range(len(samples)):
            lo = max(0, i - taps // 2)
            hi = min(len(samples), i + taps // 2 + 1)
            smoothed_d.append(sum(s.density for s in samples[lo:hi]) / (hi - lo))
        for s, d in zip(samples, smoothed_d):
            s.density = d

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
    if not density_curve:
        return []

    def label_for(t: float, d: float) -> str:
        if drop_t is None:
            if d < DENSITY_LOW_THR:  return "low"
            if d > DENSITY_HIGH_THR: return "high"
            return "mid"
        if t < drop_t:
            return "low" if d < DENSITY_LOW_THR else "mid"
        if t < drop_t + DROP_WINDOW_SEC:
            return "drop"
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
    onsets_classified = _classify_onsets(y, sr_out, clip_start_abs, onsets_abs)
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
        onsets_classified=onsets_classified,
        drop_candidates=drops,
        spectral_peaks=spectral_peaks,
        density_curve=density_curve,
        sections=sections,
        energy_envelope_hop_sec=round(hop_sec, 4),
        energy_envelope=envelope if include_envelope else [],
    )


def to_jsonable(obj: HookAnalysis) -> dict:
    """Serialize HookAnalysis to a JSON-safe dict."""
    d = asdict(obj)
    d["focus_clip"] = asdict(obj.focus_clip)
    d["drop_candidates"] = [asdict(c) for c in obj.drop_candidates]
    d["spectral_peaks"] = [asdict(p) for p in obj.spectral_peaks]
    d["density_curve"] = [asdict(s) for s in obj.density_curve]
    d["sections"] = [asdict(s) for s in obj.sections]
    d["onsets_classified"] = [asdict(o) for o in obj.onsets_classified]
    return d


# Backward-compat alias
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
    payload = json.dumps(to_jsonable(result), ensure_ascii=False, indent=2)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
        print(f"[ok] wrote {args.out}")
    else:
        sys.stdout.write(payload + "\n")


if __name__ == "__main__":
    _main()
