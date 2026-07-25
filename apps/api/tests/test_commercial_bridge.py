from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import commercial_bridge


@pytest.mark.asyncio
async def test_proxy_about_content_prefers_local_changelog_and_preserves_remote_content(monkeypatch):
    remote = {
        "heroTitle": "商业版标题",
        "communityCards": [{"imageUrl": "/uploads/community.png"}],
        "logs": [{"v": "v9.9.9", "d": "商业版单版本"}],
    }
    config = {"baseUrl": "https://commercial.example", "accessToken": "token"}
    local_logs = [
        {"v": "最新版本"},
        {"v": "v1.4.0"},
        {"v": "v1.3.0"},
        {"v": "v1.2.0"},
        {"v": "v1.1.0"},
        {"v": "v1.0.0"},
    ]

    monkeypatch.setattr(commercial_bridge, "get_commercial_bridge_config", lambda: config)
    monkeypatch.setattr(commercial_bridge, "commercial_bridge_is_configured", lambda _: True)
    monkeypatch.setattr(commercial_bridge, "_request_bridge", lambda *args, **kwargs: _resolved(remote))
    monkeypatch.setattr(commercial_bridge, "parse_changelog_to_logs", lambda: local_logs)

    content = await commercial_bridge.proxy_get_about_content()

    assert content["heroTitle"] == "商业版标题"
    assert content["communityCards"][0]["imageUrl"] == "https://commercial.example/uploads/community.png"
    assert content["bridgeEnabled"] is True
    assert content["logs"] == local_logs


async def _resolved(value):
    return value
