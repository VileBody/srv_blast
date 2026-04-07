from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ae_sdk import AeJobResult, AeRenderer, make_job_spec_from_payload

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

AE_JOBS_BASE_DIR = os.getenv("AE_JOBS_BASE_DIR", r"C:\ae_jobs")
AFTERFX_BIN = os.getenv("AFTERFX_BIN")

HOST = os.getenv("AE_NODE_HOST", "0.0.0.0")
PORT = int(os.getenv("AE_NODE_PORT", "8000"))

renderer = AeRenderer(
    base_dir=AE_JOBS_BASE_DIR,
    afterfx_bin=AFTERFX_BIN,
)

app = FastAPI(title="AE Render Node (JSX + S3 refs)", version="0.4.0")


class MediaFilePayload(BaseModel):
    url: str = Field(
        ...,
        description="HTTP/HTTPS URL ИЛИ s3://bucket/key",
    )
    relpath: str = Field(
        ...,
        description="Относительный путь внутри app/, напр. 'media/video/clip1.mp4'",
    )


class CreateJobRequest(BaseModel):
    job_id: Optional[str] = Field(None, description="Опциональный внешний ID джобы")

    # Old inline mode
    render_jsx: Optional[str] = Field(
        None,
        description="Полный текст render.jsx (legacy inline mode)",
    )
    media: List[MediaFilePayload] = Field(
        default_factory=list,
        description="Legacy inline media массив: url + relpath",
    )

    # New S3-ref mode
    render_jsx_s3_uri: Optional[str] = Field(
        None,
        description="s3://... (или http[s]) ссылка на render_full.jsx",
    )
    render_payload_s3_uri: Optional[str] = Field(
        None,
        description="s3://... (или http[s]) ссылка на final_render_instructions_full.json",
    )
    audio_url: Optional[str] = Field(
        None,
        description="Remote URL аудио (http[s]/s3)",
    )

    entry_comp: str = Field(
        "Main Render",
        description="Имя композиции (default, если JSX не вернул другое compName)",
    )
    output_relpath: str = Field(
        "work/output.mp4",
        description="Относительный путь итогового файла внутри app/",
    )

    output_s3_bucket: Optional[str] = Field(None, description="S3 bucket для итогового файла")
    output_s3_key: Optional[str] = Field(None, description="S3 key для итогового файла")


class JobResponse(BaseModel):
    job_id: str
    success: bool
    message: str
    app_dir: str
    output_path: Optional[str]
    output_url: Optional[str]


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/jobs", response_model=JobResponse)
def create_job(req: CreateJobRequest) -> JobResponse:
    payload = req.model_dump()
    job_spec = make_job_spec_from_payload(payload)

    try:
        result: AeJobResult = renderer.run_job(job_spec)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"render error: {e}") from e

    return JobResponse(
        job_id=result.job_id,
        success=result.success,
        message=result.message,
        app_dir=str(result.app_dir),
        output_path=str(result.output_path) if result.output_path else None,
        output_url=result.output_s3_url,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=HOST, port=PORT, reload=False)
