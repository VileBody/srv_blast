# core/stepper.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal

from core.fps import COMP_FPS
from core.types import KeyframeData, KeyframeEase

PercentProp = Literal["start", "end"]
Anchor = Literal["start", "end"]


@dataclass(frozen=True)
class Token:
    """
    Word-level token from SRT / aligner.

    text: token text (word)
    t_start/t_end: absolute times (seconds)
    trailing: optional, the character that follows this token in the phrase (" ", "\r", "", etc.)
    """
    text: str
    t_start: float
    t_end: float
    trailing: str = ""


@dataclass(frozen=True)
class StepperConfig:
    """
    Core config for word-stepper.

    percent_prop:
      - "start" -> ADBE Text Percent Start
      - "end"   -> ADBE Text Percent End

    anchor:
      - "start" uses token.t_start
      - "end"   uses token.t_end

    start_word:
      - index of first token that produces an initial visible percent.

    hold:
      - True: hold+jump pattern (DUMP-V1 exact), 1 + 2*(n-1) keys
      - False: 1 key per token step (no hold keys)

    fps/jump_frames:
      - dt = jump_frames/fps seconds
        DUMP-V1 uses jump_frames=1 always.

    last_jump_advance_frames:
      - IMPORTANT for AE boundary behavior:
        If anchor=="end", the last token's jump at t_end can land exactly on layer.outPoint,
        making the last word invisible (0 drawn frames).
        To match real editorial behavior, we advance the LAST jump by N frames (default 1).

        Example:
          last token end = 2.500000
          fps ~ 23.976 => dt ~ 0.041708
          last jump becomes 2.458292 (visible for ~1 frame before outPoint=2.5)

    ease:
      - speed 599.4 / influence 16.666666667 matches dumps

    iit/oit:
      - default linear 6612
    """
    percent_prop: PercentProp = "start"
    anchor: Anchor = "end"
    start_word: int = 0
    hold: bool = True
    fps: float = COMP_FPS
    jump_frames: int = 1
    last_jump_advance_frames: int = 0  # <--- key fix
    ease_speed: float = 599.4
    ease_influence: float = 16.666666667
    iit: str = "6612"
    oit: str = "6612"


def _dt(cfg: StepperConfig, frames: int) -> float:
    if cfg.fps <= 0:
        raise ValueError("StepperConfig.fps must be > 0")
    if frames <= 0:
        raise ValueError("frames must be > 0")
    return float(frames) / float(cfg.fps)


def _pct(i: int, n: int) -> float:
    return 100.0 * (float(i + 1) / float(n))


def _time(tok: Token, anchor: Anchor) -> float:
    return float(tok.t_start) if anchor == "start" else float(tok.t_end)


def _kfe(speed: float, influence: float) -> KeyframeEase:
    return KeyframeEase(speed=float(speed), influence=float(influence))


def _push_after(t: float, *, prev_t: float, eps: float) -> float:
    """
    Ensure strictly increasing time.
    """
    t = float(t)
    if t <= float(prev_t) + float(eps):
        return float(prev_t) + float(eps)
    return t


def build_percent_keyframes_by_words(tokens: List[Token], cfg: StepperConfig) -> List[KeyframeData]:
    """
    Build keyframes for Percent Start/End.

    DUMP-V1 shape when cfg.hold=True:
      - first key at time(start_word) with v=pct(start_word)
      - for each next token i:
          HOLD at (time_i - dt): v=pct(i-1), ease_out=599.4
          JUMP at time_i: v=pct(i), ease_in=599.4

    With our AE-safe tweak:
      - if i is last token AND cfg.anchor=="end" AND cfg.last_jump_advance_frames>0
        then JUMP time is advanced by N frames so it lands before outPoint.
    """
    if not tokens:
        raise ValueError("tokens must be non-empty")

    n = len(tokens)
    if cfg.start_word < 0 or cfg.start_word >= n:
        raise ValueError(f"start_word out of range: {cfg.start_word} for n={n}")

    times = [_time(tok, cfg.anchor) for tok in tokens]

    dt_sec = _dt(cfg, cfg.jump_frames)
    last_adv_sec = 0.0
    if cfg.anchor == "end" and cfg.last_jump_advance_frames > 0:
        last_adv_sec = _dt(cfg, cfg.last_jump_advance_frames)

    # tiny epsilon for ordering protection (should almost never trigger in clean data)
    eps = (1.0 / float(cfg.fps)) / 10.0

    def kf0(t: float, v: float) -> KeyframeData:
        kd = KeyframeData(t=float(t), v=float(v), iit=cfg.iit, oit=cfg.oit)
        kd.ease_in = [_kfe(0.0, cfg.ease_influence)]
        kd.ease_out = [_kfe(0.0, cfg.ease_influence)]
        return kd

    def kf_hold(t: float, v: float) -> KeyframeData:
        kd = KeyframeData(t=float(t), v=float(v), iit=cfg.iit, oit=cfg.oit)
        kd.ease_in = [_kfe(0.0, cfg.ease_influence)]
        kd.ease_out = [_kfe(cfg.ease_speed, cfg.ease_influence)]
        return kd

    def kf_jump(t: float, v: float) -> KeyframeData:
        kd = KeyframeData(t=float(t), v=float(v), iit=cfg.iit, oit=cfg.oit)
        kd.ease_in = [_kfe(cfg.ease_speed, cfg.ease_influence)]
        kd.ease_out = [_kfe(0.0, cfg.ease_influence)]
        return kd

    kfs: List[KeyframeData] = []

    i0 = cfg.start_word
    t0 = float(times[i0])
    kfs.append(kf0(t0, _pct(i0, n)))

    if not cfg.hold:
        for i in range(i0 + 1, n):
            t_raw = float(times[i])
            # AE-safe: advance the LAST key if anchor=end
            if i == n - 1 and last_adv_sec > 0.0:
                t_raw = t_raw - float(last_adv_sec)

            t = _push_after(t_raw, prev_t=float(kfs[-1].t), eps=eps)
            kfs.append(KeyframeData(t=float(t), v=float(_pct(i, n)), iit=cfg.iit, oit=cfg.oit))
        return kfs

    # DUMP-V1 hold+jump with AE-safe last-jump advance
    for i in range(i0 + 1, n):
        prev_v = _pct(i - 1, n)

        jump_t_raw = float(times[i])

        # AE-safe: if this is the LAST token and anchor=end, move jump earlier by N frames
        if i == n - 1 and last_adv_sec > 0.0:
            jump_t_raw = jump_t_raw - float(last_adv_sec)

        hold_t_raw = jump_t_raw - float(dt_sec)

        # preserve order strictly: hold then jump
        hold_t = _push_after(hold_t_raw, prev_t=float(kfs[-1].t), eps=eps)
        jump_t = _push_after(jump_t_raw, prev_t=float(hold_t), eps=eps)

        kfs.append(kf_hold(hold_t, prev_v))
        kfs.append(kf_jump(jump_t, _pct(i, n)))

    return kfs


def keyframe_match_name(cfg: StepperConfig) -> str:
    return "ADBE Text Percent Start" if cfg.percent_prop == "start" else "ADBE Text Percent End"
