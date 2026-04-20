"""Tests for size/format utilities."""

from __future__ import annotations

from pathlib import Path

import pytest

from cleanup_mac import format_bytes, get_size


@pytest.mark.parametrize(
    "n,expected",
    [
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1024 * 1024, "1.0 MB"),
        (5 * 1024 * 1024, "5.0 MB"),
        (1024 * 1024 * 1024, "1.0 GB"),
        (int(2.5 * 1024 * 1024 * 1024), "2.5 GB"),
    ],
)
def test_format_bytes(n: int, expected: str):
    assert format_bytes(n) == expected


def test_get_size_of_file(tmp_path: Path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"0" * 4096)
    assert get_size(f) >= 4096


def test_get_size_of_directory(tmp_path: Path):
    (tmp_path / "a.bin").write_bytes(b"0" * 2048)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.bin").write_bytes(b"0" * 2048)
    assert get_size(tmp_path) >= 4096


def test_get_size_missing_returns_zero(tmp_path: Path):
    assert get_size(tmp_path / "does-not-exist") == 0
