"""Tests for scan_logs."""

from __future__ import annotations

import os
import time
from pathlib import Path

from cleanup_mac import BUILTIN_WHITELIST, scan_logs


def _touch_old(path: Path, days: int) -> None:
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def test_returns_eligible_logs(fake_home: Path):
    logs = fake_home / "Library/Logs"
    slack = logs / "Slack"
    slack.mkdir()
    (slack / "big.log").write_bytes(b"0" * (2 * 1024 * 1024))
    _touch_old(slack, 30)

    results = scan_logs(
        root=logs,
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,
    )
    assert any(c.path.name == "Slack" for c in results)
    assert all(c.category == "log" for c in results)


def test_diagnostic_reports_protected_recent(fake_home: Path):
    """DiagnosticReports newer than 30 days must be skipped regardless of min_age."""
    logs = fake_home / "Library/Logs"
    dr = logs / "DiagnosticReports"
    dr.mkdir()
    (dr / "crash.ips").write_bytes(b"0" * (5 * 1024 * 1024))
    _touch_old(dr, 15)  # 15 days < 30

    results = scan_logs(
        root=logs,
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,
    )
    assert "DiagnosticReports" not in [c.path.name for c in results]


def test_diagnostic_reports_eligible_when_old(fake_home: Path):
    logs = fake_home / "Library/Logs"
    dr = logs / "DiagnosticReports"
    dr.mkdir()
    (dr / "crash.ips").write_bytes(b"0" * (5 * 1024 * 1024))
    _touch_old(dr, 90)  # older than 30

    results = scan_logs(
        root=logs,
        whitelist=BUILTIN_WHITELIST,
        min_age_days=7,
        min_size_bytes=1024 * 1024,
    )
    assert any(c.path.name == "DiagnosticReports" for c in results)


def test_missing_root_returns_empty(tmp_path: Path):
    assert (
        scan_logs(
            root=tmp_path / "missing",
            whitelist=BUILTIN_WHITELIST,
            min_age_days=7,
            min_size_bytes=1,
        )
        == []
    )
