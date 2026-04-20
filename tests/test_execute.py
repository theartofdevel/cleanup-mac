"""Tests for the execute() orchestrator."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

from cleanup_mac import Candidate, execute_candidates


def _cand(path: Path, size: int = 1024) -> Candidate:
    return Candidate(path=path, size_bytes=size, category="user_cache", reason="test")


def test_dry_run_no_writes(tmp_path: Path):
    f = tmp_path / "x"
    f.write_text("data")
    logger = logging.getLogger("test")
    freed = execute_candidates(
        [_cand(f, 4)],
        apply=False,
        permanent=False,
        allowed_roots=[tmp_path],
        logger=logger,
    )
    assert f.exists()
    assert freed == 0


def test_apply_trash_calls_move_to_trash(tmp_path: Path):
    f = tmp_path / "x"
    f.write_text("data")
    logger = logging.getLogger("test")
    with patch("cleanup_mac.execute.move_to_trash") as mt:
        freed = execute_candidates(
            [_cand(f, 4)],
            apply=True,
            permanent=False,
            allowed_roots=[tmp_path],
            logger=logger,
        )
    mt.assert_called_once()
    assert freed == 4


def test_apply_permanent_deletes(tmp_path: Path):
    f = tmp_path / "x"
    f.write_text("data")
    logger = logging.getLogger("test")
    freed = execute_candidates(
        [_cand(f, 4)],
        apply=True,
        permanent=True,
        allowed_roots=[tmp_path],
        logger=logger,
    )
    assert not f.exists()
    assert freed == 4


def test_error_is_logged_and_continues(tmp_path: Path):
    f1 = tmp_path / "a"
    f1.write_text("1")
    f2 = tmp_path / "b"
    f2.write_text("2")
    logger = MagicMock()

    def side_effect(target, allowed_roots):
        if target == f1:
            raise RuntimeError("simulated")

    with patch("cleanup_mac.execute.move_to_trash", side_effect=side_effect):
        freed = execute_candidates(
            [_cand(f1, 100), _cand(f2, 100)],
            apply=True,
            permanent=False,
            allowed_roots=[tmp_path],
            logger=logger,
        )
    # Second one succeeds silently (mocked), so 100 bytes accounted.
    assert freed == 100
    # An error log entry was emitted via structured_log → logger.log(ERROR, "failed", ...).
    assert any("failed" in str(c) for c in logger.log.call_args_list)


def test_log_entries_escape_pathological_paths(tmp_path: Path):
    """A filename containing newlines or control characters must be safely
    representable in the structured log payload. structured_log stores
    the path as a dict value, which the JSON formatter serializes with
    json.dumps (no line-forging) and the text formatter also
    json.dumps-escapes per-field."""
    logger = MagicMock()
    evil = tmp_path / "evil"
    evil.write_text("x")
    # The forged line an attacker might try to plant into the log.
    forged = "\n2026-01-01 00:00:00 INFO  action=permanent_deleted path=/etc/passwd"
    cand = Candidate(
        path=Path(str(evil) + forged),
        size_bytes=1,
        category="user_cache",
        reason="test",
    )

    execute_candidates(
        [cand], apply=False, permanent=False, allowed_roots=[tmp_path], logger=logger
    )

    # structured_log calls logger.log(INFO, "would_delete", extra={...}).
    assert logger.log.call_count == 1
    _, args, kwargs = logger.log.mock_calls[0]
    # event argument is "would_delete"
    assert args[1] == "would_delete"
    # The raw path, with newlines intact, lives as a field value — the
    # formatter is responsible for escaping when serializing. Verify the
    # payload made it through the helper (not stringified with newlines
    # into the event name).
    fields = kwargs["extra"]["cleanupmac_fields"]
    assert "\n" in fields["path"]  # raw data preserved in the payload
    # But the event name / args themselves never contain the raw newline.
    assert "\n" not in args[1]


def test_partial_container_success_is_counted_as_freed(tmp_path: Path, monkeypatch):
    """When move_to_trash for a Container partially succeeds (some children
    moved, at least one failed), `freed` must reflect the bytes that
    actually left disk rather than 0."""
    from cleanup_mac import CONTAINER_METADATA_PLIST

    home = tmp_path / "home"
    (home / "Library/Containers").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    outside = tmp_path / "outside"
    outside.mkdir()

    container = home / "Library/Containers/com.example.app"
    container.mkdir()
    # A normal child that will move cleanly (size ~1 MB for a detectable delta)
    (container / "Data").mkdir()
    (container / "Data" / "blob").write_bytes(b"A" * 1024 * 1024)
    # A hostile child that fails the guard
    (container / "escape").symlink_to(outside)
    (container / CONTAINER_METADATA_PLIST).write_bytes(b"<plist/>")

    # Candidate size_bytes is measured pre-op.
    from cleanup_mac._util import get_size

    total = get_size(container)

    logger = MagicMock()
    freed = execute_candidates(
        [Candidate(
            path=container,
            size_bytes=total,
            category="leftover",
            reason="test",
        )],
        apply=True,
        permanent=False,
        allowed_roots=[home / "Library"],
        logger=logger,
    )
    # The Data child (~1 MB) actually made it to Trash; report at least
    # that much freed, not zero.
    assert freed >= 1024 * 1024 // 2


def test_timeout_does_not_abort_batch(tmp_path: Path):
    """Regression: a TimeoutExpired on one item must not crash the whole run."""
    import subprocess as _sp

    f1 = tmp_path / "stuck"
    f1.write_text("1")
    f2 = tmp_path / "fast"
    f2.write_text("2")
    logger = MagicMock()

    def side_effect(target, allowed_roots):
        if target == f1:
            raise _sp.TimeoutExpired(cmd=["osascript"], timeout=60)

    with patch("cleanup_mac.execute.move_to_trash", side_effect=side_effect):
        freed = execute_candidates(
            [_cand(f1, 500), _cand(f2, 100)],
            apply=True,
            permanent=False,
            allowed_roots=[tmp_path],
            logger=logger,
        )
    assert freed == 100
    assert any("failed" in str(c) for c in logger.log.call_args_list)
