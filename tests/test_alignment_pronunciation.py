from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mlcore.alignment.contracts import (
    ERROR_PRONUNCIATION_UNAVAILABLE,
    ERROR_UNSUPPORTED_TEXT,
)
from mlcore.alignment.core import AlignmentFailure
from mlcore.alignment.pronunciation import (
    EspeakEnglishToRussianNormalizer,
    ipa_to_russian,
    load_pronunciation_overrides,
)


class _FakeEspeak:
    def __init__(self, pronunciations: dict[str, str]):
        self.pronunciations = pronunciations
        self.phoneme_calls: list[str] = []

    def __call__(self, command, **_kwargs):
        if "--version" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="eSpeak NG text-to-speech: 1.52.0\n",
                stderr="",
            )
        word = command[-1]
        self.phoneme_calls.append(word)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=self.pronunciations[word] + "\n",
            stderr="",
        )


def _normalizer(
    pronunciations: dict[str, str],
    *,
    overrides: dict[str, str] | None = None,
) -> tuple[EspeakEnglishToRussianNormalizer, _FakeEspeak]:
    runner = _FakeEspeak(pronunciations)
    normalizer = EspeakEnglishToRussianNormalizer(
        espeak_bin="espeak-ng",
        voice="en-us",
        expected_version="1.52.0",
        timeout_s=2.0,
        overrides=overrides or {},
        runner=runner,
    )
    return normalizer, runner


@pytest.mark.parametrize(
    ("ipa", "expected"),
    [
        ("pɹˈɪɾi", "прити"),
        ("fˈe\u200dɪs", "фэйс"),
        ("ˈælɪks", "эликс"),
        ("mˈuːvɪŋ", "мувинг"),
        ("mjˈuːzɪk", "мьюзик"),
        ("θˈɪŋk", "синк"),
        ("ðˈɪs", "зис"),
    ],
)
def test_ipa_to_russian_maps_english_phonemes(
    ipa: str,
    expected: str,
) -> None:
    assert ipa_to_russian(ipa) == expected


def test_mixed_text_preserves_display_and_normalizes_latin_words() -> None:
    normalizer, runner = _normalizer(
        {
            "Pretty": "pɹˈɪɾi",
            "face": "fˈe\u200dɪs",
        }
    )

    words = normalizer.normalize_words(["У", "меня", "Pretty", "face"])

    assert [word.display_text for word in words] == [
        "У",
        "меня",
        "Pretty",
        "face",
    ]
    assert [word.alignment_text for word in words] == [
        "У",
        "меня",
        "прити",
        "фэйс",
    ]
    assert [word.strategy for word in words] == [
        "literal_cyrillic",
        "literal_cyrillic",
        "espeak_en",
        "espeak_en",
    ]
    assert runner.phoneme_calls == ["Pretty", "face"]


def test_full_english_text_and_cache_are_supported() -> None:
    normalizer, runner = _normalizer(
        {
            "Hello": "həlˈo\u200dʊ",
            "world": "wˈɜːld",
        }
    )

    first = normalizer.normalize_words(["Hello", "world"])
    second = normalizer.normalize_words(["Hello"])

    assert [word.alignment_text for word in first] == ["халоу", "уэлд"]
    assert second[0].alignment_text == "халоу"
    assert runner.phoneme_calls == ["Hello", "world"]


def test_override_wins_without_invoking_espeak() -> None:
    normalizer, runner = _normalizer({}, overrides={"alyx": "аликс"})

    word = normalizer.normalize_word("ALYX")

    assert word.display_text == "ALYX"
    assert word.alignment_text == "аликс"
    assert word.strategy == "override"
    assert runner.phoneme_calls == []


def test_mixed_script_inside_one_word_is_normalized_by_runs() -> None:
    normalizer, _ = _normalizer({"style": "stˈa\u200dɪl"})

    word = normalizer.normalize_word("рэп-style")

    assert word.alignment_text == "рэпстайл"
    assert word.strategy == "mixed_cyrillic_espeak"


def test_accented_english_and_music_separators_are_supported() -> None:
    normalizer, runner = _normalizer(
        {
            "Beyonce": "bˈe‍ɪɑːns",
            "R": "ˈɑːɹ",
            "B": "bˈiː",
        }
    )

    words = normalizer.normalize_words(["Beyoncé", "R&B"])

    assert [word.alignment_text for word in words] == ["бэйанс", "арби"]
    assert runner.phoneme_calls == ["Beyonce", "R", "B"]

def test_unknown_ipa_symbol_fails_explicitly() -> None:
    normalizer, _ = _normalizer({"word": "pɬ"})

    with pytest.raises(AlignmentFailure) as exc:
        normalizer.normalize_word("word")

    assert exc.value.code == ERROR_UNSUPPORTED_TEXT
    assert "unmapped IPA symbol" in exc.value.message
    assert "word" in exc.value.message


def test_engine_unavailable_fails_readiness_explicitly() -> None:
    def missing_engine(_command, **_kwargs):
        raise FileNotFoundError("espeak-ng")

    with pytest.raises(AlignmentFailure) as exc:
        EspeakEnglishToRussianNormalizer(
            espeak_bin="espeak-ng",
            voice="en-us",
            expected_version="1.52.0",
            timeout_s=2.0,
            overrides={},
            runner=missing_engine,
        )

    assert exc.value.code == ERROR_PRONUNCIATION_UNAVAILABLE


def test_engine_version_mismatch_fails_explicitly() -> None:
    with pytest.raises(AlignmentFailure) as exc:
        EspeakEnglishToRussianNormalizer(
            espeak_bin="espeak-ng",
            voice="en-us",
            expected_version="1.51",
            timeout_s=2.0,
            overrides={},
            runner=_FakeEspeak({}),
        )

    assert exc.value.code == ERROR_PRONUNCIATION_UNAVAILABLE
    assert "version mismatch" in exc.value.message

def test_override_file_is_versioned_and_validated(tmp_path: Path) -> None:
    path = tmp_path / "pronunciations.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "english_to_russian": {"Alyx": "аликс"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert load_pronunciation_overrides(path) == {"alyx": "аликс"}

