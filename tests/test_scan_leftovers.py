"""Tests for scan_leftovers — the most safety-critical scanner."""

from __future__ import annotations

import os
import plistlib
import time
from pathlib import Path

from cleanup_mac import BUILTIN_WHITELIST, scan_leftovers


def _mkapp(apps_dir: Path, name: str, bid: str) -> None:
    app = apps_dir / f"{name}.app"
    (app / "Contents").mkdir(parents=True)
    with open(app / "Contents/Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": bid}, f)


def _make_leftover(location: Path, name: str, size_mb: int = 5, age_days: int = 30) -> Path:
    entry = location / name
    entry.mkdir()
    (entry / "data.bin").write_bytes(b"0" * (size_mb * 1024 * 1024))
    old = time.time() - age_days * 86400
    os.utime(entry, (old, old))
    return entry


def test_finds_leftover_in_application_support(fake_home: Path, tmp_path: Path):
    apps = tmp_path / "Applications"
    apps.mkdir()
    _mkapp(apps, "Safari", "com.apple.Safari")  # installed

    # Figma uninstalled — has leftover
    _make_leftover(
        fake_home / "Library/Application Support", "com.figma.Desktop"
    )

    results = scan_leftovers(
        scan_locations=[fake_home / "Library/Application Support"],
        installed_ids={"com.apple.Safari"},
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,
    )
    names = [c.path.name for c in results]
    assert "com.figma.Desktop" in names
    assert all(c.category == "leftover" for c in results)


def test_skips_installed_apps(fake_home: Path):
    _make_leftover(
        fake_home / "Library/Application Support", "com.example.Installed"
    )
    results = scan_leftovers(
        scan_locations=[fake_home / "Library/Application Support"],
        installed_ids={"com.example.Installed"},
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,
    )
    assert "com.example.Installed" not in [c.path.name for c in results]


def test_ignores_non_bundle_id_names(fake_home: Path):
    """Consistency: folder 'Spotify' (not a bundle ID) is skipped, never reported as leftover."""
    _make_leftover(fake_home / "Library/Application Support", "Spotify")
    _make_leftover(fake_home / "Library/Application Support", "JetBrains")
    results = scan_leftovers(
        scan_locations=[fake_home / "Library/Application Support"],
        installed_ids=set(),
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,
    )
    assert [c.path.name for c in results] == []


def test_respects_whitelist(fake_home: Path):
    _make_leftover(fake_home / "Library/Application Support", "com.apple.Dashboard")
    results = scan_leftovers(
        scan_locations=[fake_home / "Library/Application Support"],
        installed_ids=set(),
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,
    )
    assert "com.apple.Dashboard" not in [c.path.name for c in results]


def test_respects_never_touch(fake_home: Path):
    """Even if a dir inside Keychains looked like a bundle ID, never-touch must block it."""
    kc = fake_home / "Library/Keychains"
    kc.mkdir()
    leftover = _make_leftover(kc, "com.whatever.App")  # under never-touch

    results = scan_leftovers(
        scan_locations=[kc],
        installed_ids=set(),
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,
    )
    assert leftover.name not in [c.path.name for c in results]


def test_plist_suffix_handled(fake_home: Path):
    """Preferences contain files like com.figma.Desktop.plist — strip suffix."""
    prefs = fake_home / "Library/Preferences"
    pl = prefs / "com.figma.Desktop.plist"
    pl.write_bytes(b"0" * (2 * 1024 * 1024))
    old = time.time() - 30 * 86400
    os.utime(pl, (old, old))

    results = scan_leftovers(
        scan_locations=[prefs],
        installed_ids=set(),
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,
    )
    assert pl.name in [c.path.name for c in results]


def test_min_size_filter(fake_home: Path):
    _make_leftover(
        fake_home / "Library/Application Support", "com.example.Small", size_mb=0
    )
    results = scan_leftovers(
        scan_locations=[fake_home / "Library/Application Support"],
        installed_ids=set(),
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,
    )
    assert "com.example.Small" not in [c.path.name for c in results]


def test_min_age_filter(fake_home: Path):
    fresh = fake_home / "Library/Application Support/com.example.Fresh"
    fresh.mkdir()
    (fresh / "x.bin").write_bytes(b"0" * (2 * 1024 * 1024))
    # no backdating — mtime is now

    results = scan_leftovers(
        scan_locations=[fake_home / "Library/Application Support"],
        installed_ids=set(),
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,
    )
    assert "com.example.Fresh" not in [c.path.name for c in results]


def test_infers_plain_app_support_name_from_bundle_id_anchor(fake_home: Path):
    cache = fake_home / "Library/Caches/com.nextcloud.desktopclient"
    cache.mkdir()
    (cache / "data.bin").write_bytes(b"0" * 1024)
    prefs = fake_home / "Library/Preferences/com.nextcloud.desktopclient.plist"
    prefs.write_bytes(b"0" * 512)
    app_support = _make_leftover(
        fake_home / "Library/Application Support", "Nextcloud", size_mb=2
    )
    old = time.time() - 30 * 86400
    os.utime(cache, (old, old))

    results = scan_leftovers(
        scan_locations=[
            fake_home / "Library/Application Support",
            fake_home / "Library/Caches",
            fake_home / "Library/Preferences",
        ],
        installed_ids=set(),
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,
    )

    by_path = {c.path: c for c in results}
    assert cache in by_path
    assert prefs in by_path
    assert app_support in by_path
    assert "inferred" in by_path[app_support].reason
    assert "com.nextcloud.desktopclient" in by_path[app_support].reason


def test_inference_ignores_generic_bundle_id_tokens(fake_home: Path):
    _make_leftover(fake_home / "Library/Application Support", "Desktop", size_mb=2)
    _make_leftover(
        fake_home / "Library/Application Support", "com.figma.Desktop", size_mb=2
    )

    results = scan_leftovers(
        scan_locations=[fake_home / "Library/Application Support"],
        installed_ids=set(),
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,
    )

    names = [c.path.name for c in results]
    assert "com.figma.Desktop" in names
    assert "Desktop" not in names


def test_plain_names_are_not_inferred_without_bundle_id_anchor(fake_home: Path):
    _make_leftover(fake_home / "Library/Application Support", "Nextcloud", size_mb=2)

    results = scan_leftovers(
        scan_locations=[fake_home / "Library/Application Support"],
        installed_ids=set(),
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,
    )

    assert "Nextcloud" not in [c.path.name for c in results]


def test_inference_ignores_tokens_still_used_by_installed_apps(fake_home: Path):
    _make_leftover(fake_home / "Library/Application Support", "Google", size_mb=2)
    _make_leftover(
        fake_home / "Library/Application Support", "com.google.GoogleUpdater", size_mb=2
    )

    results = scan_leftovers(
        scan_locations=[fake_home / "Library/Application Support"],
        installed_ids={"com.google.Chrome"},
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,
    )

    names = [c.path.name for c in results]
    assert "com.google.GoogleUpdater" in names
    assert "Google" not in names


def test_inference_ignores_versioned_tokens_used_by_installed_apps(fake_home: Path):
    _make_leftover(fake_home / "Library/Application Support", "ScreenFlow", size_mb=2)
    _make_leftover(
        fake_home / "Library/Application Support",
        "net.telestream.screenflow.globallibrary",
        size_mb=2,
    )

    results = scan_leftovers(
        scan_locations=[fake_home / "Library/Application Support"],
        installed_ids={"net.telestream.screenflow10"},
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,
    )

    names = [c.path.name for c in results]
    assert "net.telestream.screenflow.globallibrary" in names
    assert "ScreenFlow" not in names
