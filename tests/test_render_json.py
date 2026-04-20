"""Tests for JSON rendering."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from cleanup_mac import Candidate, PackageCleanup, render_json


def test_produces_valid_json():
    buf = StringIO()
    render_json(
        candidates=[
            Candidate(
                path=Path("/tmp/a"),
                size_bytes=1024,
                category="user_cache",
                reason="x",
            ),
        ],
        packages=[
            PackageCleanup(
                tool="brew",
                current_size_bytes=2048,
                apply_command=["brew", "cleanup"],
            )
        ],
        mode="dry-run",
        installed_count=42,
        log_path=Path("/tmp/cleanup-mac.log"),
        out=buf,
    )
    data = json.loads(buf.getvalue())
    assert data["schema_version"] == 1
    assert "tool_version" in data
    assert data["mode"] == "dry-run"
    assert data["installed_bundle_ids_count"] == 42
    assert "user_cache" in data["categories"]
    assert data["categories"]["user_cache"]["total_bytes"] == 1024
    assert data["total_bytes"] == 1024 + 2048
    assert "packages" in data
    assert data["packages"]["total_bytes"] == 2048
    assert data["log_path"] == "/tmp/cleanup-mac.log"
    assert "timestamp" in data


def test_empty_categories_omitted():
    buf = StringIO()
    render_json(
        candidates=[],
        packages=[],
        mode="dry-run",
        installed_count=0,
        log_path=None,
        out=buf,
    )
    data = json.loads(buf.getvalue())
    assert data["total_bytes"] == 0
    assert data["log_path"] is None
