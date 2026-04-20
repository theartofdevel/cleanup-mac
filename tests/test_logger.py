"""Tests for cleanup_mac.logger — production logging (B2)."""

from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path

import pytest

from cleanup_mac.logger import (
    LOG_FORMAT_NAMES,
    LOG_LEVEL_NAMES,
    LOG_SCHEMA_VERSION,
    setup_logger,
    structured_log,
)


def _drain_logger(logger: logging.Logger) -> None:
    """Flush every handler so test assertions see the written content."""
    for h in logger.handlers:
        with contextlib.suppress(OSError):
            h.flush()


# --- setup_logger basics --------------------------------------------------

def test_setup_logger_creates_file(tmp_path: Path):
    log_dir = tmp_path / "logs"
    logger, log_path = setup_logger(log_dir=log_dir, enabled=True)
    structured_log(logger, logging.INFO, "test_event", key="value")
    _drain_logger(logger)
    assert log_path is not None
    assert log_path.parent == log_dir
    assert log_path.exists()
    content = log_path.read_text()
    assert "test_event" in content
    assert "value" in content


def test_setup_logger_disabled_returns_null_handler(tmp_path: Path):
    logger, log_path = setup_logger(log_dir=tmp_path, enabled=False)
    # Should not raise or write anywhere.
    structured_log(logger, logging.INFO, "ignored")
    assert log_path is None
    assert any(isinstance(h, logging.NullHandler) for h in logger.handlers)


def test_log_path_includes_timestamp(tmp_path: Path):
    _, log_path = setup_logger(log_dir=tmp_path, enabled=True)
    assert log_path is not None
    # Format: YYYY-MM-DD-HHMMSS.log
    stem = log_path.stem
    parts = stem.split("-")
    assert len(parts) == 4
    assert len(parts[0]) == 4 and parts[0].isdigit()


# --- text format ----------------------------------------------------------

def test_text_format_writes_one_line_per_record(tmp_path: Path):
    logger, path = setup_logger(tmp_path, enabled=True, log_format="text")
    structured_log(logger, logging.INFO, "trashed", path="/tmp/x", size_bytes=123)
    _drain_logger(logger)

    assert path is not None
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 1
    line = lines[0]
    assert "event=trashed" in line
    assert 'path="/tmp/x"' in line
    assert "size_bytes=123" in line
    assert "INFO" in line


# --- JSON format ----------------------------------------------------------

def test_json_format_one_object_per_line(tmp_path: Path):
    logger, path = setup_logger(tmp_path, enabled=True, log_format="json")
    structured_log(logger, logging.INFO, "trashed", path="/tmp/x", size_bytes=123)
    structured_log(logger, logging.WARNING, "user_cancelled", reason="KeyboardInterrupt")
    _drain_logger(logger)

    assert path is not None
    lines = [line for line in path.read_text().split("\n") if line.strip()]
    assert len(lines) == 2
    for line in lines:
        obj = json.loads(line)
        assert obj["schema_version"] == LOG_SCHEMA_VERSION
        assert "ts" in obj
        assert "event" in obj
        assert "level" in obj
        assert "tool_version" in obj
    first = json.loads(lines[0])
    assert first["event"] == "trashed"
    assert first["path"] == "/tmp/x"
    assert first["size_bytes"] == 123


def test_json_format_escapes_pathological_paths(tmp_path: Path):
    """A filename with newlines cannot inject forged lines in JSONL output."""
    logger, path = setup_logger(tmp_path, enabled=True, log_format="json")
    evil = "/tmp/normal\n{\"schema_version\":1,\"event\":\"forged\"}\n"
    structured_log(logger, logging.INFO, "trashed", path=evil, size_bytes=1)
    _drain_logger(logger)

    assert path is not None
    lines = [line for line in path.read_text().split("\n") if line.strip()]
    # Exactly one line — the forged payload did NOT break out.
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["event"] == "trashed"
    assert obj["path"] == evil  # raw value preserved; newlines are JSON-escaped


def test_json_format_includes_traceback_on_exc_info(tmp_path: Path):
    logger, path = setup_logger(tmp_path, enabled=True, log_format="json")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logger.error(
            "runtime_error",
            exc_info=True,
            extra={
                "cleanupmac_event": "runtime_error",
                "cleanupmac_fields": {"reason": "boom"},
            },
        )
    _drain_logger(logger)

    assert path is not None
    obj = json.loads(path.read_text().strip())
    assert obj["event"] == "runtime_error"
    assert "RuntimeError" in obj["traceback"]


# --- levels ---------------------------------------------------------------

def test_level_filters_debug_at_info(tmp_path: Path):
    logger, path = setup_logger(tmp_path, enabled=True, log_level="info")
    structured_log(logger, logging.DEBUG, "debug_trace", detail="hidden")
    structured_log(logger, logging.INFO, "visible", detail="shown")
    _drain_logger(logger)

    assert path is not None
    content = path.read_text()
    assert "debug_trace" not in content
    assert "hidden" not in content
    assert "visible" in content


