"""«Примерка» субтитров: ASR отрывка до выбора настроек текста.

Сайт гонит Stage 1a (выравнивание слов по треку) сразу после шага «Трек», пока
человек выбирает фон, и на шаге «Текст» показывает слова на таймлайне под плеером:
подвинуть тайминг, пометить слово фокусным. Правки уезжают в оркестратор поверх той
же asr_preview-джобы (`PUT /jobs/{id}/asr-words`), а рендер приходит с
`reuse_text_job_id=<asr job>` — Stage 1 не считается второй раз, Stage 2 строит
субтитры по уже выправленным словам.

Ключ примерки — трек + окно + текст отрывка: любое из них изменилось → старая
примерка недействительна (оркестратор при reuse сверяет reference-text и окно и
молча пересчитал бы ASR, выбросив правки). Поэтому фронт хранит `key` рядом со
словами, а бэк пересчитывает его сам и не доверяет клиентскому.

Формат слов наружу — camelCase (`tStart`/`tEnd`), как всё остальное API сайта;
в оркестратор — `t_start`/`t_end` (контракт `AsrWordEdit`).
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

TERMINAL = {"COMPLETED", "FAILED"}


def preview_key(audio_s3_url: str, clip_start: float, clip_end: float, target_fragment: str) -> str:
    raw = f"{audio_s3_url}|{float(clip_start):.3f}|{float(clip_end):.3f}|{target_fragment}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def target_fragment(stage_data: dict[str, Any]) -> str:
    """То же правило, что в `production_backend._request_payload`: точный отрывок,
    иначе весь текст. Разойтись нельзя — reuse сверяет reference-text побайтно."""
    fragment = str(stage_data.get("fragment") or "").strip()
    return fragment or str(stage_data.get("lyrics") or "").strip()


_WORD_RE = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)


def mock_words(fragment: str, clip_start: float, clip_end: float) -> list[dict[str, Any]]:
    """Мок-ASR для локальной разработки: слова текста равномерно по окну.

    Строки текста — как в реальном выравнивании — раскладываются по времени с
    небольшой паузой между строками, чтобы на таймлайне была структура, а не
    ровный частокол.
    """
    lines = [ln for ln in (fragment or "").splitlines() if _WORD_RE.search(ln)]
    tokens: list[tuple[str, bool]] = []  # (word, is_line_end)
    for line in lines:
        words = _WORD_RE.findall(line)
        for i, w in enumerate(words):
            tokens.append((w, i == len(words) - 1))
    if not tokens:
        return []
    total = max(0.5, float(clip_end) - float(clip_start))
    # Единица времени: слово = 1, конец строки = +0.6 паузы
    units = sum(1.0 + (0.6 if end else 0.0) for _, end in tokens)
    unit = total / units
    out: list[dict[str, Any]] = []
    cursor = float(clip_start)
    for word, end in tokens:
        t_start = round(cursor, 3)
        t_end = round(min(float(clip_end), cursor + unit * 0.85), 3)
        if t_end <= t_start:
            t_end = round(t_start + 0.05, 3)
        out.append({"text": word, "tStart": t_start, "tEnd": t_end})
        cursor += unit * (1.6 if end else 1.0)
    return out


def words_from_orchestrator(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"text": str(w.get("text") or ""), "tStart": float(w.get("t_start")), "tEnd": float(w.get("t_end"))}
        for w in words
    ]


def words_to_orchestrator(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"text": str(w.get("text") or ""), "t_start": float(w.get("tStart")), "t_end": float(w.get("tEnd"))}
        for w in words
    ]


def focus_words(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Фокус-слова из правок фронта → контракт `FocusWord` оркестратора."""
    return [
        {"text": str(w.get("text") or ""), "t_start": float(w.get("tStart"))}
        for w in words
        if w.get("focus") and str(w.get("text") or "").strip()
    ]


def empty_state(key: str) -> dict[str, Any]:
    return {
        "status": "IDLE",
        "key": key,
        "jobId": None,
        "words": [],
        "clipStart": None,
        "clipEnd": None,
        "error": None,
    }


def stage_data_asr(stage_data: dict[str, Any], *, expected_key: str) -> dict[str, Any] | None:
    """Блок `asr` из stageData сабмита, только если он про ЭТУ примерку.

    Ключ пересчитан на сервере: черновик визарда живёт в localStorage и может
    принести слова от другого трека/окна. Такой блок игнорируется целиком —
    рендер пойдёт со свежим ASR, без правок, но и без чужих таймингов.
    """
    asr = stage_data.get("asr")
    if not isinstance(asr, dict):
        return None
    if str(asr.get("key") or "") != expected_key:
        return None
    if not asr.get("jobId"):
        return None
    words = asr.get("words")
    if not isinstance(words, list):
        return None
    return asr
