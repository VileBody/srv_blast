from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

import requests


@dataclass
class AeMediaPayload:
    url: str
    relpath: str


@dataclass
class AeRenderResponse:
    job_id: str
    success: bool
    message: str
    app_dir: str
    output_path: Optional[str]
    output_url: Optional[str]


class AeRenderClient:
    def __init__(self, base_url: Optional[str] = None, timeout: int = 3600) -> None:
        # AE_NODE_URL типа "http://win-ae-node:8000"
        self.base_url = (base_url or os.getenv("AE_NODE_URL") or "").rstrip("/")
        if not self.base_url:
            raise RuntimeError("AE_NODE_URL is not set; cannot call AE node")
        self.timeout = timeout

    def render(
        self,
        job_id: str,
        render_jsx: str,
        media: List[AeMediaPayload],
        entry_comp: str,
        output_relpath: str,
        output_bucket: Optional[str],
        output_key: Optional[str],
    ) -> AeRenderResponse:
        payload = {
            "job_id": job_id,
            "render_jsx": render_jsx,
            "media": [
                {"url": m.url, "relpath": m.relpath}
                for m in media
            ],
            "entry_comp": entry_comp,
            "output_relpath": output_relpath,
            "output_s3_bucket": output_bucket,
            "output_s3_key": output_key,
        }

        url = f"{self.base_url}/jobs"
        resp = requests.post(url, json=payload, timeout=self.timeout,proxies={"http": None, "https": None},)
        resp.raise_for_status()
        data = resp.json()

        return AeRenderResponse(
            job_id=data["job_id"],
            success=data["success"],
            message=data.get("message", ""),
            app_dir=data["app_dir"],
            output_path=data.get("output_path"),
            output_url=data.get("output_url"),
        )
