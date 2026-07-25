from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from ..core.secret_store import decrypt_secret, encrypt_secret


GENERAL_MODEL_API_KEY_PURPOSE = "system.general_model.api_key"
EMBEDDING_MODEL_API_KEY_PURPOSE = "system.embedding_model.api_key"
MODEL_CONFIG_API_KEY_PURPOSE = "model_config.api_key"
AI_PROVIDER_API_KEY_PURPOSE = "xianyu_ai_provider.api_key"
RAG_EMBEDDING_API_KEY_PURPOSE = "rag_knowledge_base.embedding_api_key"
FEEDBACK_STORE_PURPOSE = "frontend.feedback_store"
AD_APPLICATION_STORE_PURPOSE = "frontend.ad_applications_store"
REMOTE_SLIDER_API_KEY_PURPOSE = "remote_slider.api_key"

SENSITIVE_SETTING_KEYS = frozenset(
    {
        "admin_password_hash",
        "frontend.notification_settings",
        "frontend.feedback_store",
        "frontend.ad_applications_store",
        "open_source.system_config",
        "remote_slider.config",
    }
)

SYSTEM_SECRET_FIELDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("generalModel", "apiKey"), GENERAL_MODEL_API_KEY_PURPOSE),
    (("embeddingModel", "apiKey"), EMBEDDING_MODEL_API_KEY_PURPOSE),
)


def _transform_system_config_secrets(
    config: Any,
    transform: Callable[..., str | None],
) -> dict[str, Any]:
    result = deepcopy(config) if isinstance(config, dict) else {}
    for path, purpose in SYSTEM_SECRET_FIELDS:
        parent: dict[str, Any] = result
        for segment in path[:-1]:
            value = parent.get(segment)
            if not isinstance(value, dict):
                value = {}
                parent[segment] = value
            parent = value
        field = path[-1]
        value = parent.get(field)
        if value not in (None, ""):
            parent[field] = transform(str(value), purpose=purpose)
    return result


def encrypt_system_config_secrets(config: Any) -> dict[str, Any]:
    """Return a storage-safe copy of the open-source system configuration."""
    return _transform_system_config_secrets(config, encrypt_secret)


def decrypt_system_config_secrets(config: Any) -> dict[str, Any]:
    """Return a runtime copy while retaining compatibility with legacy plaintext."""
    return _transform_system_config_secrets(config, decrypt_secret)


def prepare_secret_for_storage(
    *,
    incoming: Any,
    existing: Any = None,
    purpose: str,
    clear: bool = False,
) -> str | None:
    """Resolve form-update semantics and return only storage-safe ciphertext.

    Empty input preserves the existing credential. A caller must opt in to clearing
    through ``clear=True``; this keeps masked form fields from erasing credentials.
    Legacy plaintext is encrypted even when an otherwise unrelated edit is saved.
    """
    if clear:
        return None
    incoming_text = str(incoming or "").strip()
    candidate = incoming_text if incoming_text else existing
    if candidate in (None, ""):
        return None
    return encrypt_secret(str(candidate), purpose=purpose)


def decrypt_runtime_secret(value: Any, *, purpose: str) -> str:
    """Resolve ciphertext or legacy plaintext immediately before runtime use."""
    return str(decrypt_secret(None if value is None else str(value), purpose=purpose) or "")


def is_sensitive_setting_key(value: Any) -> bool:
    return str(value or "").strip().casefold() in SENSITIVE_SETTING_KEYS
