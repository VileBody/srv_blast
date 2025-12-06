from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, ValidationError

from .models import VideoAsset, VideoVariant

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Pydantic-модели под descriptions/*.json
# --------------------------------------------------------------------------- #

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
    """
    Описывает одну запись из descriptions/<prefix>.json.

    Поддерживаем оба варианта:
    {
      "response": {...},       # обычный кейс
      "options": [...]
    }

    и
    {
      "response": [{...}],     # как в 777011741993891093.json
      "options": [...]
    }
    """
    response: Optional[DescriptionResponse | List[DescriptionResponse]] = None
    options: List[DescriptionOption]
    # на всякий случай, если вдруг где-то есть старое поле
    response_raw: Optional[str] = None


# --------------------------------------------------------------------------- #
# AssetLibrary
# --------------------------------------------------------------------------- #

class AssetLibrary:
    """
    Маппинг префикс -> описание и канонический файл.

    Структура на диске:

    pins/
      4503668372533608_01_....mp4
      4503668372533608_02_....mp4
      ...

    descriptions/
      4503668372533608.json
      8725793024858247.json
      ...

    Пример contents файла (см. репу):

    {
      "response": {
        "summary": "...",
        "objects": [...],
        "camera": {...},
        "visuals": {...},
        "composition": "...",
        "tags": [...]
      },
      "options": [
        { "file": "...mp4", "width": 720, "height": 1280 },
        ...
      ]
    }
    """

    def __init__(self, descriptions_dir: Path, pins_dir: Path):
        self.descriptions_dir = descriptions_dir
        self.pins_dir = pins_dir
        self.assets: Dict[str, VideoAsset] = {}

    # ------------------------------------------------------------------ #
    # загрузка
    # ------------------------------------------------------------------ #

    def load_from_files(self) -> None:
        """
        Загружает все *.json из descriptions_dir,
        парсит их через Pydantic и сопоставляет с файлами в pins_dir.

        Внутри нормализуем response так, чтобы в self.assets[*].description
        всегда лежал dict вида:

        {
          "response": { ... },   # ОДИН объект DescriptionResponse
          "options": [ ... ]     # список опций
        }
        """
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

            # --- строим variants из options ---
            variants: List[VideoVariant] = []
            for opt in model.options:
                video_path = self.pins_dir / opt.file
                if not video_path.exists():
                    log.warning(
                        "Video file %s from %s not found in %s",
                        opt.file,
                        path.name,
                        self.pins_dir,
                    )

                variant = VideoVariant(
                    prefix=prefix,
                    path=video_path,
                    width=opt.width,
                    height=opt.height,
                    duration=0.0,  # при необходимости потом допробиваем ffprobe
                )
                variants.append(variant)

            if not variants:
                log.warning("No valid variants built for %s; skipping", path)
                continue

            canonical = variants[0]

            # --- нормализуем response ---
            resp_obj: Optional[DescriptionResponse] = None
            if isinstance(model.response, DescriptionResponse):
                resp_obj = model.response
            elif isinstance(model.response, list) and model.response:
                # кейс, как в 777011741993891093.json
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

    # ------------------------------------------------------------------ #
    # доступ
    # ------------------------------------------------------------------ #

    def get_asset(self, prefix: str) -> VideoAsset:
        return self.assets[prefix]

    def to_prompt_payload(self) -> list[dict]:
        """
        Упрощённое представление библиотеки для Gemini:
        только то, что нужно для подбора визуала.

        На базе нормализованного description:

        {
          "prefix": "4503668372533608",
          "summary": "...",
          "tags": [...],
          "options": [
            {"file": "...mp4", "width": 720, "height": 1280},
            ...
          ]
        }
        """
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
