"""Tests for scan_user_caches."""

from __future__ import annotations

import os
import time
from pathlib import Path

from cleanup_mac import BUILTIN_WHITELIST, scan_user_caches


def _touch_old(path: Path, days: int) -> None:
    """Push mtime into the past by `days` days."""
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def test_returns_eligible_caches(fake_home: Path):
    caches = fake_home / "Library/Caches"
    spotify = caches / "com.spotify.client"
    spotify.mkdir()
    (spotify / "big.bin").write_bytes(b"0" * (2 * 1024 * 1024))  # 2 MB
    _touch_old(spotify, 30)

    candidates = scan_user_caches(
        root=caches,
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,
    )
    paths = [c.path.name for c in candidates]
    assert "com.spotify.client" in paths
    assert all(c.category == "user_cache" for c in candidates)


def test_skips_whitelist(fake_home: Path):
    caches = fake_home / "Library/Caches"
    apple = caches / "com.apple.Safari"
    apple.mkdir()
    (apple / "big.bin").write_bytes(b"0" * (2 * 1024 * 1024))
    _touch_old(apple, 30)

    candidates = scan_user_caches(
        root=caches,
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,
    )
    assert "com.apple.Safari" not in [c.path.name for c in candidates]


def test_skips_recent(fake_home: Path):
    caches = fake_home / "Library/Caches"
    fresh = caches / "com.example.Fresh"
    fresh.mkdir()
    (fresh / "x.bin").write_bytes(b"0" * (2 * 1024 * 1024))
    # mtime is now (freshly created)

    candidates = scan_user_caches(
        root=caches,
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,
    )
    assert "com.example.Fresh" not in [c.path.name for c in candidates]


def test_skips_small(fake_home: Path):
    caches = fake_home / "Library/Caches"
    tiny = caches / "com.example.Tiny"
    tiny.mkdir()
    (tiny / "x.bin").write_bytes(b"0" * 1024)  # 1 KB
    _touch_old(tiny, 30)

    candidates = scan_user_caches(
        root=caches,
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,  # 1 MB
    )
    assert "com.example.Tiny" not in [c.path.name for c in candidates]


def test_missing_root_returns_empty(tmp_path: Path):
    candidates = scan_user_caches(
        root=tmp_path / "does-not-exist",
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1,
    )
    assert candidates == []
