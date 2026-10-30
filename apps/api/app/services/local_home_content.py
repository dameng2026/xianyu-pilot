from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..core.config import settings

logger = logging.getLogger(__name__)

DATA_FILE: str | Path = Path(settings.STORAGE_DIR) / "content_manage.json"
MAX_CONTENT_FILE_BYTES = 1024 * 1024
MAX_ANNOUNCEMENTS = 100


def _bounded_text(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _bounded_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bounded_bool(value: object, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_announcement(source: dict[str, Any], fallback_id: int) -> dict[str, Any]:
    return {
        "id": _bounded_int(source.get("id"), fallback_id),
        "title": _bounded_text(source.get("title") or source.get("name"), 200),
        "content": _bounded_text(
            source.get("content") or source.get("body") or source.get("desc"),
            10_000,
        ),
        "enabled": _bounded_bool(source.get("enabled"), True),
        "createdAt": _bounded_text(source.get("createdAt"), 64),
        "updatedAt": _bounded_text(source.get("updatedAt"), 64),
    }


def _validated_collection(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"content storage {key} must be an array of objects")
    return value


def load_local_home_content() -> dict[str, Any]:
    """Read local announcements without rewriting the source file."""

    path = Path(DATA_FILE)
    try:
        if not path.exists():
            return {"announcements": [], "meta": {}}
        with path.open("rb") as handle:
            raw = handle.read(MAX_CONTENT_FILE_BYTES + 1)
        if not raw or len(raw) > MAX_CONTENT_FILE_BYTES:
            raise ValueError("content storage size is invalid")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("content storage root must be an object")
        raw_announcements = _validated_collection(payload, "announcements")
        meta = payload.get("meta", {})
        if not isinstance(meta, dict):
            raise ValueError("content storage meta must be an object")
        return {
            "announcements": [
                _normalize_announcement(item, index + 1)
                for index, item in enumerate(raw_announcements[:MAX_ANNOUNCEMENTS])
            ],
            "meta": dict(meta),
        }
    except (json.JSONDecodeError, UnicodeError, ValueError, OSError) as exc:
        logger.error(
            "Local home content storage is unavailable errorType=%s",
            type(exc).__name__,
        )
        raise RuntimeError("content storage is corrupt or unavailable") from exc
