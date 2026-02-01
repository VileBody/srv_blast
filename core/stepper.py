# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional

from core.types import KeyframeData, KeyframeEase

PercentProp = Literal["start", "end"]
Anchor = Literal["start", "end"]


@dataclass(frozen=True)
class Token:
    """
    Word-level token from SRT / aligner.

    text: token text (word)
    t_start/t_end: absolute times (seconds) for that token
    trailing: optional, the character that follows this token in the phrase (" ", "\\r", "", etc.)
              (kept for future, does not affect percent-by-words mode)
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
        start_word=0 => first token => 100*(1/N)
        start_word=1 => second token => 100*(2/N), etc.

    hold:
      - True: hold+jump pattern (2 keys per transition, 1 key for first step)
      - False: 1 key per token step

    fps/jump_frames:
      - jump spacing for hold->jump is jump_frames/fps seconds

    ease:
      - by default 599.4/16.6666 (matches your dumps)

    iit/oit:
      - interpolation codes (default linear 6612)
    """
    percent_prop: PercentProp = "start"
    anchor: Anchor = "end"
    start_word: int = 0
    hold: bool = True
    fps: float = 23.9759979248047
    jump_frames: int = 1
    ease_speed: float = 599.4
    ease_influence: float = 16.666666667
    iit: str = "6612"
    oit: str = "6612"


def _dt(cfg: StepperConfig) -> float:
    if cfg.fps <= 0:
        raise ValueError("StepperConfig.fps must be > 0")
    if cfg.jump_frames <= 0:
        raise ValueError("StepperConfig.jump_frames must be > 0")
    return float(cfg.jump_frames) / float(cfg.fps)


def _pct(i: int, n: int) -> float:
    # i is token index, n total tokens
    return 100.0 * (float(i + 1) / float(n))


def _time(tok: Token, anchor: Anchor) -> float:
    return float(tok.t_start) if anchor == "start" else float(tok.t_end)


def build_percent_keyframes_by_words(
    tokens: List[Token],
    cfg: StepperConfig,
) -> List[KeyframeData]:
    """
    Build keyframes for Percent Start/End based ONLY on number of tokens.

    Output list is keyframes for the chosen percent property:
      - cfg.percent_prop == "start" => ADBE Text Percent Start
      - cfg.percent_prop == "end"   => ADBE Text Percent End

    You will still set match_name separately in PropertyData.
    """
    if not tokens:
        raise ValueError("tokens must be non-empty")

    n = len(tokens)
    if cfg.start_word < 0 or cfg.start_word >= n:
        raise ValueError(f"start_word out of range: {cfg.start_word} for n={n}")

    # Anchor times per token
    times = [_time(tok, cfg.anchor) for tok in tokens]
    dt_sec = _dt(cfg)

    def kf(t: float, v: float, ease_in: bool, ease_out: bool) -> KeyframeData:
        kd = KeyframeData(t=float(t), v=float(v), iit=cfg.iit, oit=cfg.oit)
        if ease_in:
            kd.ease_in = [KeyframeEase(speed=cfg.ease_speed, influence=cfg.ease_influence)]
        if ease_out:
            kd.ease_out = [KeyframeEase(speed=cfg.ease_speed, influence=cfg.ease_influence)]
        return kd

    kfs: List[KeyframeData] = []

    # First visible step at start_word
    i0 = cfg.start_word
    kfs.append(kf(times[i0], _pct(i0, n), ease_in=False, ease_out=False))

    if not cfg.hold:
        # one key per subsequent word
        for i in range(i0 + 1, n):
            kfs.append(kf(times[i], _pct(i, n), ease_in=False, ease_out=False))
        return kfs

    # hold+jump pattern:
    # for each subsequent token i:
    #   hold at (times[i] - dt) with prev percent, ease_out
    #   jump at times[i] with new percent, ease_in
    for i in range(i0 + 1, n):
        prev_v = _pct(i - 1, n)
        hold_t = times[i] - dt_sec

        # if hold would land before previous time, clamp
        if hold_t < times[i - 1]:
            hold_t = times[i - 1]

        kfs.append(kf(hold_t, prev_v, ease_in=False, ease_out=True))
        kfs.append(kf(times[i], _pct(i, n), ease_in=True, ease_out=False))

    return kfs


def keyframe_match_name(cfg: StepperConfig) -> str:
    """
    Convenience: which AE property this set of keyframes should map to.
    """
    return "ADBE Text Percent Start" if cfg.percent_prop == "start" else "ADBE Text Percent End"
