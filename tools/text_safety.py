from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_SURROGATE_PATTERN = re.compile(r"[\ud800-\udfff]")


def contains_surrogates(text: str) -> bool:
    return bool(_SURROGATE_PATTERN.search(text))


def sanitize_utf8_text(text: str) -> str:
    if not isinstance(text, str) or not contains_surrogates(text):
        return text
    try:
        # Recover valid surrogate pairs into their real code points and replace
        # orphaned surrogates with U+FFFD so downstream UTF-8 encoding succeeds.
        return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    except Exception:
        return "".join("\ufffd" if 0xD800 <= ord(ch) <= 0xDFFF else ch for ch in text)


def sanitize_for_utf8(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_utf8_text(value)
    if isinstance(value, list):
        return [sanitize_for_utf8(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_for_utf8(item) for item in value)
    if isinstance(value, dict):
        return {
            sanitize_utf8_text(key) if isinstance(key, str) else key: sanitize_for_utf8(item)
            for key, item in value.items()
        }
    return value


def safe_json_dumps(value: Any, **kwargs: Any) -> str:
    return json.dumps(sanitize_for_utf8(value), **kwargs)


def safe_write_text(path: Path, content: str, *, trailing_newline: bool = False) -> Path:
    normalized = sanitize_utf8_text(content)
    if trailing_newline:
        normalized = normalized.rstrip() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized, encoding="utf-8")
    return path
