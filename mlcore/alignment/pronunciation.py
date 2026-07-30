from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import (
    ERROR_PRONUNCIATION_UNAVAILABLE,
    ERROR_UNSUPPORTED_TEXT,
)
from .core import AlignmentFailure


PRONUNCIATION_MODE = "espeak_en_to_ru"
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]+")
_LATIN_LETTERS = "A-Za-zÀ-ÖØ-öø-ÿ"
_LATIN_OR_DIGIT_RE = re.compile(
    rf"[{_LATIN_LETTERS}]+(?:['’][{_LATIN_LETTERS}]+)*|\d+"
)
_IGNORABLE_WORD_SEPARATORS = frozenset("-_.'’&/+")
_IPA_IGNORABLE = frozenset("ˈˌːˑ͜͡.|‖()[]{}")

# Longest entries must be checked first.
_IPA_TO_RUSSIAN: tuple[tuple[str, str], ...] = (
    ("ŋk", "нк"),
    ("ŋɡ", "нг"),
    ("ŋg", "нг"),
    ("tʃ", "ч"),
    ("dʒ", "дж"),
    ("aɪ", "ай"),
    ("eɪ", "эй"),
    ("oɪ", "ой"),
    ("ɔɪ", "ой"),
    ("aʊ", "ау"),
    ("oʊ", "оу"),
    ("əʊ", "оу"),
    ("ɪə", "иа"),
    ("eə", "эа"),
    ("ʊə", "уа"),
    ("ɑ", "а"),
    ("ɒ", "о"),
    ("æ", "э"),
    ("ʌ", "а"),
    ("ɔ", "о"),
    ("ə", "а"),
    ("ɚ", "ар"),
    ("ɝ", "эр"),
    ("ɜ", "э"),
    ("ɛ", "э"),
    ("e", "э"),
    ("ɪ", "и"),
    ("i", "и"),
    ("ᵻ", "и"),
    ("ʊ", "у"),
    ("u", "у"),
    ("ɐ", "а"),
    ("ɘ", "э"),
    ("ɨ", "ы"),
    ("ɹ", "р"),
    ("r", "р"),
    ("ɾ", "т"),
    ("j", "й"),
    ("w", "у"),
    ("θ", "с"),
    ("ð", "з"),
    ("ʃ", "ш"),
    ("ʒ", "ж"),
    ("ŋ", "нг"),
    ("ɡ", "г"),
    ("g", "г"),
    ("ʔ", ""),
    ("p", "п"),
    ("b", "б"),
    ("t", "т"),
    ("d", "д"),
    ("k", "к"),
    ("f", "ф"),
    ("v", "в"),
    ("s", "с"),
    ("z", "з"),
    ("h", "х"),
    ("m", "м"),
    ("n", "н"),
    ("l", "л"),
    ("x", "х"),
)


@dataclass(frozen=True)
class PronunciationWord:
    display_text: str
    alignment_text: str
    strategy: str
    ipa: str = ""


def _strip_ipa_controls(value: str) -> str:
    output: list[str] = []
    for character in unicodedata.normalize("NFC", str(value or "")):
        category = unicodedata.category(character)
        if character.isspace() or character in _IPA_IGNORABLE:
            continue
        if category in {"Cf", "Mn", "Me"}:
            continue
        output.append(character)
    return "".join(output)


def ipa_to_russian(ipa: str) -> str:
    normalized = _strip_ipa_controls(ipa)
    if not normalized:
        raise AlignmentFailure(
            ERROR_UNSUPPORTED_TEXT,
            "eSpeak returned an empty IPA pronunciation",
        )

    output: list[str] = []
    index = 0
    while index < len(normalized):
        for source, target in _IPA_TO_RUSSIAN:
            if normalized.startswith(source, index):
                output.append(target)
                index += len(source)
                break
        else:
            unknown = normalized[index]
            raise AlignmentFailure(
                ERROR_UNSUPPORTED_TEXT,
                "eSpeak returned an unmapped IPA symbol "
                f"{unknown!r} in pronunciation {ipa!r}",
            )

    pronunciation = "".join(output)
    # /mj u/ and similar sequences are represented more naturally for the
    # Russian grapheme CTC as a soft sign plus vowel.
    pronunciation = re.sub(r"([бвгдзклмнпрстфх])йу", r"\1ью", pronunciation)
    if not pronunciation:
        raise AlignmentFailure(
            ERROR_UNSUPPORTED_TEXT,
            f"IPA pronunciation has no alignable Russian tokens: {ipa!r}",
        )
    return pronunciation


