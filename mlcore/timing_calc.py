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

@dataclass(frozen=True)
class Block7Timing:
    part1: Timing
    part2: Timing

def _seg_start(seg: Segment) -> float:
    return float(seg.tokens[0].t_start)

def _seg_end(seg: Segment) -> float:
    return float(seg.tokens[-1].t_end)

def compute_timings(payload: BlocksTokensPayload) -> Tuple[Dict[str, object], float]:
    """
    Возвращает:
      timings: dict по блокам (где нужно — вложенные тайминги сегментов)
      comp_dur: float
    Правило:
      - block_1 starts at 0.0
      - start блока = t_start первого токена его первого сегмента
      - out блока (кроме последнего) = start следующего блока
      - out последнего блока = t_end последнего токена + 1.0
      - внутри составных блоков сегменты режем по start следующего сегмента
    """
    # starts for blocks
    b1_in = 0.0
    b2_in = _seg_start(payload.block_2.p1)
    b3_in = float(payload.block_3.tokens[0].t_start)
    b4_in = _seg_start(payload.block_4.p1)
    b5_in = _seg_start(payload.block_5.slowly_in)
    b6_in = float(payload.block_6.tokens[0].t_start)
    b7_in = _seg_start(payload.block_7.part1)

    # block out's (except last)
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

    # block 2 internal seam
    b2_p1_in = b2_in
    b2_p2_in = _seg_start(payload.block_2.p2)
    b2_p1_out = b2_p2_in
    b2_p2_out = b2_out

    # block 4 internal seam
    b4_p1_in = b4_in
    b4_p2_in = _seg_start(payload.block_4.p2)
    b4_p1_out = b4_p2_in
    b4_p2_out = b4_out

    # block 5 internal seams
    b5_s_in = b5_in
    b5_f_in = _seg_start(payload.block_5.fast_reveal)
    b5_g_in = _seg_start(payload.block_5.glitch_peak)
    b5_s_out = b5_f_in
    b5_f_out = b5_g_in
    b5_g_out = b5_out

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
        ),
        "block_6": Timing(b6_in, b6_out),
        "block_7": Block7Timing(
            part1=Timing(b7_p1_in, b7_p1_out),
            part2=Timing(b7_p2_in, b7_p2_out),
        ),
    }

    return timings, comp_dur
