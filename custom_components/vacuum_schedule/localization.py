"""Shared presentation localization for Vacuum Schedule.

The scheduler domain model stores machine-readable codes only. User-facing text
for the custom panel and notifications is resolved from the same EN/RU/UK
catalogs at the presentation boundary.
"""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Mapping

_DEFAULT_LANGUAGE = "en"
_SUPPORTED_LANGUAGES = {"en", "ru", "uk"}
_CATALOG_DIR = Path(__file__).parent / "frontend" / "localization"


def normalize_language(language: str | None) -> str:
    """Return one supported locale code, falling back to English."""
    raw = str(language or _DEFAULT_LANGUAGE).lower().replace("_", "-")
    if raw.startswith("ru"):
        return "ru"
    if raw.startswith("uk") or raw.startswith("ua"):
        return "uk"
    return _DEFAULT_LANGUAGE


@lru_cache(maxsize=len(_SUPPORTED_LANGUAGES))
def catalog(language: str) -> Mapping[str, str]:
    """Load one immutable-ish flat localization catalog."""
    lang = normalize_language(language)
    path = _CATALOG_DIR / f"{lang}.json"
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid localization catalog: {path}")
    return {str(key): str(value) for key, value in data.items()}


def translate(key: str, language: str | None, /, **params: Any) -> str:
    """Resolve a presentation key and substitute named placeholders.

    Unknown keys intentionally fall back to the English catalog first and then
    to the key itself. This keeps diagnostics usable without leaking a random
    second language into the UI.
    """
    lang = normalize_language(language)
    value = catalog(lang).get(key)
    if value is None and lang != _DEFAULT_LANGUAGE:
        value = catalog(_DEFAULT_LANGUAGE).get(key)
    if value is None:
        value = key
    for name, replacement in params.items():
        value = value.replace("{" + str(name) + "}", str(replacement))
    return value


def translate_code(prefix: str, code: str | None, language: str | None) -> str | None:
    """Translate a machine-readable code under ``prefix`` when known."""
    if code is None:
        return None
    raw = str(code)
    key = f"{prefix}.{raw}"
    value = translate(key, language)
    return raw if value == key else value