def test_debug_level_emits_debug_records(tmp_path: Path):
    logger, path = setup_logger(tmp_path, enabled=True, log_level="debug")
    structured_log(logger, logging.DEBUG, "debug_trace", detail="shown")
    _drain_logger(logger)

    assert path is not None
    assert "debug_trace" in path.read_text()


def test_error_level_suppresses_info(tmp_path: Path):
    logger, path = setup_logger(tmp_path, enabled=True, log_level="error")
    structured_log(logger, logging.INFO, "info_event")
    structured_log(logger, logging.ERROR, "error_event")
    _drain_logger(logger)

    assert path is not None
    content = path.read_text()
    assert "info_event" not in content
    assert "error_event" in content


# --- retention ------------------------------------------------------------

def test_retention_keeps_newest_n(tmp_path: Path):
    # Pre-populate the dir with 5 stale log files with increasing mtimes.
    for i in range(5):
        f = tmp_path / f"2020-01-0{i + 1}-120000.log"
        f.write_text("old")
        os.utime(f, (1577836800 + i, 1577836800 + i))

    _, new_path = setup_logger(tmp_path, enabled=True, retention=2)

    remaining = sorted(p.name for p in tmp_path.glob("*.log"))
    # 2 newest old files + the new one = 3.
    assert new_path is not None and new_path.exists()
    assert len(remaining) == 3
    assert "2020-01-01-120000.log" not in remaining
    assert "2020-01-02-120000.log" not in remaining
    assert "2020-01-03-120000.log" not in remaining
    assert "2020-01-04-120000.log" in remaining
    assert "2020-01-05-120000.log" in remaining


def test_retention_zero_keeps_all(tmp_path: Path):
    for i in range(3):
        (tmp_path / f"old-{i}.log").write_text("old")

    setup_logger(tmp_path, enabled=True, retention=0)

    remaining = list(tmp_path.glob("*.log"))
    assert len(remaining) == 4  # 3 old + 1 new


def test_retention_is_idempotent_when_empty(tmp_path: Path):
    _, path = setup_logger(tmp_path, enabled=True, retention=5)
    assert path is not None and path.exists()


# --- validation -----------------------------------------------------------

def test_rejects_invalid_format(tmp_path: Path):
    with pytest.raises(ValueError, match="log_format"):
        setup_logger(tmp_path, enabled=True, log_format="xml")


def test_rejects_invalid_level(tmp_path: Path):
    with pytest.raises(ValueError, match="log_level"):
        setup_logger(tmp_path, enabled=True, log_level="trace")


def test_constants_are_tuples():
    """Exported name-sets are used in argparse `choices=` — must be immutable."""
    assert isinstance(LOG_FORMAT_NAMES, tuple)
    assert isinstance(LOG_LEVEL_NAMES, tuple)
    assert set(LOG_FORMAT_NAMES) == {"text", "json"}
    assert set(LOG_LEVEL_NAMES) == {"debug", "info", "warn", "error"}


# --- reentrancy -----------------------------------------------------------

def test_handlers_are_cleared_on_second_setup(tmp_path: Path):
    """Calling setup_logger twice (tests, or repeated main()) must not
    produce duplicate log lines."""
    logger1, _ = setup_logger(tmp_path, enabled=True, log_format="json")
    count_after_first = len(logger1.handlers)
    logger2, _ = setup_logger(tmp_path, enabled=True, log_format="json")
    assert len(logger2.handlers) == count_after_first
    assert logger1 is logger2  # same named logger singleton


def test_log_file_has_restricted_permissions(tmp_path: Path):
    """Audit log contains absolute paths (usernames, projects, bundle IDs).
    Must be owner-only readable so other local users / indexers can't
    harvest the user's cleanup history."""
    _, log_path = setup_logger(log_dir=tmp_path / "logs", enabled=True)
    assert log_path is not None
    mode = log_path.stat().st_mode & 0o777
    # Other and group bits must be zero; owner read+write required.
    assert mode & 0o077 == 0, f"log file too permissive: {oct(mode)}"
    assert mode & 0o600 == 0o600, f"log file missing owner rw: {oct(mode)}"


def test_retention_sorts_by_name_not_mtime(tmp_path: Path):
    """Two parallel runs within one filesystem-mtime tick must not let the
    retention trim delete a file currently being appended to. Name-based
    sort is deterministic because log names contain the timestamp."""
    from cleanup_mac.logger import _cleanup_old_logs

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # Create files where mtime order is reversed relative to name order,
    # to prove the sort uses names not mtimes.
    newest_by_name = log_dir / "2026-04-18-120000.log"
    older_by_name = log_dir / "2026-04-17-120000.log"
    newest_by_name.write_text("new")
    older_by_name.write_text("old")
    # Now flip mtimes — older name, newer mtime.
    os.utime(older_by_name, (9e9, 9e9))
    os.utime(newest_by_name, (1, 1))
    _cleanup_old_logs(log_dir, retention=1)
    # Retention must keep the one with the newer name, not the newer mtime.
    assert newest_by_name.exists()
    assert not older_by_name.exists()