def load_pronunciation_overrides(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AlignmentFailure(
            ERROR_PRONUNCIATION_UNAVAILABLE,
            f"pronunciation override file is missing: {path}",
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise AlignmentFailure(
            ERROR_PRONUNCIATION_UNAVAILABLE,
            f"pronunciation override file is invalid: {type(exc).__name__}: {exc}",
        ) from exc

    if payload.get("schema_version") != 1:
        raise AlignmentFailure(
            ERROR_PRONUNCIATION_UNAVAILABLE,
            "pronunciation override schema_version must be 1",
        )
    raw_overrides = payload.get("english_to_russian")
    if not isinstance(raw_overrides, dict):
        raise AlignmentFailure(
            ERROR_PRONUNCIATION_UNAVAILABLE,
            "english_to_russian pronunciation overrides must be an object",
        )

    overrides: dict[str, str] = {}
    for raw_word, raw_pronunciation in raw_overrides.items():
        word = unicodedata.normalize("NFKC", str(raw_word)).casefold().strip()
        pronunciation = unicodedata.normalize(
            "NFKC",
            str(raw_pronunciation),
        ).casefold().strip()
        if not word or not pronunciation or not _CYRILLIC_RE.fullmatch(pronunciation):
            raise AlignmentFailure(
                ERROR_PRONUNCIATION_UNAVAILABLE,
                f"invalid pronunciation override {raw_word!r}: {raw_pronunciation!r}",
            )
        overrides[word] = pronunciation
    return overrides


class EspeakEnglishToRussianNormalizer:
    def __init__(
        self,
        *,
        espeak_bin: str,
        voice: str,
        timeout_s: float,
        expected_version: str,
        overrides: Mapping[str, str],
        data_path: Path | None = None,
        runner: Any = subprocess.run,
    ):
        if not espeak_bin:
            raise AlignmentFailure(
                ERROR_PRONUNCIATION_UNAVAILABLE,
                "ALIGNMENT_ESPEAK_BIN is empty",
            )
        if not voice:
            raise AlignmentFailure(
                ERROR_PRONUNCIATION_UNAVAILABLE,
                "ALIGNMENT_ESPEAK_VOICE is empty",
            )
        if timeout_s <= 0.0:
            raise AlignmentFailure(
                ERROR_PRONUNCIATION_UNAVAILABLE,
                "ALIGNMENT_ESPEAK_TIMEOUT_S must be positive",
            )
        self.mode = PRONUNCIATION_MODE
        if not expected_version:
            raise AlignmentFailure(
                ERROR_PRONUNCIATION_UNAVAILABLE,
                "ALIGNMENT_ESPEAK_EXPECTED_VERSION is empty",
            )
        self.espeak_bin = str(espeak_bin)
        self.voice = str(voice)
        self.timeout_s = float(timeout_s)
        self.data_path = data_path
        self._overrides = {
            unicodedata.normalize("NFKC", str(word)).casefold(): str(pronunciation)
            for word, pronunciation in overrides.items()
        }
        self._runner = runner
        self._cache: dict[str, tuple[str, str]] = {}
        self.engine_version = self._read_engine_version()
        version_match = re.search(r"\b\d+\.\d+(?:\.\d+)?\b", self.engine_version)
        self.engine_semver = version_match.group(0) if version_match else ""
        if self.engine_semver != str(expected_version):
            raise AlignmentFailure(
                ERROR_PRONUNCIATION_UNAVAILABLE,
                "eSpeak version mismatch: "
                f"expected={expected_version!r} actual={self.engine_semver!r} "
                f"output={self.engine_version!r}",
            )

    def _base_command(self) -> list[str]:
        command = [self.espeak_bin]
        if self.data_path is not None:
            command.append(f"--path={self.data_path}")
        return command

    def _run(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        try:
            completed = self._runner(
                list(command),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=self.timeout_s,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AlignmentFailure(
                ERROR_PRONUNCIATION_UNAVAILABLE,
                f"eSpeak invocation failed: {type(exc).__name__}: {exc}",
            ) from exc
        if completed.returncode != 0:
            error = (completed.stderr or completed.stdout or "").strip()
            raise AlignmentFailure(
                ERROR_PRONUNCIATION_UNAVAILABLE,
                f"eSpeak exited with code={completed.returncode}: {error[-1000:]}",
            )
        return completed

    def _read_engine_version(self) -> str:
        completed = self._run([*self._base_command(), "--version"])
        output = f"{completed.stdout}\n{completed.stderr}".strip()
        first_line = output.splitlines()[0].strip() if output else ""
        if not first_line:
            raise AlignmentFailure(
                ERROR_PRONUNCIATION_UNAVAILABLE,
                "eSpeak --version returned no version",
            )
        return first_line

    def _phonemize_run(self, value: str) -> tuple[str, str]:
        folded_value = "".join(
            character
            for character in unicodedata.normalize("NFKD", value.replace("’", "'"))
            if unicodedata.category(character) != "Mn"
        )
        try:
            folded_value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise AlignmentFailure(
                ERROR_UNSUPPORTED_TEXT,
                f"English word cannot be folded to ASCII: {value!r}",
            ) from exc
        cache_key = folded_value.casefold()
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        completed = self._run(
            [
                *self._base_command(),
                "-q",
                "--ipa=3",
                "-b",
                "1",
                "-v",
                self.voice,
                folded_value,
            ]
        )
        ipa = (completed.stdout or "").strip()
        try:
            pronunciation = ipa_to_russian(ipa)
        except AlignmentFailure as exc:
            raise AlignmentFailure(
                exc.code,
                f"cannot normalize English word {value!r}: {exc.message}",
            ) from exc
        result = (pronunciation, ipa)
        self._cache[cache_key] = result
        return result

    def normalize_word(self, display_word: str) -> PronunciationWord:
        word = unicodedata.normalize("NFKC", str(display_word)).strip()
        override = self._overrides.get(word.casefold())
        if override is not None:
            return PronunciationWord(word, override, "override")

        if _CYRILLIC_RE.fullmatch(word):
            return PronunciationWord(word, word, "literal_cyrillic")

        output: list[str] = []
        ipa_parts: list[str] = []
        strategies: set[str] = set()
        position = 0
        while position < len(word):
            cyrillic_match = _CYRILLIC_RE.match(word, position)
            if cyrillic_match is not None:
                output.append(cyrillic_match.group(0))
                strategies.add("literal_cyrillic")
                position = cyrillic_match.end()
                continue

            latin_match = _LATIN_OR_DIGIT_RE.match(word, position)
            if latin_match is not None:
                pronunciation, ipa = self._phonemize_run(latin_match.group(0))
                output.append(pronunciation)
                ipa_parts.append(ipa)
                strategies.add("espeak_en")
                position = latin_match.end()
                continue

            character = word[position]
            if character in _IGNORABLE_WORD_SEPARATORS:
                position += 1
                continue
            raise AlignmentFailure(
                ERROR_UNSUPPORTED_TEXT,
                f"reference word {word!r} contains unsupported character {character!r}",
            )

        alignment_text = "".join(output).casefold()
        if not alignment_text:
            raise AlignmentFailure(
                ERROR_UNSUPPORTED_TEXT,
                f"reference word {word!r} has no alignable pronunciation",
            )
        strategy = (
            next(iter(strategies))
            if len(strategies) == 1
            else "mixed_cyrillic_espeak"
        )
        return PronunciationWord(
            display_text=word,
            alignment_text=alignment_text,
            strategy=strategy,
            ipa=" ".join(ipa_parts),
        )

    def normalize_words(
        self,
        display_words: Sequence[str],
    ) -> list[PronunciationWord]:
        return [self.normalize_word(word) for word in display_words]

