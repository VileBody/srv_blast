"""Subject-aware framing for the horizontal 4:3 photo flow.

The expensive object detection happens once while the photo base is tagged or
backfilled.  Render jobs consume only the small normalized ``framing`` object:

    {
      "version": "photo-framing-v1",
      "strategy": "yolox",
      "subject_class": "car",
      "subject_bbox": [x1, y1, x2, y2],
      "focus_x": 0.51,
      "focus_y": 0.72,
      "confidence": 0.91
    }

All coordinates are normalized to 0..1.  ``focus_x`` / ``focus_y`` are already
clamped to the range in which a 4:3 cover crop cannot expose empty canvas.

The detector implementation is adapted to the output contract of OpenCV Zoo's
YOLOX-S model.  OpenCV Zoo and all files in its YOLOX directory are Apache-2.0:
https://github.com/opencv/opencv_zoo/tree/main/models/object_detection_yolox
"""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


FRAMING_VERSION = "photo-framing-v1"
DEFAULT_MODEL_PATH = Path("data/models/object_detection_yolox_2022nov.onnx")
DEFAULT_COMP_W = 1920
DEFAULT_COMP_H = 1440

COCO_CLASSES: Tuple[str, ...] = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag",
    "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard",
    "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon",
    "bowl", "banana", "apple", "sandwich", "orange", "broccoli", "carrot",
    "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
    "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
)

VEHICLE_CLASSES = {"bicycle", "car", "motorcycle", "bus", "train", "truck", "boat"}
ANIMAL_CLASSES = {
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear",
    "zebra", "giraffe",
}


def _clamp01(value: Any, default: float = 0.5) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        out = default
    if not math.isfinite(out):
        out = default
    return max(0.0, min(1.0, out))


def normalize_framing(value: Any) -> Dict[str, Any]:
    """Validate and compact framing read from DB/snapshot/payload."""
    if not isinstance(value, Mapping):
        return {}
    bbox_raw = value.get("subject_bbox")
    bbox: List[float] = []
    if isinstance(bbox_raw, Sequence) and not isinstance(bbox_raw, (str, bytes)) and len(bbox_raw) == 4:
        bbox = [_clamp01(x, 0.0) for x in bbox_raw]
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            bbox = []
    out: Dict[str, Any] = {
        "version": str(value.get("version") or FRAMING_VERSION),
        "strategy": str(value.get("strategy") or "center"),
        "subject_class": str(value.get("subject_class") or ""),
        "focus_x": _clamp01(value.get("focus_x")),
        "focus_y": _clamp01(value.get("focus_y")),
        "confidence": _clamp01(value.get("confidence"), 0.0),
    }
    if bbox:
        out["subject_bbox"] = bbox
    quality_raw = value.get("quality")
    if isinstance(quality_raw, Mapping):
        from mlcore.photo_quality import normalize_photo_quality

        quality = normalize_photo_quality(quality_raw)
        if quality:
            out["quality"] = quality
    return out


def cover_visible_fraction(
    *,
    src_w: int,
    src_h: int,
    comp_w: int = DEFAULT_COMP_W,
    comp_h: int = DEFAULT_COMP_H,
) -> Tuple[float, float]:
    """Width/height of the source visible after an exact cover fit, in 0..1."""
    if min(src_w, src_h, comp_w, comp_h) <= 0:
        raise ValueError("source and comp dimensions must be positive")
    scale = max(float(comp_w) / float(src_w), float(comp_h) / float(src_h))
    return (
        min(1.0, float(comp_w) / (scale * float(src_w))),
        min(1.0, float(comp_h) / (scale * float(src_h))),
    )


def clamp_focus_for_cover(
    focus_x: float,
    focus_y: float,
    *,
    src_w: int,
    src_h: int,
    comp_w: int = DEFAULT_COMP_W,
    comp_h: int = DEFAULT_COMP_H,
) -> Tuple[float, float]:
    """Clamp a source-space focus so a cover crop never reveals empty canvas."""
    visible_w, visible_h = cover_visible_fraction(
        src_w=src_w, src_h=src_h, comp_w=comp_w, comp_h=comp_h
    )
    half_w, half_h = visible_w / 2.0, visible_h / 2.0
    return (
        max(half_w, min(1.0 - half_w, _clamp01(focus_x))),
        max(half_h, min(1.0 - half_h, _clamp01(focus_y))),
    )


