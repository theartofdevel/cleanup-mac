"""Tests for bundle-ID utilities and installed-app indexing."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

from cleanup_mac import base_id, get_installed_bundle_ids, is_bundle_id


def _make_app(apps_dir: Path, app_name: str, bundle_id: str) -> Path:
    app = apps_dir / f"{app_name}.app"
    (app / "Contents").mkdir(parents=True)
    plist_path = app / "Contents/Info.plist"
    with open(plist_path, "wb") as f:
        plistlib.dump({"CFBundleIdentifier": bundle_id}, f)
    return app


def test_is_bundle_id_positive():
    assert is_bundle_id("com.apple.Safari")
    assert is_bundle_id("com.google.Chrome")
    assert is_bundle_id("org.mozilla.firefox")
    assert is_bundle_id("com.example.App.plist")
    assert is_bundle_id("com.example.App.savedState")


def test_is_bundle_id_negative():
    assert not is_bundle_id("Spotify")
    assert not is_bundle_id("JetBrains")
    assert not is_bundle_id("Adobe Photoshop 2024")
    assert not is_bundle_id("")


def test_base_id_strips_known_suffixes():
    assert base_id("com.example.App.plist") == "com.example.App"
    assert base_id("com.example.App.savedState") == "com.example.App"
    assert base_id("com.example.App") == "com.example.App"


def test_get_installed_bundle_ids(tmp_path: Path):
    apps = tmp_path / "Applications"
    apps.mkdir()
    _make_app(apps, "Safari", "com.apple.Safari")
    _make_app(apps, "Chrome", "com.google.Chrome")

    sys_apps = tmp_path / "SystemApplications"
    sys_apps.mkdir()
    _make_app(sys_apps, "Mail", "com.apple.mail")

    ids = get_installed_bundle_ids(roots=[apps, sys_apps])
    assert "com.apple.Safari" in ids
    assert "com.google.Chrome" in ids
    assert "com.apple.mail" in ids


def test_get_installed_bundle_ids_handles_missing_plist(tmp_path: Path):
    apps = tmp_path / "Applications"
    apps.mkdir()
    broken = apps / "Broken.app"
    (broken / "Contents").mkdir(parents=True)  # no Info.plist
    _make_app(apps, "Good", "com.example.Good")

    ids = get_installed_bundle_ids(roots=[apps])
    assert "com.example.Good" in ids


def test_get_installed_bundle_ids_finds_nested_apps(tmp_path: Path):
    """Xcode.app contains other .app bundles (Instruments, etc.) — we want them too."""
    apps = tmp_path / "Applications"
    apps.mkdir()
    xcode = _make_app(apps, "Xcode", "com.apple.dt.Xcode")
    (xcode / "Contents/Applications").mkdir(parents=True)
    nested = xcode / "Contents/Applications/Instruments.app"
    (nested / "Contents").mkdir(parents=True)
    with open(nested / "Contents/Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": "com.apple.dt.Instruments"}, f)

    ids = get_installed_bundle_ids(roots=[apps])
    assert "com.apple.dt.Xcode" in ids
    assert "com.apple.dt.Instruments" in ids


def test_get_installed_bundle_ids_finds_app_extensions(tmp_path: Path):
    apps = tmp_path / "Applications"
    apps.mkdir()
    agenda = _make_app(apps, "Agenda", "com.momenta.agenda.macos")
    extension = agenda / "Contents/PlugIns/Agenda Widget.appex"
    (extension / "Contents").mkdir(parents=True)
    with open(extension / "Contents/Info.plist", "wb") as f:
        plistlib.dump(
            {"CFBundleIdentifier": "com.momenta.agenda.macos.extension-widget"}, f
        )

    ids = get_installed_bundle_ids(roots=[apps])
    assert "com.momenta.agenda.macos" in ids
    assert "com.momenta.agenda.macos.extension-widget" in ids


def test_get_installed_bundle_ids_includes_application_groups(
    tmp_path: Path, monkeypatch
):
    apps = tmp_path / "Applications"
    apps.mkdir()
    _make_app(apps, "Agenda", "com.momenta.agenda.macos")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=plistlib.dumps(
                {
                    "com.apple.security.application-groups": [
                        "WRBK2Z2EG7.group.com.momenta.agenda.macos"
                    ],
                }
            ),
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    ids = get_installed_bundle_ids(roots=[apps])
    assert "com.momenta.agenda.macos" in ids
    assert "WRBK2Z2EG7.group.com.momenta.agenda.macos" in ids
