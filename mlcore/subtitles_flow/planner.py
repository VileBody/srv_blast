from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, List, Sequence, Type

from pydantic import BaseModel

from core.subtitles_mode import (
    SUBTITLES_MODE_IMPULSE_2ND,
    SUBTITLES_MODE_LEGACY_BLOCKS,
    SUBTITLES_MODE_SCENES_3RD,
    normalize_subtitles_mode,
)
from mlcore.models.stage1_plan import Stage1PlanPayload
from mlcore.models.subtitles_flow import (
    Impulse2ndRawPayload,
    Scene3rdPayloadScene,
    Scenes3rdPayload,
    SubtitleFlowPlan,
    SubtitleFlowSegment,
    SubtitleFlowToken,
)
from mlcore.models.subtitles_tokens import BlocksTokensPayload, ClipWindow
from mlcore.prompts import (
    build_stage2_subtitles_system_instruction,
    build_stage2_subtitles_user_prompt,
)
from .impulse_adapter import flow_to_impulse_raw_payload


_MINOR_CLAMP_EPS = 0.06
_CLOSE_GAP_EPS = 0.08
_MIN_SEGMENT_DUR = 0.12
_MAX_LINE_CHARS_WARNING = 24
_IMPULSE_LAST_SEGMENT_TAIL_PAD_EPS = 0.55


@dataclass(frozen=True)
class SubtitleFlowWarning:
    mode: str
    segment_id: str
    reason: str
    action: str


class BaseSubtitlesPlanner:
    mode: str
    schema_model: Type[BaseModel]
    use_tokens_structured: bool = False

    def build_system_instruction(self) -> str:
        return build_stage2_subtitles_system_instruction(subtitles_mode=self.mode)

    def build_user_prompt(self, *, stage1_json: dict[str, Any]) -> str:
        return build_stage2_subtitles_user_prompt(
            stage1_json=stage1_json,
            subtitles_mode=self.mode,
            schema_name=self.schema_model.__name__,
        )

    def validate_resume_payload(self, data: dict[str, Any]) -> BlocksTokensPayload | SubtitleFlowPlan:
        raise NotImplementedError

    def normalize_payload(
        self,
        *,
        payload: BaseModel,
        stage1: Stage1PlanPayload,
        logger: logging.Logger,
    ) -> BlocksTokensPayload | SubtitleFlowPlan:
        raise NotImplementedError

    def _emit(self, logger: logging.Logger, warnings: Sequence[SubtitleFlowWarning]) -> None:
        for w in warnings:
            logger.warning(
                "subtitle_flow_warning mode=%s segment_id=%s reason=%s action=%s",
                w.mode,
                w.segment_id,
                w.reason,
                w.action,
            )


class LegacyBlocksPlanner(BaseSubtitlesPlanner):
    mode = SUBTITLES_MODE_LEGACY_BLOCKS
    schema_model = BlocksTokensPayload
    use_tokens_structured = True

    def validate_resume_payload(self, data: dict[str, Any]) -> BlocksTokensPayload:
        return BlocksTokensPayload.model_validate(data)

    def normalize_payload(
        self,
        *,
        payload: BaseModel,
        stage1: Stage1PlanPayload,
        logger: logging.Logger,
    ) -> BlocksTokensPayload:
        del logger
        if not isinstance(payload, BlocksTokensPayload):
            payload = BlocksTokensPayload.model_validate(payload.model_dump(mode="json"))
        if abs(float(payload.clip.start) - float(stage1.audio.clip_start_abs)) > 1e-6:
            raise ValueError("subtitles.clip.start must equal stage1.audio.clip_start_abs")
        if abs(float(payload.clip.end) - float(stage1.audio.clip_end_abs)) > 1e-6:
            raise ValueError("subtitles.clip.end must equal stage1.audio.clip_end_abs")
        return payload


