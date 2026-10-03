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

# Превью каталогов (вайбы, фото, стили субтитров, эффекты) — сырые AE-рендеры 1080×1920
# (фото 1920×1440), а карточка на экране ~150–300 px. Короткая сторона ≤540, CRF 29, без звука.
# fps источника не трогаем: в рендерах эффектов бывает 24/25/60, и 30 исказило бы ритм вспышек.
# Ключевой кадр каждые 0,5 с по времени (не по кадрам — fps разный): луп и перемотка дешёвые.
SHORT_SIDE_540 = "scale='if(gt(iw,ih),-2,min(540,iw))':'if(gt(iw,ih),min(540,ih),-2)':flags=bicubic"
PREVIEW_ARGS = [
    "-an",
    "-vf", SHORT_SIDE_540,
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "29", "-pix_fmt", "yuv420p",
    "-force_key_frames", "expr:gte(t,n_forced*0.5)", "-sc_threshold", "0",
    "-movflags", "+faststart",
]
# заставка карточки — с первой секунды: в 0 с AE-рендер стиля часто ещё пустой (текст/эффект не вошёл)
PREVIEW_POSTER_AT = 1.0
# картинки каталога (PNG рамок 1080×1920 и т.п.) — до 720 по ширине; альфа сохраняется (PNG)
IMAGE_MAX_W = 720
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg"}
# превью и картинки адресуются содержимым (оригинал + его ETag) — кэшируются навсегда и всеми
IMMUTABLE_PUBLIC = "public, max-age=31536000, immutable"


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


# ── превью каталогов: адрес = оригинал + его версия (ETag) ───────────────────────

def object_version(etag: str) -> str:
    """ETag объекта S3 → метка версии для имени копии и ссылки.

    Ключи превью каталогов не версионированы (`previews/<plane>/<id>.mp4`, сборщик с --force
    перезаливает на место): без ETag в адресе перезалитый ролик навсегда отдавался бы старой
    копией из кэша браузера (immutable)."""
    clean = "".join(ch for ch in str(etag or "") if ch.isalnum() or ch == "-")
    if not clean:
        raise MediaProxyError("у объекта S3 нет ETag — версию превью не определить")
    return clean[:40]


def preview_name(locator: str, version: str) -> str:
    return proxy_name(f"{locator}#{version}", "preview") + ".mp4"


def preview_poster_name(locator: str, version: str) -> str:
    return preview_name(locator, version) + ".poster.jpg"


def image_ext(locator: str) -> str:
    ext = Path(locator.split("?", 1)[0]).suffix.lower()
    ext = ".jpg" if ext == ".jpeg" else ext
    if ext not in IMAGE_TYPES:
        raise MediaProxyError(f"картинка каталога в неподдерживаемом формате: …{locator[-60:]}")
    return ext


def image_name(locator: str, version: str) -> str:
    return proxy_name(f"{locator}#{version}", "image") + image_ext(locator)


def preview_path(secret: str, locator: str, version: str) -> str:
    """Стабильный адрес превью каталога: та же версия оригинала → тот же адрес → кэш браузера.

    Расширение в адресе обязательно: сайт отличает видео от картинки по нему (isVideoUrl)."""
    token = sign(secret, {"k": "preview", "s": locator, "v": version})
    return f"/api/wizard/media/preview/{token}/clip.mp4"


def image_path(secret: str, locator: str, version: str) -> str:
    token = sign(secret, {"k": "image", "s": locator, "v": version})
    return f"/api/wizard/media/image/{token}/image{image_ext(locator)}"


def unsign_kind(secret: str, token: str, kind: str) -> tuple[str, str]:
    """(оригинал, версия) из ссылки нужного вида; ссылка клипа сюда не подходит."""
    data = unsign(secret, token)
    if data.get("k") != kind or not data.get("s") or not data.get("v"):
        raise MediaProxyError("ссылка на превью другого вида")
    return data["s"], data["v"]


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

# Общий реестр работ на все пулы: один и тот же файл не сжимается дважды, даже если его
# одновременно просят фоновый прогрев и открытый экран. Рядом с работой — пул, в чьей
# очереди она стоит: перехватывать можно только из ЧУЖОЙ (фоновой) очереди.
_JOBS: dict[str, Future] = {}
_OWNER: dict[str, "Builder"] = {}
_JOBS_LOCK = threading.Lock()


class Builder:
    """Пул подготовки копий. Повторный запрос того же файла ждёт уже идущую работу.

    urgent — пул открытого экрана: если тот же файл ещё только стоит в очереди фонового
    прогрева, работа снимается оттуда и запускается здесь сразу, а не ждёт свою очередь.
    Работу из своей же очереди не перехватывает: раньше второй запрос того же клипа (тот же
    клип в превью стола и в сетке «Все ролики», повторный запрос плеера) отменял работу,
    которую ждал первый, — первый получал ошибку (а <video> навсегда оставался на обложке),
    а сама работа уезжала в конец срочной очереди при каждом новом запросе.
    """

    def __init__(self, workers: int = 3, *, urgent: bool = False) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="media-proxy")
        self._urgent = urgent

    def run(self, name: str, fn: Callable[[], None]) -> Future:
        """Запустить подготовку или вернуть уже идущую. Упавшую — запустить заново."""
        with _JOBS_LOCK:
            job = _JOBS.get(name)
            owner = _OWNER.get(name)
            if (job is not None and self._urgent and owner is not None and not owner._urgent
                    and not job.running() and not job.done()):
                job.cancel()  # ещё не начата в фоновой очереди — забираем себе
            if job is None or job.cancelled() or (job.done() and job.exception() is not None):
                job = self._pool.submit(self._guarded, name, fn)
                _JOBS[name] = job
                _OWNER[name] = self
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

