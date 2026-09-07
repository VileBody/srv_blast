from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANDING = ROOT / "landing"


class VisibleRussianParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.skip_depth = 0
        self.values: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skip_depth += 1
        for name, value in attrs:
            if name in {"aria-label", "title", "alt", "placeholder"} and value and re.search(r"[А-Яа-яЁё]", value):
                self.values.add(value.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if not self.skip_depth and value and re.search(r"[А-Яа-яЁё]", value):
            self.values.add(value)


def test_all_visible_russian_landing_strings_are_in_central_dictionary() -> None:
    parser = VisibleRussianParser()
    parser.feed((LANDING / "index.html").read_text(encoding="utf-8"))
    dictionary = (LANDING / "js" / "i18n.js").read_text(encoding="utf-8")
    missing = sorted(value for value in parser.values if value not in dictionary)
    assert not missing, "Missing central translations: " + repr(missing)


def test_public_legal_pages_use_shared_bilingual_renderer() -> None:
    expected = {
        "privacy.html": "privacy",
        "terms.html": "terms",
        "cookies.html": "cookies",
        "personal-data-consent.html": "consent",
        "offer.html": "offer",
        "contacts.html": "contacts",
    }
    for filename, key in expected.items():
        source = (LANDING / filename).read_text(encoding="utf-8")
        assert f'data-legal-document="{key}"' in source
        assert 'js/i18n.js' in source
        assert 'js/legal-documents.js' in source


def test_home_loads_i18n_and_consent_before_main() -> None:
    source = (LANDING / "index.html").read_text(encoding="utf-8")
    assert source.index("js/i18n.js") < source.index("js/main.js")
    assert source.index("js/cookie-consent.js") < source.index("js/main.js")
    for filename in ("privacy.html", "terms.html", "cookies.html", "personal-data-consent.html", "offer.html", "contacts.html"):
        assert f'href="{filename}"' in source


def test_optional_scripts_require_explicit_consent_category() -> None:
    script_pattern = re.compile(r"<script\b[^>]*data-consent-category[^>]*>", re.IGNORECASE)
    for html_file in LANDING.glob("*.html"):
        for tag in script_pattern.findall(html_file.read_text(encoding="utf-8")):
            assert re.search(r'type=["\']text/plain["\']', tag, re.IGNORECASE), f"Ungated optional script in {html_file.name}: {tag}"


def test_hero_badge_text_never_wraps() -> None:
    styles = (LANDING / "css" / "style.css").read_text(encoding="utf-8")
    rule = re.search(r"\.hero-badge span\s*\{(?P<body>[^}]*)\}", styles)
    assert rule is not None
    assert re.search(r"white-space\s*:\s*nowrap\s*;", rule.group("body"))


def test_landing_legal_texts_stay_in_sync_with_web_app_documents() -> None:
    """Лендинг и веб-приложение публикуют одни и те же юридические факты.

    Расхождение реквизитов или даты редакции между blast808.com и
    app.blast808.com — прямой повод для отказа модерации Google/TikTok,
    поэтому дрейф ловим тестом, а не глазами.
    """
    landing = (LANDING / "js" / "legal-documents.js").read_text(encoding="utf-8")
    web_app = (ROOT / "web_app" / "frontend" / "src" / "data" / "legal-docs.ts").read_text(encoding="utf-8")

    for requisite in ("623013205426", "324620000005644", "support@blast808.com", "Чернов Никита Романович"):
        assert requisite in landing, f"landing legal docs lost {requisite}"
        assert requisite in web_app, f"web app legal docs lost {requisite}"

    landing_effective = re.search(r"const EFFECTIVE_RU = '(\d+) (\S+) (\d{4})", landing)
    web_app_effective = re.search(r"LEGAL_UPDATED = '(\d{4})-(\d{2})-(\d{2})'", web_app)
    assert landing_effective and web_app_effective
    months = {
        "января": "01", "февраля": "02", "марта": "03", "апреля": "04",
        "мая": "05", "июня": "06", "июля": "07", "августа": "08",
        "сентября": "09", "октября": "10", "ноября": "11", "декабря": "12",
    }
    day, month_ru, year = landing_effective.groups()
    assert (year, months[month_ru], f"{int(day):02d}") == web_app_effective.groups(), (
        "Даты редакции разошлись: подними их одновременно в обоих наборах документов"
    )