class _FlowPlannerBase(BaseSubtitlesPlanner):
    def validate_resume_payload(self, data: dict[str, Any]) -> SubtitleFlowPlan:
        flow = SubtitleFlowPlan.model_validate(data)
        if str(flow.mode) != self.mode:
            raise ValueError(f"resume subtitles mode mismatch: {flow.mode!r} != {self.mode!r}")
        return flow

    def _clip_from_stage1(self, stage1: Stage1PlanPayload) -> ClipWindow:
        return ClipWindow.model_validate(
            {
                "start": float(stage1.audio.clip_start_abs),
                "end": float(stage1.audio.clip_end_abs),
            }
        )

    def _minor_clamp(
        self,
        *,
        value: float,
        low: float,
        high: float,
        segment_id: str,
        reason: str,
        warnings: List[SubtitleFlowWarning],
    ) -> float:
        if value < low:
            if (low - value) > _MINOR_CLAMP_EPS:
                raise ValueError(f"{reason}: value={value} < low={low} (segment_id={segment_id})")
            warnings.append(
                SubtitleFlowWarning(
                    mode=self.mode,
                    segment_id=segment_id,
                    reason=reason,
                    action=f"clamped_to_low={low:.6f}",
                )
            )
            return float(low)
        if value > high:
            if (value - high) > _MINOR_CLAMP_EPS:
                raise ValueError(f"{reason}: value={value} > high={high} (segment_id={segment_id})")
            warnings.append(
                SubtitleFlowWarning(
                    mode=self.mode,
                    segment_id=segment_id,
                    reason=reason,
                    action=f"clamped_to_high={high:.6f}",
                )
            )
            return float(high)
        return float(value)

    def _finalize_flow(
        self,
        *,
        clip: ClipWindow,
        segments: List[SubtitleFlowSegment],
        warnings: List[SubtitleFlowWarning],
        logger: logging.Logger,
    ) -> SubtitleFlowPlan:
        if not segments:
            raise ValueError("subtitle flow plan is empty")

        segs = sorted(segments, key=lambda s: (float(s.in_point), str(s.segment_id)))
        for i, seg in enumerate(segs):
            seg_id = str(seg.segment_id)
            dur = float(seg.out_point) - float(seg.in_point)
            if dur < 0.35:
                warnings.append(
                    SubtitleFlowWarning(
                        mode=self.mode,
                        segment_id=seg_id,
                        reason="short_segment",
                        action=f"kept duration={dur:.3f}",
                    )
                )

            if len(str(seg.text).strip()) == 0:
                raise ValueError(f"empty segment text (segment_id={seg_id})")

            low_words = [w for w in str(seg.text).replace("\r", " ").split(" ") if w]
            for j in range(1, len(low_words)):
                if low_words[j - 1].lower() == low_words[j].lower():
                    warnings.append(
                        SubtitleFlowWarning(
                            mode=self.mode,
                            segment_id=seg_id,
                            reason="repeated_word",
                            action=f"kept pair={low_words[j - 1]!r}",
                        )
                    )
                    break

            for ln in seg.lines:
                if len(" ".join(str(ln).split())) > _MAX_LINE_CHARS_WARNING:
                    warnings.append(
                        SubtitleFlowWarning(
                            mode=self.mode,
                            segment_id=seg_id,
                            reason="line_length",
                            action=f"kept line chars={len(' '.join(str(ln).split()))}",
                        )
                    )
                    break

            if i > 0:
                prev = segs[i - 1]
                if float(seg.in_point) < float(prev.out_point) - 1e-6:
                    overlap = float(prev.out_point) - float(seg.in_point)
                    if overlap > _MINOR_CLAMP_EPS:
                        raise ValueError(
                            "critical segment overlap: "
                            f"prev={prev.segment_id}({prev.in_point}..{prev.out_point}) "
                            f"curr={seg.segment_id}({seg.in_point}..{seg.out_point})"
                        )
                    seg.in_point = float(prev.out_point)
                    if float(seg.out_point) <= float(seg.in_point):
                        seg.out_point = float(seg.in_point) + _MIN_SEGMENT_DUR
                    warnings.append(
                        SubtitleFlowWarning(
                            mode=self.mode,
                            segment_id=seg_id,
                            reason="minor_overlap",
                            action=f"clamped in_point to {seg.in_point:.6f}",
                        )
                    )

                gap = float(seg.in_point) - float(prev.out_point)
                if 0.0 <= gap < _CLOSE_GAP_EPS:
                    warnings.append(
                        SubtitleFlowWarning(
                            mode=self.mode,
                            segment_id=seg_id,
                            reason="close_boundary",
                            action=f"kept gap={gap:.3f}",
                        )
                    )

        flow = SubtitleFlowPlan.model_validate(
            {
                "mode": self.mode,
                "clip": clip.model_dump(mode="json"),
                "segments": [s.model_dump(mode="json", by_alias=True) for s in segs],
            }
        )
        self._emit(logger, warnings)
        return flow


