from pathlib import Path

import pytest

from app.api.v1.routes.misc import _load_address_dict_from_json, _resolve_address_dict_path


EXPECTED_PATH = Path(__file__).resolve().parents[1] / "app" / "data" / "china_address_dict.json"


def test_resolve_address_dict_path_from_source_layout():
    assert _resolve_address_dict_path() == EXPECTED_PATH


def test_load_address_dict_from_json_has_non_empty_tree():
    tree = _load_address_dict_from_json()
    assert isinstance(tree, dict)
    assert tree["provinces"]


def test_missing_address_dict_is_distinguishable(monkeypatch):
    missing_path = Path("missing-china-address-dict.json")
    monkeypatch.setattr(
        "app.api.v1.routes.misc._resolve_address_dict_path",
        lambda: missing_path,
    )
    with pytest.raises(FileNotFoundError):
        _load_address_dict_from_json()
