"""Tests for core data types."""

from __future__ import annotations

from pathlib import Path

from cleanup_mac import Candidate, PackageCleanup


def test_candidate_holds_fields():
    c = Candidate(
        path=Path("/tmp/foo"),
        size_bytes=1024,
        category="user_cache",
        reason="test",
    )
    assert c.path == Path("/tmp/foo")
    assert c.size_bytes == 1024
    assert c.category == "user_cache"
    assert c.reason == "test"


def test_package_cleanup_holds_fields():
    p = PackageCleanup(
        tool="brew",
        current_size_bytes=2048,
        apply_command=["brew", "cleanup", "--prune=all"],
    )
    assert p.tool == "brew"
    assert p.current_size_bytes == 2048
    assert p.apply_command == ["brew", "cleanup", "--prune=all"]
