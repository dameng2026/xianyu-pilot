"""Update info service: probe GitHub releases for manual native updates.

Caches GitHub API responses in-process for 6 hours to stay under the
unauthenticated rate limit (60 requests/hour per IP).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
GITHUB_TIMEOUT_SECONDS = 8.0
CACHE_TTL_SECONDS = 6 * 3600  # 6 hours
RELEASE_NOTES_MAX_CHARS = 2000



@dataclass
class CachedRelease:
    """In-memory cache entry for the latest GitHub release."""

    tag: str
    published_at: str
    body: str
    html_url: str
    tarball_url: str
    fetched_at: float


_cache: Optional[CachedRelease] = None


def _normalize_version(tag: str) -> str:
    """Strip a leading 'v' from a release tag: 'v1.2.0' -> '1.2.0'."""
    return tag[1:] if tag and tag.startswith("v") else (tag or "")


def _compare_versions(current: str, latest: str) -> bool:
    """Return True if latest is strictly newer than current.

    Uses simple tuple comparison on numeric components; non-numeric parts
    are ignored. Returns False if either side is empty.
    """
    if not current or not latest:
        return False
    if current == latest:
        return False

    def parse(value: str) -> tuple[int, ...]:
        parts: list[int] = []
        for chunk in value.split("."):
            digits = "".join(ch for ch in chunk if ch.isdigit())
            parts.append(int(digits) if digits else 0)
        return tuple(parts)

    current_tuple = parse(current)
    latest_tuple = parse(latest)
    # Pad to same length for comparison.
    length = max(len(current_tuple), len(latest_tuple))
    current_tuple = current_tuple + (0,) * (length - len(current_tuple))
    latest_tuple = latest_tuple + (0,) * (length - len(latest_tuple))
    return latest_tuple > current_tuple


def detect_deployment_mode() -> str:
    return "desktop" if settings.is_desktop else "source"


async def _fetch_latest_release(repo: str) -> Optional[dict[str, Any]]:
    """Call GitHub releases/latest. Returns None on any failure."""
    url = f"{GITHUB_API_BASE}/repos/{repo}/releases/latest"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "xianyu-assistant-opensource/update-checker",
    }
    try:
        async with httpx.AsyncClient(timeout=GITHUB_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=headers)
        if response.status_code != 200:
            logger.warning(
                "GitHub releases/latest returned status=%s repo=%s",
                response.status_code,
                repo,
            )
            return None
        return response.json()
    except (httpx.HTTPError, ValueError, OSError) as exc:
        logger.warning("GitHub releases/latest fetch failed errorType=%s", type(exc).__name__)
        return None


async def _get_cached_release() -> tuple[Optional[CachedRelease], str]:
    """Return (release, source) where source is 'live' | 'cached' | 'unavailable'."""
    global _cache
    now = time.time()

    cache_fresh = _cache is not None and (now - _cache.fetched_at) < CACHE_TTL_SECONDS
    if cache_fresh:
        return _cache, "cached"

    repo = settings.github_repo or ""
    if not repo or "/" not in repo:
        return _cache, "unavailable"

    payload = await _fetch_latest_release(repo)
    if payload is None:
        # Fall back to stale cache even if expired; better than nothing.
        return _cache, "cached" if _cache else "unavailable"

    tag = str(payload.get("tag_name") or "")
    body = str(payload.get("body") or "")
    if len(body) > RELEASE_NOTES_MAX_CHARS:
        body = body[:RELEASE_NOTES_MAX_CHARS] + "..."
    release = CachedRelease(
        tag=tag,
        published_at=str(payload.get("published_at") or ""),
        body=body,
        html_url=str(payload.get("html_url") or ""),
        tarball_url=str(payload.get("tarball_url") or ""),
        fetched_at=now,
    )
    _cache = release
    return release, "live"


def _build_offline_backup(release: Optional[CachedRelease], repo: str) -> dict[str, str]:
    if release is not None:
        tarball = release.tarball_url
        html_url = release.html_url
    else:
        tarball = f"https://github.com/{repo}/archive/refs/heads/main.tar.gz"
        html_url = f"https://github.com/{repo}/releases/latest"
    return {"tarballUrl": tarball, "releaseUrl": html_url}


def _format_cached_at(release: Optional[CachedRelease]) -> str:
    if release is None:
        return ""
    try:
        dt = datetime.fromtimestamp(release.fetched_at, tz=timezone.utc)
        return dt.isoformat()
    except (ValueError, OSError):
        return ""


async def build_update_info(current_version: str) -> dict[str, Any]:
    """Assemble the full /system/update-info response payload."""
    repo = settings.github_repo or ""
    release, source = await _get_cached_release()

    latest_version = _normalize_version(release.tag if release else "")
    has_update = bool(latest_version) and _compare_versions(current_version, latest_version)
    deployment_mode = detect_deployment_mode()

    return {
        "currentVersion": current_version,
        "latestVersion": latest_version,
        "hasUpdate": has_update,
        "deploymentMode": deployment_mode,
        "releaseNotes": release.body if release else "",
        "publishedAt": release.published_at if release else "",
        "offlineBackup": _build_offline_backup(release, repo),
        "githubApiSource": source,
        "cachedAt": _format_cached_at(release),
    }


def invalidate_cache() -> None:
    """Clear the in-process release cache. Mainly for tests."""
    global _cache
    _cache = None
