# mlcore/timing_calc.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

from mlcore.models import BlocksTokensPayload, Segment


@dataclass(frozen=True)
class Timing:
    in_p: float
    out_p: float


@dataclass(frozen=True)
class Block2Timing:
    p1: Timing
    p2: Timing


@dataclass(frozen=True)
class Block4Timing:
    p1: Timing
    p2: Timing


@dataclass(frozen=True)
class Block5Timing:
    slowly_in: Timing
    fast_reveal: Timing
    glitch_peak: Timing
    mine: Timing


@dataclass(frozen=True)
class Block7Timing:
    part1: Timing
    part2: Timing


def _seg_start(seg: Segment) -> float:
    """
    DUMP-V1 style:
      layer start aligns to first word END time,
      so the first reveal key can sit exactly at in_p.
    """
    return float(seg.tokens[0].t_end)


def _seg_end(seg: Segment) -> float:
    return float(seg.tokens[-1].t_end)


def compute_timings(payload: BlocksTokensPayload) -> Tuple[Dict[str, object], float]:
    """
    Returns:
      timings: dict per block
      comp_dur: float

    Base rule (DUMP-V1 style):
      - block_1 starts at 0.0 (keep)
      - start of each subsequent block/segment = t_end of the FIRST token (for normal text segments)
      - out of a block (except last) = start of next block
      - internal seams = start of next segment (t_end of its first token)
      - out of the last block = t_end of last token + 1.0

    Variant A addition:
      - block_5.mine is a dedicated Mine segment (exact token window)
      - glitch_peak OUT is clamped to mine IN:
            glitch_peak.out == mine.in
        so peak never overlaps Mine window visually.
    """
    # starts
    b1_in = 0.0

    b2_in = _seg_start(payload.block_2.p1)
    b3_in = float(payload.block_3.tokens[0].t_end)
    b4_in = _seg_start(payload.block_4.p1)

    b5_in = _seg_start(payload.block_5.slowly_in)
    b6_in = float(payload.block_6.tokens[0].t_end)
    b7_in = _seg_start(payload.block_7.part1)

    # outs (except last)
    b1_out = b2_in
    b2_out = b3_in
    b3_out = b4_in
    b4_out = b5_in
    b5_out = b6_in
    b6_out = b7_in

    # last out
    b7_last_end = _seg_end(payload.block_7.part2)
    b7_out = b7_last_end + 1.0
    comp_dur = b7_out

    # block 2 internal seam (p2 start)
    b2_p1_in = b2_in
    b2_p2_in = _seg_start(payload.block_2.p2)
    b2_p1_out = b2_p2_in
    b2_p2_out = b2_out

    # block 4 internal seam
    b4_p1_in = b4_in
    b4_p2_in = _seg_start(payload.block_4.p2)
    b4_p1_out = b4_p2_in
    b4_p2_out = b4_out

    # block 5 internal seams (normal segments)
    b5_s_in = b5_in
    b5_f_in = _seg_start(payload.block_5.fast_reveal)
    b5_g_in = _seg_start(payload.block_5.glitch_peak)

    b5_s_out = b5_f_in
    b5_f_out = b5_g_in

    # block 5 mine timing (special: exact token window)
    mine_tok = payload.block_5.mine.tokens[0]
    b5_m_in = float(mine_tok.t_start)
    b5_m_out = float(mine_tok.t_end)

    # IMPORTANT: glitch_peak.out == mine.in (clamped, never earlier than glitch_peak.in)
    b5_g_out = max(b5_g_in, b5_m_in)

    # block 7 internal seam
    b7_p1_in = b7_in
    b7_p2_in = _seg_start(payload.block_7.part2)
    b7_p1_out = b7_p2_in
    b7_p2_out = b7_out

    timings: Dict[str, object] = {
        "block_1": Timing(b1_in, b1_out),
        "block_2": Block2Timing(
            p1=Timing(b2_p1_in, b2_p1_out),
            p2=Timing(b2_p2_in, b2_p2_out),
        ),
        "block_3": Timing(b3_in, b3_out),
        "block_4": Block4Timing(
            p1=Timing(b4_p1_in, b4_p1_out),
            p2=Timing(b4_p2_in, b4_p2_out),
        ),
        "block_5": Block5Timing(
            slowly_in=Timing(b5_s_in, b5_s_out),
            fast_reveal=Timing(b5_f_in, b5_f_out),
            glitch_peak=Timing(b5_g_in, b5_g_out),
            mine=Timing(b5_m_in, b5_m_out),
        ),
        "block_6": Timing(b6_in, b6_out),
        "block_7": Block7Timing(
            part1=Timing(b7_p1_in, b7_p1_out),
            part2=Timing(b7_p2_in, b7_p2_out),
        ),
    }

    return timings, comp_dur