class Impulse2ndPlanner(_FlowPlannerBase):
    mode = SUBTITLES_MODE_IMPULSE_2ND
    schema_model = Impulse2ndRawPayload
    use_tokens_structured = False

    def normalize_payload(
        self,
        *,
        payload: BaseModel,
        stage1: Stage1PlanPayload,
        logger: logging.Logger,
    ) -> SubtitleFlowPlan:
        if not isinstance(payload, Impulse2ndRawPayload):
            payload = Impulse2ndRawPayload.model_validate(payload.model_dump(mode="json"))

        clip = self._clip_from_stage1(stage1)
        warnings: List[SubtitleFlowWarning] = []

        anchor_in_abs = self._minor_clamp(
            value=float(payload.anchor_in_abs),
            low=float(clip.start),
            high=float(clip.end),
            segment_id="anchor",
            reason="anchor_out_of_clip",
            warnings=warnings,
        )

        if payload.segments and abs(float(payload.segments[0].in_point)) > _CLOSE_GAP_EPS:
            warnings.append(
                SubtitleFlowWarning(
                    mode=self.mode,
                    segment_id="anchor",
                    reason="anchor_offset_nonzero",
                    action=f"kept first_segment_in={float(payload.segments[0].in_point):.6f}",
                )
            )

        global_tokens: List[SubtitleFlowToken] = []
        for wt in payload.word_timings:
            t_start = self._minor_clamp(
                value=anchor_in_abs + float(wt.start),
                low=float(clip.start),
                high=float(clip.end),
                segment_id="global_word_timings",
                reason="global_token_start_out_of_clip",
                warnings=warnings,
            )
            t_end = self._minor_clamp(
                value=anchor_in_abs + float(wt.end),
                low=float(clip.start),
                high=float(clip.end),
                segment_id="global_word_timings",
                reason="global_token_end_out_of_clip",
                warnings=warnings,
            )
            if t_end <= t_start:
                raise ValueError(f"global token has non-positive duration ({wt.word!r}, {t_start}..{t_end})")
            global_tokens.append(SubtitleFlowToken(text=str(wt.word), t_start=t_start, t_end=t_end))

        segments: List[SubtitleFlowSegment] = []
        effective_clip_end = float(clip.end)
        total_segments = len(payload.segments)
        for i, seg in enumerate(payload.segments, start=1):
            seg_id = f"impulse_{i:03d}"
            reason = str(seg.reason or "").strip()
            if not reason:
                warnings.append(
                    SubtitleFlowWarning(
                        mode=self.mode,
                        segment_id=seg_id,
                        reason="decision_reason_missing",
                        action="kept without reason",
                    )
                )
            seg_in = self._minor_clamp(
                value=anchor_in_abs + float(seg.in_point),
                low=float(clip.start),
                high=float(clip.end),
                segment_id=seg_id,
                reason="segment_in_out_of_clip",
                warnings=warnings,
            )
            seg_out_abs = anchor_in_abs + float(seg.out_point)
            if i == total_segments and seg_out_abs > float(clip.end):
                overshoot = float(seg_out_abs) - float(clip.end)
                if overshoot <= _IMPULSE_LAST_SEGMENT_TAIL_PAD_EPS:
                    seg_out = float(seg_out_abs)
                    effective_clip_end = max(float(effective_clip_end), float(seg_out))
                    warnings.append(
                        SubtitleFlowWarning(
                            mode=self.mode,
                            segment_id=seg_id,
                            reason="segment_out_tail_pad_extend_clip",
                            action=f"extended_clip_end_to={effective_clip_end:.6f} overshoot={overshoot:.3f}",
                        )
                    )
                else:
                    seg_out = self._minor_clamp(
                        value=seg_out_abs,
                        low=float(clip.start),
                        high=float(clip.end),
                        segment_id=seg_id,
                        reason="segment_out_out_of_clip",
                        warnings=warnings,
                    )
            else:
                seg_out = self._minor_clamp(
                    value=seg_out_abs,
                    low=float(clip.start),
                    high=float(clip.end),
                    segment_id=seg_id,
                    reason="segment_out_out_of_clip",
                    warnings=warnings,
                )
            if seg_out <= seg_in:
                if (seg_in - seg_out) > _MINOR_CLAMP_EPS:
                    raise ValueError(
                        f"invalid segment duration after clamp (segment_id={seg_id}, {seg_in}..{seg_out})"
                    )
                seg_out = seg_in + _MIN_SEGMENT_DUR
                warnings.append(
                    SubtitleFlowWarning(
                        mode=self.mode,
                        segment_id=seg_id,
                        reason="minor_duration_clamp",
                        action=f"extended out_point to {seg_out:.6f}",
                    )
                )

            tokens: List[SubtitleFlowToken] = []
            if seg.word_timings:
                for wt in seg.word_timings:
                    t_start = self._minor_clamp(
                        value=anchor_in_abs + float(wt.start),
                        low=float(clip.start),
                        high=float(clip.end),
                        segment_id=seg_id,
                        reason="token_start_out_of_clip",
                        warnings=warnings,
                    )
                    t_end = self._minor_clamp(
                        value=anchor_in_abs + float(wt.end),
                        low=float(clip.start),
                        high=float(clip.end),
                        segment_id=seg_id,
                        reason="token_end_out_of_clip",
                        warnings=warnings,
                    )
                    if t_end <= t_start:
                        raise ValueError(
                            f"token has non-positive duration (segment_id={seg_id}, {wt.word!r}, {t_start}..{t_end})"
                        )
                    tokens.append(SubtitleFlowToken(text=str(wt.word), t_start=t_start, t_end=t_end))
            elif global_tokens:
                for tok in global_tokens:
                    if float(tok.t_start) >= seg_in - 1e-6 and float(tok.t_end) <= seg_out + 1e-6:
                        tokens.append(
                            SubtitleFlowToken(
                                text=str(tok.text),
                                t_start=float(tok.t_start),
                                t_end=float(tok.t_end),
                            )
                        )
                if tokens:
                    warnings.append(
                        SubtitleFlowWarning(
                            mode=self.mode,
                            segment_id=seg_id,
                            reason="segment_tokens_from_global_word_timings",
                            action=f"attached_tokens={len(tokens)}",
                        )
                    )

            segments.append(
                SubtitleFlowSegment.model_validate(
                    {
                        "id": seg_id,
                        "text": str(seg.text),
                        "in_point": seg_in,
                        "out_point": seg_out,
                        "style_tag": str(seg.type),
                        "lines": [str(seg.text)],
                        "tokens": [t.model_dump(mode="json") for t in tokens],
                    }
                            )
                        )

        flow_clip = clip
        if effective_clip_end > float(clip.end) + 1e-9:
            flow_clip = ClipWindow.model_validate(
                {
                    "start": float(clip.start),
                    "end": float(effective_clip_end),
                }
            )
        flow_dur = float(flow_clip.end) - float(flow_clip.start)
        if flow_dur > 18.0 + 1e-6:
            warnings.append(
                SubtitleFlowWarning(
                    mode=self.mode,
                    segment_id="clip",
                    reason="clip_duration_over_18",
                    action=f"kept duration={flow_dur:.3f}",
                )
            )

        flow = self._finalize_flow(
            clip=flow_clip,
            segments=segments,
            warnings=warnings,
            logger=logger,
        )
        try:
            rt = flow_to_impulse_raw_payload(flow)
            logger.info(
                "impulse_adapter_roundtrip_ok anchor_in_abs=%.6f segments=%d",
                float(rt.anchor_in_abs),
                len(rt.segments),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("impulse_adapter_roundtrip_failed err=%s", str(e))
        return flow


class Scenes3rdPlanner(_FlowPlannerBase):
    mode = SUBTITLES_MODE_SCENES_3RD
    schema_model = Scenes3rdPayload
    use_tokens_structured = False

    def _maybe_fallback_type3_last_gap_to_type1(
        self,
        *,
        scene: Scene3rdPayloadScene,
        segment_id: str,
        warnings: List[SubtitleFlowWarning],
    ) -> None:
        if str(scene.type) != "TYPE_3":
            return
        if len(scene.word_timings) < 2:
            return
        last_gap = float(scene.word_timings[-1].start) - float(scene.word_timings[-2].end)
        if last_gap >= 0.25 - 1e-6:
            return

        scene.type = "TYPE_1"
        scene.focus_word = None
        scene.focus_style = None
        warnings.append(
            SubtitleFlowWarning(
                mode=self.mode,
                segment_id=segment_id,
                reason="type3_last_gap_fallback_type1",
                action=f"last_gap={last_gap:.3f}",
            )
        )

    def _validate_reference_scene_contract(self, scene: Scene3rdPayloadScene) -> None:
        words = [str(w).strip() for w in scene.words if str(w).strip()]
        if not words:
            raise ValueError(f"scene has no words (id={scene.id})")
        if len(words) > 5:
            raise ValueError(f"scene has >5 words (id={scene.id}, words={len(words)})")

        lines: List[List[str]] = []
        for row in scene.lines:
            row_words = [str(w).strip() for w in row if str(w).strip()]
            if row_words:
                lines.append(row_words)

        if lines:
            flat = [w for row in lines for w in row]
            if flat != words:
                raise ValueError(
                    "scene.lines must flatten to scene.words in the same order "
                    f"(id={scene.id}, flat={flat!r}, words={words!r})"
                )

        if scene.word_timings:
            wt_words = [str(wt.word).strip() for wt in scene.word_timings]
            if wt_words != words:
                raise ValueError(
                    "scene.word_timings words mismatch scene.words "
                    f"(id={scene.id}, wt={wt_words!r}, words={words!r})"
                )
            if abs(float(scene.start) - float(scene.word_timings[0].start)) > 1e-6:
                raise ValueError(f"scene.start must equal first word_timing.start (id={scene.id})")
            if abs(float(scene.end) - float(scene.word_timings[-1].end)) > 1e-6:
                raise ValueError(f"scene.end must equal last word_timing.end (id={scene.id})")

        t = str(scene.type)
        dur = float(scene.end) - float(scene.start)

        if t == "TYPE_4":
            if len(words) not in {1, 2}:
                raise ValueError(f"TYPE_4 must have 1-2 words (id={scene.id})")
            if len(lines) > 1:
                raise ValueError(f"TYPE_4 must stay on one line (id={scene.id})")

        elif t == "TYPE_5":
            if len(words) not in {4, 5}:
                raise ValueError(f"TYPE_5 must have 4-5 words (id={scene.id})")
            if dur <= 3.0:
                raise ValueError(f"TYPE_5 must be >3.0s (id={scene.id}, dur={dur:.3f})")

        elif t == "TYPE_3":
            if len(words) not in {3, 4}:
                raise ValueError(f"TYPE_3 must have 3-4 words (id={scene.id})")
            if len(lines) > 1:
                raise ValueError(f"TYPE_3 must be single-line (id={scene.id})")
            last_len = len(words[-1])
            if last_len < 3 or last_len > 8:
                raise ValueError(f"TYPE_3 last word must be 3..8 chars (id={scene.id})")
            if len(scene.word_timings) >= 2:
                last_gap = float(scene.word_timings[-1].start) - float(scene.word_timings[-2].end)
                if last_gap < 0.25 - 1e-6:
                    raise ValueError(f"TYPE_3 last_gap must be >=0.25s (id={scene.id}, gap={last_gap:.3f})")

    def _lines_for_scene(self, scene: Scene3rdPayloadScene) -> List[str]:
        out: List[str] = []
        if scene.lines:
            for row in scene.lines:
                text = " ".join(str(w).strip() for w in row if str(w).strip())
                if text:
                    out.append(text)
        if not out:
            out = [" ".join(str(w).strip() for w in scene.words if str(w).strip())]
        return out

    def normalize_payload(
        self,
        *,
        payload: BaseModel,
        stage1: Stage1PlanPayload,
        logger: logging.Logger,
    ) -> SubtitleFlowPlan:
        if not isinstance(payload, Scenes3rdPayload):
            payload = Scenes3rdPayload.model_validate(payload.model_dump(mode="json"))

        clip = self._clip_from_stage1(stage1)
        if abs(float(payload.clip.start) - float(clip.start)) > 1e-6:
            raise ValueError("subtitles.clip.start must equal stage1.audio.clip_start_abs")
        if abs(float(payload.clip.end) - float(clip.end)) > 1e-6:
            raise ValueError("subtitles.clip.end must equal stage1.audio.clip_end_abs")

        warnings: List[SubtitleFlowWarning] = []
        segments: List[SubtitleFlowSegment] = []
        for scene in payload.scenes:
            seg_id = f"scene_{int(scene.id):03d}"
            self._maybe_fallback_type3_last_gap_to_type1(
                scene=scene,
                segment_id=seg_id,
                warnings=warnings,
            )
            self._validate_reference_scene_contract(scene)
            if str(scene.type) == "TYPE_4":
                seg_dur = float(scene.end) - float(scene.start)
                if seg_dur < 0.44 - 1e-6:
                    warnings.append(
                        SubtitleFlowWarning(
                            mode=self.mode,
                            segment_id=seg_id,
                            reason="type4_short_duration",
                            action=f"kept dur={seg_dur:.3f}",
                        )
                    )
            seg_in = self._minor_clamp(
                value=float(scene.start),
                low=float(clip.start),
                high=float(clip.end),
                segment_id=seg_id,
                reason="scene_start_out_of_clip",
                warnings=warnings,
            )
            seg_out = self._minor_clamp(
                value=float(scene.end),
                low=float(clip.start),
                high=float(clip.end),
                segment_id=seg_id,
                reason="scene_end_out_of_clip",
                warnings=warnings,
            )
            if seg_out <= seg_in:
                if (seg_in - seg_out) > _MINOR_CLAMP_EPS:
                    raise ValueError(
                        f"invalid scene duration after clamp (segment_id={seg_id}, {seg_in}..{seg_out})"
                    )
                seg_out = seg_in + _MIN_SEGMENT_DUR
                warnings.append(
                    SubtitleFlowWarning(
                        mode=self.mode,
                        segment_id=seg_id,
                        reason="minor_duration_clamp",
                        action=f"extended out_point to {seg_out:.6f}",
                    )
                )

            tokens: List[SubtitleFlowToken] = []
            for wt in scene.word_timings:
                t_start = self._minor_clamp(
                    value=float(wt.start),
                    low=float(clip.start),
                    high=float(clip.end),
                    segment_id=seg_id,
                    reason="token_start_out_of_clip",
                    warnings=warnings,
                )
                t_end = self._minor_clamp(
                    value=float(wt.end),
                    low=float(clip.start),
                    high=float(clip.end),
                    segment_id=seg_id,
                    reason="token_end_out_of_clip",
                    warnings=warnings,
                )
                if t_end <= t_start:
                    raise ValueError(
                        f"token has non-positive duration (segment_id={seg_id}, {wt.word!r}, {t_start}..{t_end})"
                    )
                tokens.append(SubtitleFlowToken(text=str(wt.word), t_start=t_start, t_end=t_end))

            text = " ".join(str(w).strip() for w in scene.words if str(w).strip())
            segments.append(
                SubtitleFlowSegment.model_validate(
                    {
                        "id": seg_id,
                        "text": text,
                        "in_point": seg_in,
                        "out_point": seg_out,
                        "style_tag": str(scene.type),
                        "lines": self._lines_for_scene(scene),
                        "tokens": [t.model_dump(mode="json") for t in tokens],
                        "focus_word": scene.focus_word,
                        "focus_style": scene.focus_style,
                    }
                )
            )

        return self._finalize_flow(
            clip=clip,
            segments=segments,
            warnings=warnings,
            logger=logger,
        )


class SubtitlesPlannerFactory:
    @staticmethod
    def create(mode: str) -> BaseSubtitlesPlanner:
        resolved = normalize_subtitles_mode(mode)
        if resolved == SUBTITLES_MODE_LEGACY_BLOCKS:
            return LegacyBlocksPlanner()
        if resolved == SUBTITLES_MODE_IMPULSE_2ND:
            return Impulse2ndPlanner()
        if resolved == SUBTITLES_MODE_SCENES_3RD:
            return Scenes3rdPlanner()
        raise RuntimeError(f"Unknown subtitles mode: {mode!r}")
