"""Лёгкие копии медиа для сайта: превью исходников и трек для прослушки.

Зачем. Превью «Пула» и монтажного стола играло ОРИГИНАЛЫ исходников — те же файлы, что
идут в рендер: 5–40 МБ на клип, 6–16 Мбит/с, индекс `moov` в конце файла (браузер не
начинает играть, пока не докачает хвост). На экране кадр занимает треть, а каждый новый
кадр на склейке качал свой клип заново — плей ждал сеть. Трек сайт качал по нескольку раз
(плеер, волна шага «Трек», «Текста», «Битов» стола, обложки проектов).

Как. Сайт ходит не в S3, а в свою прослойку (`/api/wizard/media/...`). Она по требованию
делает лёгкую копию ffmpeg'ом, кладёт её в S3 рядом с остальными ассетами сайта и дальше
отдаёт уже готовую. Сжимается только то, что человек реально выбрал: подбор клипов сразу
запускает подготовку своих клипов в фоне, к открытию превью они обычно готовы.

- клип: ≤540 по короткой стороне, H.264 CRF 28, ключевой кадр каждые 0,5 с, без звука,
  `+faststart` — играть можно с первых байт и с любого места;
- трек: AAC 96 кбит/с, `+faststart` — в 3–4 раза легче оригинала;
- пики трека: громкость (RMS) каждые 50 мс, один раз на трек — волну больше не нужно
  считать в браузере из целого файла.

Рендер этого не касается: он по-прежнему берёт оригиналы.
"""
from __future__ import annotations

import array
import base64
import hashlib
import hmac
import json
import logging
import math
import subprocess
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Callable

log = logging.getLogger("blast.media_proxy")

VERSION = "v1"
PEAKS_PER_SECOND = 20
PEAK_RATE = 4000  # Гц моно — громкости по 50 мс хватает с запасом

CLIP_ARGS = [
    "-an",
    "-vf", "scale='if(gt(iw,ih),-2,min(540,iw))':'if(gt(iw,ih),min(540,ih),-2)':flags=bicubic,fps=30",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
    "-g", "15", "-keyint_min", "15", "-sc_threshold", "0",
    "-movflags", "+faststart",
]
TRACK_ARGS = ["-vn", "-ac", "2", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart"]


class MediaProxyError(RuntimeError):
    """Лёгкую копию сделать не удалось — причина уходит в ответ, а не прячется."""


# ── подписанные ссылки: прослойка отдаёт только то, что ей выдал сам сайт ─────────

def sign(secret: str, payload: dict[str, str]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).decode().rstrip("=")
    mac = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{mac}"


def unsign(secret: str, token: str) -> dict[str, str]:
    body, _, mac = token.partition(".")
    want = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    if not body or not hmac.compare_digest(mac, want):
        raise MediaProxyError("ссылка на превью повреждена или подделана")
    pad = "=" * (-len(body) % 4)
    data = json.loads(base64.urlsafe_b64decode(body + pad))
    if not isinstance(data, dict):
        raise MediaProxyError("ссылка на превью повреждена")
    return {str(k): str(v) for k, v in data.items()}


def proxy_name(source: str, kind: str) -> str:
    """Имя лёгкой копии: от адреса оригинала — одна копия на файл на весь сайт."""
    digest = hashlib.sha1(f"{VERSION}:{kind}:{source}".encode()).hexdigest()
    return f"{kind}/{digest[:2]}/{digest}"


# ── ffmpeg ─────────────────────────────────────────────────────────────────────

def transcode(src: Path, dst: Path, args: list[str]) -> None:
    proc = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), *args, str(dst)],
                          capture_output=True, text=True, timeout=180)
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        raise MediaProxyError(f"ffmpeg не смог сжать файл: {(proc.stderr or '').strip()[-300:]}")