def build_clip(store, name: str, fetch: Callable[[Path], None], args: list[str] | None = None) -> None:
    """Скачать оригинал клипа → лёгкая копия → в хранилище (если её ещё нет)."""
    if store.has(name):
        return
    import tempfile
    with tempfile.TemporaryDirectory(prefix="blast-clip-") as tmp:
        src, dst = Path(tmp) / "src", Path(tmp) / "out.mp4"
        fetch(src)
        transcode(src, dst, CLIP_ARGS if args is None else args)
        store.put(name, dst, "video/mp4")


def build_preview(store, name: str, fetch: Callable[[Path], None]) -> None:
    """Превью каталога: свой профиль (fps источника, CRF 29) — см. PREVIEW_ARGS."""
    build_clip(store, name, fetch, PREVIEW_ARGS)


def build_preview_poster(store, clip: str, poster: str, source: str) -> None:
    """Заставка карточки каталога (JPEG ≤540) — видна, пока ролик не в кадре или не играет.

    Готовое превью есть — кадр из него; нет — прямо из оригинала по ссылке (ffmpeg читает
    только нужный кусок), чтобы ряд карточек не ждал сжатия роликов целиком."""
    if store.has(poster):
        return
    import tempfile
    with tempfile.TemporaryDirectory(prefix="blast-pposter-") as tmp:
        dst = Path(tmp) / "poster.jpg"
        if store.has(clip):
            src = Path(tmp) / "clip.mp4"
            src.write_bytes(store.read(clip))
            transcode_frame(str(src), dst, PREVIEW_POSTER_AT, scale=None)
        else:
            transcode_frame(source, dst, PREVIEW_POSTER_AT, scale=SHORT_SIDE_540)
        store.put(poster, dst, "image/jpeg")


def build_image(store, name: str, fetch: Callable[[Path], None]) -> None:
    """Картинка каталога (рамка и т.п.) → ≤720 по ширине, тот же формат (PNG — с альфой)."""
    if store.has(name):
        return
    import tempfile
    ext = Path(name).suffix
    with tempfile.TemporaryDirectory(prefix="blast-image-") as tmp:
        src, dst = Path(tmp) / "src", Path(tmp) / f"out{ext}"
        fetch(src)
        args = ["-frames:v", "1", "-update", "1", "-vf", f"scale='min({IMAGE_MAX_W},iw)':-1:flags=lanczos"]
        if ext == ".jpg":
            args += ["-q:v", "4"]
        transcode(src, dst, args)
        store.put(name, dst, IMAGE_TYPES[ext])


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
NOW = Builder(workers=2, urgent=True)
# миниатюры кадров — своим пулом: полоса из 10–30 кадров не встаёт в очередь перед клипом плеера
POSTERS = Builder(workers=4, urgent=True)


def build_poster(store, clip: str, poster: str, at: float, source: str) -> None:
    """Кадр клипа для миниатюры (JPEG ~10–20 КБ) — вместо <video>, качавшего клип ради кадра.

    Лёгкая копия уже есть — кадр из неё (ключевой кадр каждые 0,5 с, декодировать почти
    нечего). Нет — кадр прямо из оригинала: ffmpeg читает по ссылке только нужный кусок
    файла, а не ждёт, пока клип скачается и сожмётся целиком. Так полоса кадров
    появляется сразу, даже если прогрев до этих клипов ещё не дошёл.
    """
    if store.has(poster):
        return
    import tempfile
    with tempfile.TemporaryDirectory(prefix="blast-poster-") as tmp:
        dst = Path(tmp) / "poster.jpg"
        if store.has(clip):
            src = Path(tmp) / "clip.mp4"
            src.write_bytes(store.read(clip))
            transcode_frame(str(src), dst, at)
        else:
            transcode_frame(source, dst, at)
        store.put(poster, dst, "image/jpeg")


def transcode_frame(src: str, dst: Path, at: float, scale: str | None = "scale=-2:320") -> None:
    """src — путь или ссылка (https): -ss до -i, ffmpeg перематывает по индексу, не читая всё.

    scale=None — кадр в размере источника (заставка превью из уже сжатой копии ≤540)."""
    vf = ["-vf", scale] if scale else []
    proc = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, at):.2f}", "-i", src, "-frames:v", "1",
                           *vf, "-q:v", "6", str(dst)], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        raise MediaProxyError(f"ffmpeg не смог снять кадр: {(proc.stderr or '').strip()[-300:]}")
