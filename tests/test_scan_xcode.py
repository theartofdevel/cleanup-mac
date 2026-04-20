"""Tests for scan_xcode."""

from __future__ import annotations

import os
import time
from pathlib import Path

from cleanup_mac import scan_xcode


def _aged(path: Path, days: int) -> None:
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def _populate(dir_path: Path, mb: int) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "blob.bin").write_bytes(b"0" * mb * 1024 * 1024)


def test_derived_data_respects_min_age(fake_home: Path):
    """DerivedData must honour --min-age. Deleting a DerivedData entry for
    a project being actively built corrupts xcodebuild's state, so the
    scanner now defers to the same age threshold every other category
    uses — projects touched within the window are left alone."""
    dd = fake_home / "Library/Developer/Xcode/DerivedData/SomeProject-abcd"
    _populate(dd, 5)
    _aged(dd, 2)  # below default min-age of 7 — must be skipped.

    results = scan_xcode(
        developer_root=fake_home / "Library/Developer",
        min_age_days=7,
        min_size_bytes=1024 * 1024,
        aggressive=False,
    )
    assert not any("DerivedData" in str(c.path) for c in results)

    _aged(dd, 30)  # older than min_age — now eligible.
    results = scan_xcode(
        developer_root=fake_home / "Library/Developer",
        min_age_days=7,
        min_size_bytes=1024 * 1024,
        aggressive=False,
    )
    assert any("DerivedData" in str(c.path) for c in results)


def test_archives_only_under_aggressive(fake_home: Path):
    arch = fake_home / "Library/Developer/Xcode/Archives/2024-01-01/A.xcarchive"
    _populate(arch, 5)
    _aged(arch, 365)

    non_agg = scan_xcode(
        developer_root=fake_home / "Library/Developer",
        min_age_days=7,
        min_size_bytes=1024 * 1024,
        aggressive=False,
    )
    assert not any("Archives" in str(c.path) for c in non_agg)

    agg = scan_xcode(
        developer_root=fake_home / "Library/Developer",
        min_age_days=7,
        min_size_bytes=1024 * 1024,
        aggressive=True,
    )
    assert any("Archives" in str(c.path) for c in agg)


def test_archives_only_matches_xcarchive(fake_home: Path):
    """Entries in Archives/<day>/ that are not .xcarchive bundles (user
    bak files, notes, unrelated folders) must not be scanned for deletion."""
    day = fake_home / "Library/Developer/Xcode/Archives/2024-01-01"
    day.mkdir(parents=True)
    # A real archive bundle — should be picked up.
    real = day / "MyApp.xcarchive"
    _populate(real, 5)
    _aged(real, 365)
    # A user backup folder inside the day dir — must be ignored.
    unrelated = day / "backup-notes"
    _populate(unrelated, 5)
    _aged(unrelated, 365)

    res = scan_xcode(
        developer_root=fake_home / "Library/Developer",
        min_age_days=7,
        min_size_bytes=1024 * 1024,
        aggressive=True,
    )
    paths = [str(c.path) for c in res]
    assert any("MyApp.xcarchive" in p for p in paths)
    assert not any("backup-notes" in p for p in paths)


def test_ios_device_support_uses_90d(fake_home: Path):
    ds = fake_home / "Library/Developer/Xcode/iOS DeviceSupport/17.0"
    _populate(ds, 5)
    _aged(ds, 60)  # newer than 90 — must be skipped

    res = scan_xcode(
        developer_root=fake_home / "Library/Developer",
        min_age_days=7,
        min_size_bytes=1024 * 1024,
        aggressive=False,
    )
    assert not any("iOS DeviceSupport/17.0" in str(c.path) for c in res)

    _aged(ds, 120)
    res2 = scan_xcode(
        developer_root=fake_home / "Library/Developer",
        min_age_days=7,
        min_size_bytes=1024 * 1024,
        aggressive=False,
    )
    assert any("iOS DeviceSupport" in str(c.path) for c in res2)


def test_core_simulator_caches(fake_home: Path):
    cs = fake_home / "Library/Developer/CoreSimulator/Caches/dyld"
    _populate(cs, 5)
    _aged(cs, 30)

    res = scan_xcode(
        developer_root=fake_home / "Library/Developer",
        min_age_days=7,
        min_size_bytes=1024 * 1024,
        aggressive=False,
    )
    assert any("CoreSimulator/Caches" in str(c.path) for c in res)
