from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class VideoVariant:
    prefix: str
    path: Path
    width: int
    height: int
    duration: float

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height

    def to_option_dict(self) -> Dict[str, Any]:
        return {
            "file": self.path.name,
            "width": self.width,
            "height": self.height,
        }


@dataclass
class VideoAsset:
    """Одна логическая сцена = группа файлов с одинаковым префиксом."""

    prefix: str
    canonical: VideoVariant
    variants: List[VideoVariant] = field(default_factory=list)
    description: Dict[str, Any] | None = None

    def to_json_dict(self) -> Dict[str, Any]:
        return {
            "prefix": self.prefix,
            "canonical_file": self.canonical.path.name,
            "description": self.description,
            "options": [v.to_option_dict() for v in self.variants],
        }


@dataclass
class AudioSegmentPlan:
    index: int
    start: float
    end: float
    mood: str
    description: str

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class VisualShotSpec:
    """Один визуальный кусочек под отрезок аудио."""

    asset_prefix: str
    target_duration: float


@dataclass
class SegmentEditPlan:
    audio_segment: AudioSegmentPlan
    shots: List[VisualShotSpec] = field(default_factory=list)


@dataclass
class EditProject:
    audio_path: Path
    segments: List[SegmentEditPlan]
    # вместо одного большого файла — список финальных роликов по сегментам
    final_videos: List[Path] = field(default_factory=list)