def framing_from_bbox(
    bbox: Sequence[float],
    *,
    src_w: int,
    src_h: int,
    subject_class: str,
    confidence: float,
    strategy: str = "yolox",
    comp_w: int = DEFAULT_COMP_W,
    comp_h: int = DEFAULT_COMP_H,
) -> Dict[str, Any]:
    """Build safe cover framing from a normalized subject bounding box."""
    if len(bbox) != 4:
        raise ValueError("bbox must contain x1,y1,x2,y2")
    x1, y1, x2, y2 = [_clamp01(x, 0.0) for x in bbox]
    if x2 <= x1 or y2 <= y1:
        raise ValueError("bbox must have positive area")

    # A little context prevents faces/cars from touching the 4:3 frame edges.
    pad_x = min(0.08, (x2 - x1) * 0.16)
    pad_y = min(0.08, (y2 - y1) * 0.12)
    x1p, y1p = max(0.0, x1 - pad_x), max(0.0, y1 - pad_y)
    x2p, y2p = min(1.0, x2 + pad_x), min(1.0, y2 + pad_y)
    focus_x = (x1p + x2p) / 2.0
    focus_y = (y1p + y2p) / 2.0

    # Human detections often include the whole body.  A slight upward bias keeps
    # the face/torso visible without forcing every portrait to exact centre.
    if subject_class == "person":
        focus_y -= min(0.08, (y2p - y1p) * 0.10)
    focus_x, focus_y = clamp_focus_for_cover(
        focus_x,
        focus_y,
        src_w=src_w,
        src_h=src_h,
        comp_w=comp_w,
        comp_h=comp_h,
    )
    return normalize_framing(
        {
            "version": FRAMING_VERSION,
            "strategy": strategy,
            "subject_class": subject_class,
            "subject_bbox": [x1, y1, x2, y2],
            "focus_x": focus_x,
            "focus_y": focus_y,
            "confidence": confidence,
        }
    )


def center_framing(
    *,
    src_w: int,
    src_h: int,
    comp_w: int = DEFAULT_COMP_W,
    comp_h: int = DEFAULT_COMP_H,
) -> Dict[str, Any]:
    focus_x, focus_y = clamp_focus_for_cover(
        0.5, 0.5, src_w=src_w, src_h=src_h, comp_w=comp_w, comp_h=comp_h
    )
    return normalize_framing(
        {
            "version": FRAMING_VERSION,
            "strategy": "center",
            "focus_x": focus_x,
            "focus_y": focus_y,
            "confidence": 0.0,
        }
    )


def _norm_tags(tags: Iterable[Any]) -> set[str]:
    return {" ".join(str(x or "").strip().lower().split()) for x in tags or [] if str(x or "").strip()}


def _preferred_classes(*, theme_tags: Iterable[Any], people_type: Any) -> set[str]:
    tags = _norm_tags(theme_tags)
    text = " ".join(sorted(tags))
    people = str(people_type or "").strip().lower()
    # Explicit human metadata wins over incidental road/vehicle detections.
    # ``driver`` is the exception: a real car theme should frame the vehicle.
    if people not in {"", "none", "driver"}:
        return {"person"}
    if any(word in text for word in ("car", "vehicle", "night drive", "traffic", "motorcycle", "moving car", "машин")):
        return set(VEHICLE_CLASSES)
    if people == "driver" or any(word in text for word in ("person", "people", "portrait", "silhouette", "girl", "guy", "couple", "crowd")):
        return {"person"}
    if any(word in text for word in ("animal", "bird", "cat", "dog", "horse")):
        return set(ANIMAL_CLASSES)
    return set()


def choose_subject_detection(
    detections: Iterable[Mapping[str, Any]],
    *,
    theme_tags: Iterable[Any] = (),
    people_type: Any = "none",
) -> Optional[Dict[str, Any]]:
    """Choose one semantic subject, preferring classes implied by photo tags."""
    rows: List[Dict[str, Any]] = []
    preferred = _preferred_classes(theme_tags=theme_tags, people_type=people_type)
    if not preferred:
        return None
    for raw in detections or []:
        label = str(raw.get("class") or "")
        bbox = raw.get("bbox")
        if not label or not isinstance(bbox, Sequence) or len(bbox) != 4:
            continue
        try:
            x1, y1, x2, y2 = [float(x) for x in bbox]
            confidence = float(raw.get("confidence") or 0.0)
        except (TypeError, ValueError):
            continue
        area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        centrality = max(0.0, 1.0 - math.hypot(cx - 0.5, cy - 0.5))
        preferred_bonus = 2.0 if label in preferred else 0.0
        # Confidence dominates; area and centrality break ties between several
        # people/cars while the semantic tag bonus wins over incidental objects.
        score = preferred_bonus + confidence * 1.5 + min(area, 0.6) + centrality * 0.15
        rows.append({**dict(raw), "_score": score})
    if not rows:
        return None
    if preferred:
        preferred_rows = [row for row in rows if str(row.get("class") or "") in preferred]
        if not preferred_rows:
            return None
        rows = preferred_rows
    rows.sort(key=lambda x: float(x["_score"]), reverse=True)
    return rows[0]