def peaks(src: Path) -> dict[str, object]:
    """Громкость трека (RMS 0…1) каждые 50 мс и длительность — на волну сайта."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(PEAK_RATE), "-f", "s16le", "-"],
        capture_output=True, timeout=180,
    )
    if proc.returncode != 0:
        raise MediaProxyError(f"ffmpeg не смог прочитать трек: {proc.stderr.decode(errors='ignore').strip()[-300:]}")
    samples = array.array("h")
    samples.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    step = PEAK_RATE // PEAKS_PER_SECOND
    out: list[float] = []
    for i in range(0, len(samples), step):
        chunk = samples[i:i + step]
        if not chunk:
            break
        out.append(round(math.sqrt(sum(x * x for x in chunk) / len(chunk)) / 32768, 4))
    return {"rate": PEAKS_PER_SECOND, "duration": round(len(samples) / PEAK_RATE, 3), "rms": out}


# ── одна подготовка на файл: параллельные запросы ждут ту же работу ──────────────

class Builder:
    """Пул подготовки копий. Повторный запрос того же файла ждёт уже идущую работу."""

    def __init__(self, workers: int = 3) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="media-proxy")
        self._lock = threading.Lock()
        self._jobs: dict[str, Future] = {}

    def run(self, name: str, fn: Callable[[], None]) -> Future:
        """Запустить подготовку или вернуть уже идущую. Упавшую — запустить заново."""
        with self._lock:
            job = self._jobs.get(name)
            if job is None or (job.done() and job.exception() is not None):
                job = self._pool.submit(self._guarded, name, fn)
                self._jobs[name] = job
            return job

    @staticmethod
    def _guarded(name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:
            log.exception("media proxy %s failed", name)
            raise


# фоновый прогрев: подбор клипов сразу ставит свои клипы в очередь
BUILDER = Builder()


# ── где лежат готовые копии: S3 на проде, папка на диске в моке ───────────────────

class S3Store:
    def __init__(self, client, bucket: str, prefix: str) -> None:
        self.client, self.bucket, self.prefix = client, bucket, prefix.strip("/")

    def key(self, name: str) -> str:
        return f"{self.prefix}/web_preview/{VERSION}/{name}"

    def has(self, name: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self.key(name))
            return True
        except Exception as exc:  # 404 — копии ещё нет; остальное пусть всплывёт при записи
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def put(self, name: str, path: Path, content_type: str) -> None:
        with path.open("rb") as fh:
            self.client.put_object(Bucket=self.bucket, Key=self.key(name), Body=fh, ContentType=content_type,
                                   CacheControl="private, max-age=31536000, immutable")

    def read(self, name: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self.key(name))["Body"].read()

    def open(self, name: str, byte_range: str | None):
        """(тело, тип, длина, Content-Range | None) — с поддержкой Range для перемотки."""
        kwargs = {"Bucket": self.bucket, "Key": self.key(name)}
        if byte_range:
            kwargs["Range"] = byte_range
        obj = self.client.get_object(**kwargs)
        return obj["Body"], str(obj.get("ContentType") or "application/octet-stream"), obj.get("ContentLength"), obj.get("ContentRange")


class LocalStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, name: str) -> Path:
        return self.root / VERSION / name

    def has(self, name: str) -> bool:
        return self.path(name).exists()

    def put(self, name: str, path: Path, content_type: str) -> None:
        dst = self.path(name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(path.read_bytes())

    def read(self, name: str) -> bytes:
        return self.path(name).read_bytes()


# ── подготовка копий ─────────────────────────────────────────────────────────────

def build_clip(store, name: str, fetch: Callable[[Path], None]) -> None:
    """Скачать оригинал клипа → лёгкая копия → в хранилище (если её ещё нет)."""
    if store.has(name):
        return
    import tempfile
    with tempfile.TemporaryDirectory(prefix="blast-clip-") as tmp:
        src, dst = Path(tmp) / "src", Path(tmp) / "out.mp4"
        fetch(src)
        transcode(src, dst, CLIP_ARGS)
        store.put(name, dst, "video/mp4")


def build_track(store, name: str, fetch: Callable[[Path], None]) -> None:
    """Трек → AAC-копия для прослушки + пики громкости (один раз на трек)."""
    if store.has(f"{name}.m4a") and store.has(f"{name}.json"):
        return
    import tempfile
    with tempfile.TemporaryDirectory(prefix="blast-track-") as tmp:
        src, dst, pk = Path(tmp) / "src", Path(tmp) / "out.m4a", Path(tmp) / "peaks.json"
        fetch(src)
        transcode(src, dst, TRACK_ARGS)
        pk.write_text(json.dumps(peaks(src), separators=(",", ":")), encoding="utf-8")
        store.put(f"{name}.m4a", dst, "audio/mp4")
        store.put(f"{name}.json", pk, "application/json")


# подготовка, о которой просит открытый экран, идёт впереди фонового прогрева
NOW = Builder(workers=2)


def build_poster(store, clip: str, poster: str, at: float, fetch: Callable[[Path], None]) -> None:
    """Кадр клипа для миниатюры (JPEG ~10–20 КБ) — вместо <video>, качавшего клип ради кадра."""
    if store.has(poster):
        return
    build_clip(store, clip, fetch)
    import tempfile
    with tempfile.TemporaryDirectory(prefix="blast-poster-") as tmp:
        src, dst = Path(tmp) / "clip.mp4", Path(tmp) / "poster.jpg"
        src.write_bytes(store.read(clip))
        transcode_frame(src, dst, at)
        store.put(poster, dst, "image/jpeg")


def transcode_frame(src: Path, dst: Path, at: float) -> None:
    proc = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, at):.2f}", "-i", str(src), "-frames:v", "1",
                           "-vf", "scale=-2:320", "-q:v", "6", str(dst)], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        raise MediaProxyError(f"ffmpeg не смог снять кадр: {(proc.stderr or '').strip()[-300:]}")
