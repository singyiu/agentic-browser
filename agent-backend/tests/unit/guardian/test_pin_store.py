"""Unit tests for the guardian parent-PIN store (PBKDF2 hash file + env back-compat)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_backend.guardian.pin_store import PinStore, validate_pin_format


def _store(tmp_path: Path, *, env_pin: str = "") -> PinStore:
    return PinStore(str(tmp_path / "admin.json"), env_pin=env_pin)


# --- is_configured -----------------------------------------------------------


def test_not_configured_when_no_file_and_no_env(tmp_path: Path) -> None:
    assert _store(tmp_path).is_configured() is False


def test_configured_from_env_pin(tmp_path: Path) -> None:
    assert _store(tmp_path, env_pin="1234").is_configured() is True


def test_configured_after_set_pin(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_pin("1234")
    assert store.is_configured() is True


# --- verify ------------------------------------------------------------------


def test_verify_correct_and_wrong_pin_after_set(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_pin("4825")
    assert store.verify("4825") is True
    assert store.verify("0000") is False
    assert store.verify("") is False


def test_verify_uses_env_pin_when_no_file(tmp_path: Path) -> None:
    store = _store(tmp_path, env_pin="987654")
    assert store.verify("987654") is True
    assert store.verify("123456") is False


def test_file_pin_takes_precedence_over_env(tmp_path: Path) -> None:
    store = _store(tmp_path, env_pin="0000")
    store.set_pin("4321")
    assert store.verify("4321") is True  # the file wins
    assert store.verify("0000") is False  # the old env PIN is no longer accepted


def test_verify_false_when_unconfigured(tmp_path: Path) -> None:
    assert _store(tmp_path).verify("1234") is False


# --- storage format / security ----------------------------------------------


def test_hash_file_never_stores_plaintext_pin(tmp_path: Path) -> None:
    path = tmp_path / "admin.json"
    PinStore(str(path)).set_pin("57913")
    text = path.read_text()
    assert "57913" not in text
    data = json.loads(text)
    assert data["algo"] == "pbkdf2_sha256"
    assert data["iterations"] >= 600_000
    assert data["salt"] and data["hash"]


def test_same_pin_yields_distinct_salt_and_hash(tmp_path: Path) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    PinStore(str(a)).set_pin("1234")
    PinStore(str(b)).set_pin("1234")
    da, db = json.loads(a.read_text()), json.loads(b.read_text())
    assert da["salt"] != db["salt"]
    assert da["hash"] != db["hash"]


def test_set_pin_creates_parent_dir(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "admin.json"
    PinStore(str(nested)).set_pin("1234")
    assert nested.exists()


def test_malformed_file_is_treated_as_unconfigured(tmp_path: Path) -> None:
    path = tmp_path / "admin.json"
    path.write_text("not json{")
    store = PinStore(str(path))
    assert store.is_configured() is False
    assert store.verify("1234") is False


# --- validate_pin_format -----------------------------------------------------


@pytest.mark.parametrize("pin", ["1234", "12345", "12345678"])
def test_validate_accepts_4_to_8_digits(pin: str) -> None:
    assert validate_pin_format(pin) is None


@pytest.mark.parametrize("pin", ["", "123", "123456789", "abcd", "12 34", "12.3", "１２３４"])
def test_validate_rejects_bad_pins(pin: str) -> None:
    assert validate_pin_format(pin) is not None