class OpenCvYoloXDetector:
    """Small CPU detector backed by OpenCV DNN and OpenCV Zoo YOLOX-S."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        confidence_threshold: float = 0.32,
        nms_threshold: float = 0.5,
    ) -> None:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "photo framing requires opencv-python-headless>=4.10 and numpy"
            ) from exc
        self.cv2 = cv2
        self.np = np
        self.model_path = Path(model_path)
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"photo framing model missing: {self.model_path}. "
                "Run scripts/download_photo_framing_model.py or set PHOTO_FRAMING_MODEL_PATH."
            )
        self.net = cv2.dnn.readNet(str(self.model_path))
        self.input_size = (640, 640)
        self.strides = (8, 16, 32)
        self.confidence_threshold = float(confidence_threshold)
        self.nms_threshold = float(nms_threshold)
        self._generate_grids()

    def _generate_grids(self) -> None:
        grids, expanded = [], []
        for stride in self.strides:
            hsize = self.input_size[0] // stride
            wsize = self.input_size[1] // stride
            xv, yv = self.np.meshgrid(self.np.arange(wsize), self.np.arange(hsize))
            grid = self.np.stack((xv, yv), axis=2).reshape(1, -1, 2)
            grids.append(grid)
            expanded.append(self.np.full((*grid.shape[:2], 1), stride))
        self.grids = self.np.concatenate(grids, axis=1)
        self.expanded_strides = self.np.concatenate(expanded, axis=1)

    def detect(self, path: str | Path) -> Tuple[int, int, List[Dict[str, Any]]]:
        image = self.cv2.imread(str(path))
        if image is None:
            raise RuntimeError(f"OpenCV could not read photo: {path}")
        src_h, src_w = image.shape[:2]
        rgb = self.cv2.cvtColor(image, self.cv2.COLOR_BGR2RGB)
        padded = self.np.ones((640, 640, 3), dtype=self.np.float32) * 114.0
        ratio = min(640.0 / src_h, 640.0 / src_w)
        rw, rh = int(src_w * ratio), int(src_h * ratio)
        padded[:rh, :rw] = self.cv2.resize(rgb, (rw, rh)).astype(self.np.float32)
        blob = self.np.transpose(padded, (2, 0, 1))[self.np.newaxis, :, :, :]
        self.net.setInput(blob)
        outputs = self.net.forward(self.net.getUnconnectedOutLayersNames())[0][0]
        outputs[:, :2] = (outputs[:, :2] + self.grids[0]) * self.expanded_strides[0]
        outputs[:, 2:4] = self.np.exp(outputs[:, 2:4]) * self.expanded_strides[0]
        boxes = self.np.ones_like(outputs[:, :4])
        boxes[:, 0] = outputs[:, 0] - outputs[:, 2] / 2.0
        boxes[:, 1] = outputs[:, 1] - outputs[:, 3] / 2.0
        boxes[:, 2] = outputs[:, 2]
        boxes[:, 3] = outputs[:, 3]
        scores_all = outputs[:, 4:5] * outputs[:, 5:]
        scores = self.np.amax(scores_all, axis=1)
        class_ids = self.np.argmax(scores_all, axis=1)
        keep = self.cv2.dnn.NMSBoxesBatched(
            boxes.tolist(),
            scores.tolist(),
            class_ids.tolist(),
            self.confidence_threshold,
            self.nms_threshold,
        )
        detections: List[Dict[str, Any]] = []
        for idx in (list(keep) if len(keep) else []):
            idx = int(idx)
            x, y, w, h = [float(v) / ratio for v in boxes[idx]]
            x1, y1 = max(0.0, x), max(0.0, y)
            x2, y2 = min(float(src_w), x + w), min(float(src_h), y + h)
            if x2 <= x1 or y2 <= y1:
                continue
            cls_id = int(class_ids[idx])
            detections.append(
                {
                    "class": COCO_CLASSES[cls_id] if 0 <= cls_id < len(COCO_CLASSES) else str(cls_id),
                    "confidence": float(scores[idx]),
                    "bbox": [x1 / src_w, y1 / src_h, x2 / src_w, y2 / src_h],
                }
            )
        return src_w, src_h, detections


def analyze_photo_framing(
    path: str | Path,
    *,
    theme_tags: Iterable[Any] = (),
    people_type: Any = "none",
    detector: Optional[OpenCvYoloXDetector] = None,
    model_path: str | Path | None = None,
) -> Dict[str, Any]:
    """Detect the main subject and return render-ready normalized framing."""
    if detector is None:
        configured = model_path or os.environ.get("PHOTO_FRAMING_MODEL_PATH") or DEFAULT_MODEL_PATH
        detector = OpenCvYoloXDetector(configured)
    src_w, src_h, detections = detector.detect(path)
    subject = choose_subject_detection(
        detections, theme_tags=theme_tags, people_type=people_type
    )
    if not subject:
        return center_framing(src_w=src_w, src_h=src_h)
    return framing_from_bbox(
        subject["bbox"],
        src_w=src_w,
        src_h=src_h,
        subject_class=str(subject["class"]),
        confidence=float(subject["confidence"]),
    )
