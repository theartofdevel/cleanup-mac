"""Tests for aggressive-only scanners."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

from cleanup_mac import BUILTIN_WHITELIST, scan_system_caches, scan_temp_files


def _aged(path: Path, days: int) -> None:
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def _populate(path: Path, mb: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "x.bin").write_bytes(b"0" * mb * 1024 * 1024)


def test_system_caches_returns_candidates(tmp_path: Path):
    fake = tmp_path / "fake_sys_caches"
    fake.mkdir()
    third = fake / "com.thirdparty.Tool"
    _populate(third, 5)
    _aged(third, 30)

    with patch("os.geteuid", return_value=0):
        res = scan_system_caches(
            root=fake,
            whitelist=BUILTIN_WHITELIST,
            min_age_days=7,
            min_size_bytes=1024 * 1024,
        )
    assert any(c.path.name == "com.thirdparty.Tool" for c in res)
    assert all(c.category == "system_cache" for c in res)


def test_system_caches_requires_root(tmp_path: Path):
    """When not root, returns empty + warning; no scanning attempted."""
    fake = tmp_path / "fake_sys_caches"
    fake.mkdir()
    _populate(fake / "com.thirdparty.Tool", 5)

    with patch("os.geteuid", return_value=501):
        res = scan_system_caches(
            root=fake,
            whitelist=BUILTIN_WHITELIST,
            min_age_days=7,
            min_size_bytes=1024 * 1024,
        )
    assert res == []


def test_system_caches_skips_apple(tmp_path: Path):
    fake = tmp_path / "fake_sys_caches"
    fake.mkdir()
    _populate(fake / "com.apple.kext", 5)
    _aged(fake / "com.apple.kext", 30)

    with patch("os.geteuid", return_value=0):
        res = scan_system_caches(
            root=fake,
            whitelist=BUILTIN_WHITELIST,
            min_age_days=7,
            min_size_bytes=1024 * 1024,
        )
    assert "com.apple.kext" not in [c.path.name for c in res]


def test_temp_files_skips_open_file(tmp_path: Path):
    fake = tmp_path / "tmp"
    fake.mkdir()
    f = fake / "open.tmp"
    f.write_bytes(b"0" * 2 * 1024 * 1024)
    _aged(f, 3)

    # lsof returns nonzero when the file IS open; zero when not.
    # Simulate "open": lsof returns 0 (file is in use).
    with patch("subprocess.run") as run:
        # First call by get_size uses du; accept it normally.
        # We'll have get_size patched separately to not confuse.
        run.return_value.returncode = 0
        run.return_value.stdout = "cmd\npath\n"
        with patch("cleanup_mac._util.get_size", return_value=2 * 1024 * 1024):
            res = scan_temp_files(
                roots=[fake],
                min_age_days=1,
                min_size_bytes=1024,
            )
    assert [c.path.name for c in res] == []


def test_temp_files_emits_when_not_open(tmp_path: Path):
    fake = tmp_path / "tmp"
    fake.mkdir()
    f = fake / "closed.tmp"
    f.write_bytes(b"0" * 2 * 1024 * 1024)
    _aged(f, 3)

    with patch("subprocess.run") as run:
        run.return_value.returncode = 1  # lsof: not open
        run.return_value.stdout = ""
        with patch("cleanup_mac._util.get_size", return_value=2 * 1024 * 1024):
            res = scan_temp_files(
                roots=[fake],
                min_age_days=1,
                min_size_bytes=1024,
            )
    assert any(c.path.name == "closed.tmp" for c in res)


def test_temp_files_skips_other_user(tmp_path: Path):
    """Under sudo (euid == 0), files owned by a different UID must be
    excluded — deleting another user's temp files as root is a cross-user
    data-loss / daemon-destabilisation risk."""
    fake = tmp_path / "tmp"
    fake.mkdir()
    f = fake / "other-user.tmp"
    f.write_bytes(b"0" * 2 * 1024 * 1024)
    _aged(f, 3)

    # Simulate: the tool runs as euid=0 but the file is owned by another user.
    with patch("os.geteuid", return_value=99999):
        with patch("subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            with patch("cleanup_mac._util.get_size", return_value=2 * 1024 * 1024):
                res = scan_temp_files(
                    roots=[fake],
                    min_age_days=1,
                    min_size_bytes=1024,
                )
    assert [c.path.name for c in res] == []
