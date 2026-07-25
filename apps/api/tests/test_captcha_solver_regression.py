from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.file_secrets import resolve_file_secret_values
from app.services import captcha_solver


def test_internal_api_token_uses_internal_api_token_file(tmp_path, monkeypatch):
    token_file = tmp_path / "internal_api_token"
    token_file.write_text("file-token\n", encoding="utf-8")
    values = resolve_file_secret_values(
        {
            "internal_api_token": "",
            "internal_api_token_file": str(token_file),
        },
        secret_fields=("internal_api_token",),
    )
    monkeypatch.setattr(captcha_solver.settings, "internal_api_token", values["internal_api_token"])
    monkeypatch.setenv("INTERNAL_API_TOKEN", "different-environment-token")

    assert captcha_solver._resolve_internal_api_token() == "file-token"


def test_messages_page_image_send_paths_only_refresh_conversations():
    source_path = Path(__file__).resolve().parents[2] / "web" / "src" / "pages" / "MessagesPage.vue"
    source = source_path.read_text(encoding="utf-8")

    assert source.count("await refreshAll(true)") == 1
    assert source.count("await loadConversations(true, { background: true })") >= 6
