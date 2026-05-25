"""Guardian unit-test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_backend.guardian import profile_manager


@pytest.fixture(autouse=True)
def _hermetic_profile_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the default profile data dir to a per-test tmp dir.

    Managers built without an explicit ``data_dir`` (e.g. the one ``create_app`` builds)
    open the Global profile's stores under ``PROFILE_DATA_DIR``. Without this, tests would
    create — and the blocklist precedence tests would write to — the repo's real
    ``data/profiles/global/``, leaking state across tests. Managers given an explicit
    ``data_dir`` (the ``_pm_client`` / ``_manager`` helpers) are unaffected.
    """
    monkeypatch.setattr(profile_manager, "PROFILE_DATA_DIR", str(tmp_path / "guardian-data"))
