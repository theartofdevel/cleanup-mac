"""Shared pytest fixtures for cleanup-mac tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Path.home() at a per-test tmp directory and pre-create ~/Library."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "Library").mkdir()
    (home / "Library" / "Caches").mkdir()
    (home / "Library" / "Logs").mkdir()
    (home / "Library" / "Application Support").mkdir()
    (home / "Library" / "Containers").mkdir()
    (home / "Library" / "Group Containers").mkdir()
    (home / "Library" / "Preferences").mkdir()
    (home / "Library" / "LaunchAgents").mkdir()
    (home / "Library" / "Saved Application State").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def fake_apps(tmp_path: Path) -> Path:
    """Directory to mimic /Applications."""
    apps = tmp_path / "Applications"
    apps.mkdir()
    return apps
