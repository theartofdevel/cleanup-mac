"""Tests for terminal rendering."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from cleanup_mac import Candidate, PackageCleanup, render_terminal


def _c(name: str, size: int, category: str = "user_cache") -> Candidate:
    return Candidate(
        path=Path(f"/tmp/{name}"),
        size_bytes=size,
        category=category,
        reason="test",
    )


def test_renders_totals():
    buf = StringIO()
    candidates = [
        _c("a", 1024 * 1024 * 100, "user_cache"),
        _c("b", 1024 * 1024 * 50, "user_cache"),
        _c("c", 1024 * 1024 * 500, "leftover"),
    ]
    render_terminal(
        candidates=candidates,
        packages=[],
        is_dry_run=True,
        verbose=False,
        quiet=False,
        colors=False,
        out=buf,
        log_path=None,
    )
    output = buf.getvalue()
    assert "User caches" in output
    assert "Leftovers" in output
    assert "TOTAL" in output
    assert "DRY RUN" in output
    assert "would free" in output


def test_apply_mode_says_found_not_would_free():
    """Non-dry-run rendering happens BEFORE execution — must not claim items are deleted."""
    buf = StringIO()
    render_terminal(
        candidates=[_c("a", 1024 * 1024, "user_cache")],
        packages=[],
        is_dry_run=False,
        verbose=False,
        quiet=False,
        colors=False,
        out=buf,
        log_path=None,
    )
    output = buf.getvalue()
    assert "would free" not in output
    assert "DELETED" not in output
    assert "found" in output
    assert "about to delete" in output


def test_quiet_suppresses_paths():
    buf = StringIO()
    render_terminal(
        candidates=[_c("a", 1024 * 1024, "user_cache")],
        packages=[],
        is_dry_run=True,
        verbose=False,
        quiet=True,
        colors=False,
        out=buf,
        log_path=None,
    )
    output = buf.getvalue()
    assert "/tmp/a" not in output
    assert "TOTAL" in output


def test_verbose_shows_all():
    buf = StringIO()
    candidates = [_c(f"app-{i}", 1024 * 1024 * (20 - i)) for i in range(15)]
    render_terminal(
        candidates=candidates,
        packages=[],
        is_dry_run=True,
        verbose=True,
        quiet=False,
        colors=False,
        out=buf,
        log_path=None,
    )
    output = buf.getvalue()
    for i in range(15):
        assert f"app-{i}" in output


def test_non_verbose_truncates():
    buf = StringIO()
    candidates = [_c(f"app-{i}", 1024 * 1024 * (20 - i)) for i in range(15)]
    render_terminal(
        candidates=candidates,
        packages=[],
        is_dry_run=True,
        verbose=False,
        quiet=False,
        colors=False,
        out=buf,
        log_path=None,
    )
    output = buf.getvalue()
    assert "(5 more" in output  # 15 - 10 = 5


def test_package_row_rendered():
    buf = StringIO()
    pkgs = [
        PackageCleanup(
            tool="brew",
            current_size_bytes=2_000_000_000,
            apply_command=["brew", "cleanup", "--prune=all"],
        )
    ]
    render_terminal(
        candidates=[],
        packages=pkgs,
        is_dry_run=True,
        verbose=False,
        quiet=False,
        colors=False,
        out=buf,
        log_path=None,
    )
    output = buf.getvalue()
    assert "brew" in output
    assert "Package managers" in output
