from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, ValidationError

from src.core.models import VideoAsset, VideoVariant
from .s3 import download_from_s3

log = logging.getLogger(__name__)


class DescriptionOption(BaseModel):
    file: str
    width: int
    height: int


class DescriptionResponse(BaseModel):
    summary: str = ""
    objects: List[str] = []
    camera: dict = {}
    visuals: dict = {}
    composition: str = ""
    tags: List[str] = []


class DescriptionFile(BaseModel):
    response: Optional[DescriptionResponse | List[DescriptionResponse]] = None
    options: List[DescriptionOption]
    response_raw: Optional[str] = None


class AssetLibrary:
    """
    descriptions/ живут локально (репозиторий),
    pins/ мы используем как локальный кеш для S3.

    Пины берём из S3_BUCKET_ASSET_STORAGE:
      - ключ = opt.file (никаких подпапок).
      - если файл уже есть в pins_dir — повторно не скачиваем.
    """

    def __init__(self, descriptions_dir: Path, pins_dir: Path):
        self.descriptions_dir = descriptions_dir
        self.pins_dir = pins_dir
        self.assets: Dict[str, VideoAsset] = {}

        self.s3_bucket_assets: str | None = os.getenv("S3_BUCKET_ASSET_STORAGE") or None
        if self.s3_bucket_assets:
            log.info(
                "AssetLibrary will use S3 bucket %s as fallback for pins (local is cache)",
                self.s3_bucket_assets,
            )
        else:
            log.warning(
                "S3_BUCKET_ASSET_STORAGE is not set; AssetLibrary will not be able to fetch pins from S3"
            )

    def load_from_files(self) -> None:
        self.assets.clear()
        if not self.descriptions_dir.exists():
            log.warning(
                "Descriptions dir %s does not exist; library will be empty",
                self.descriptions_dir,
            )
            return

        json_files: List[Path] = sorted(self.descriptions_dir.glob("*.json"))
        log.info(
            "Loading %d description files from %s",
            len(json_files),
            self.descriptions_dir,
        )

        for path in json_files:
            prefix = path.stem
            try:
                raw = path.read_text(encoding="utf-8")
            except Exception as e:
                log.error("Failed to read %s: %s", path, e)
                continue

            try:
                model = DescriptionFile.model_validate_json(raw)
            except ValidationError as e:
                log.error("Pydantic validation error in %s: %s", path, e)
                continue

            variants: List[VideoVariant] = []

            for opt in model.options:
                video_path = self.pins_dir / opt.file
                video_path.parent.mkdir(parents=True, exist_ok=True)

                if not video_path.exists() and self.s3_bucket_assets:
                    try:
                        download_from_s3(self.s3_bucket_assets, opt.file, video_path)
                    except Exception:
                        log.warning(
                            "Failed to fetch %s from S3 bucket %s; asset may be unusable",
                            opt.file,
                            self.s3_bucket_assets,
                        )

                if not video_path.exists():
                    log.warning(
                        "Video file %s from %s not found locally (pins_dir=%s) "
                        "and no S3 bucket or download failed",
                        opt.file,
                        path.name,
                        self.pins_dir,
                    )

                variant = VideoVariant(
                    prefix=prefix,
                    path=video_path,
                    width=opt.width,
                    height=opt.height,
                    duration=0.0,
                )
                variants.append(variant)

            if not variants:
                log.warning("No valid variants built for %s; skipping asset", path)
                continue

            canonical = variants[0]

            resp_obj: Optional[DescriptionResponse] = None
            if isinstance(model.response, DescriptionResponse):
                resp_obj = model.response
            elif isinstance(model.response, list) and model.response:
                resp_obj = model.response[0]
                log.warning(
                    "Description %s: 'response' is a list, using first element",
                    path.name,
                )
            else:
                if model.response_raw:
                    log.warning(
                        "Description %s: no structured 'response', only 'response_raw'; "
                        "summary/tags will be empty",
                        path.name,
                    )

            normalized_desc = {
                "response": resp_obj.model_dump() if resp_obj else {},
                "options": [opt.model_dump() for opt in model.options],
            }

            asset = VideoAsset(
                prefix=prefix,
                canonical=canonical,
                variants=variants,
                description=normalized_desc,
            )
            self.assets[prefix] = asset

        log.info("Loaded %d assets into library", len(self.assets))

    def get_asset(self, prefix: str) -> VideoAsset:
        return self.assets[prefix]

    def to_prompt_payload(self) -> list[dict]:
        payload: list[dict] = []

        for a in self.assets.values():
            desc = a.description or {}
            response = desc.get("response") or {}
            if not isinstance(response, dict):
                log.warning(
                    "Asset %s: unexpected 'response' type (%s), treating as empty",
                    a.prefix,
                    type(response),
                )
                response = {}

            summary = str(response.get("summary", "") or "")

            tags_val = response.get("tags", [])
            if isinstance(tags_val, list):
                tags = tags_val
            elif tags_val:
                tags = [str(tags_val)]
            else:
                tags = []

            options = []
            for v in a.variants:
                options.append(
                    {
                        "file": v.path.name,
                        "width": v.width,
                        "height": v.height,
                    }
                )

            payload.append(
                {
                    "prefix": a.prefix,
                    "summary": summary,
                    "tags": tags,
                    "options": options,
                }
            )

        log.info("Built prompt payload for %d assets", len(payload))
        return payload
