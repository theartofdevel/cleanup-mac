"""End-to-end tests for main()."""

from __future__ import annotations

import os
import time
from pathlib import Path

from cleanup_mac import main


def _aged(path: Path, days: int) -> None:
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def test_main_dry_run_exits_zero(fake_home: Path, capsys, monkeypatch):
    leftover = fake_home / "Library/Application Support/com.example.Gone"
    leftover.mkdir()
    (leftover / "data.bin").write_bytes(b"0" * 2 * 1024 * 1024)
    _aged(leftover, 30)

    # No /Applications fixture — installed_ids empty → leftover detected.
    monkeypatch.setattr("cleanup_mac.bundle.DEFAULT_APP_ROOTS", (Path("/nonexistent"),))
    monkeypatch.setattr("cleanup_mac.cli.scan_package_managers", lambda aggressive=False: [])
    rc = main(["--no-log"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "com.example.Gone" in out
    assert "DRY RUN" in out


def test_main_only_flag_scopes_scanners(fake_home: Path, capsys, monkeypatch):
    leftover = fake_home / "Library/Application Support/com.example.Gone"
    leftover.mkdir()
    (leftover / "data.bin").write_bytes(b"0" * 2 * 1024 * 1024)
    _aged(leftover, 30)

    cache = fake_home / "Library/Caches/com.example.Cache"
    cache.mkdir()
    (cache / "data.bin").write_bytes(b"0" * 2 * 1024 * 1024)
    _aged(cache, 30)

    monkeypatch.setattr("cleanup_mac.bundle.DEFAULT_APP_ROOTS", (Path("/nonexistent"),))
    monkeypatch.setattr("cleanup_mac.cli.scan_package_managers", lambda aggressive=False: [])
    rc = main(["--no-log", "--only", "leftover"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "com.example.Gone" in out
    assert "com.example.Cache" in out


def test_main_keyboard_interrupt_returns_3(fake_home, monkeypatch, capsys):
    def boom(*a, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr("cleanup_mac.cli.scan_user_caches", boom)
    monkeypatch.setattr("cleanup_mac.bundle.DEFAULT_APP_ROOTS", (Path("/nonexistent"),))
    rc = main(["--no-log"])
    assert rc == 3


def test_main_shows_banner_before_scanning(fake_home, monkeypatch, capsys):
    """Every interactive run must announce the mode and filters up front,
    so the user knows whether they're about to delete or just scanning."""
    monkeypatch.setattr("cleanup_mac.bundle.DEFAULT_APP_ROOTS", (Path("/nonexistent"),))
    monkeypatch.setattr("cleanup_mac.cli.scan_package_managers", lambda aggressive=False: [])
    rc = main(["--no-log"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "Mode: DRY-RUN" in err
    assert "min-age=" in err
    assert "Scope:" in err


def test_main_json_mode_suppresses_banner(fake_home, monkeypatch, capsys):
    """--json is for machine consumers; the banner would corrupt stderr-
    free stdout use-cases (e.g. `cleanup-mac --json | jq`)."""
    monkeypatch.setattr("cleanup_mac.bundle.DEFAULT_APP_ROOTS", (Path("/nonexistent"),))
    monkeypatch.setattr("cleanup_mac.cli.scan_package_managers", lambda aggressive=False: [])
    rc = main(["--no-log", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Mode: DRY-RUN" not in captured.err
    assert "Mode: DRY-RUN" not in captured.out
    # stdout must still be valid JSON.
    import json as _json
    _json.loads(captured.out)


def test_main_apply_without_tty_refuses(fake_home, monkeypatch, capsys):
    """Destructive --apply without a TTY and without --yes must refuse.
    A script that forgot --yes shouldn't silently start deleting."""
    monkeypatch.setattr("cleanup_mac.bundle.DEFAULT_APP_ROOTS", (Path("/nonexistent"),))
    monkeypatch.setattr("cleanup_mac.cli.scan_package_managers", lambda aggressive=False: [])
    # pytest's captured stdin is not a TTY.
    rc = main(["--no-log", "--apply"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "not a TTY" in err or "cannot prompt" in err


def test_main_apply_with_yes_proceeds(fake_home, monkeypatch, capsys):
    """--yes is the escape hatch for scripting a destructive run."""
    leftover = fake_home / "Library/Application Support/com.example.Gone"
    leftover.mkdir()
    (leftover / "data.bin").write_bytes(b"0" * 2 * 1024 * 1024)
    _aged(leftover, 30)

    monkeypatch.setattr("cleanup_mac.bundle.DEFAULT_APP_ROOTS", (Path("/nonexistent"),))
    monkeypatch.setattr("cleanup_mac.cli.scan_package_managers", lambda aggressive=False: [])
    rc = main(["--no-log", "--apply", "--yes"])
    assert rc == 0
    # The leftover must actually have been moved (to ~/.Trash under fake_home).
    assert not leftover.exists()


def test_main_interactive_without_tty_refuses(fake_home, monkeypatch, capsys):
    """`-i` needs a TTY for its per-category prompts. Without one, silently
    "proceeding" would degenerate into a confusing no-op as every prompt
    defaulted to N. Refuse explicitly instead."""
    monkeypatch.setattr("cleanup_mac.bundle.DEFAULT_APP_ROOTS", (Path("/nonexistent"),))
    monkeypatch.setattr("cleanup_mac.cli.scan_package_managers", lambda aggressive=False: [])
    rc = main(["--no-log", "-i"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "not a TTY" in err or "cannot confirm" in err


def test_main_dry_run_via_tty_prompt_accepts_enter(fake_home, monkeypatch, capsys):
    """Dry-run in a TTY prompts with [Y/n] — hitting Enter accepts."""
    import io

    leftover = fake_home / "Library/Application Support/com.example.Gone"
    leftover.mkdir()
    (leftover / "data.bin").write_bytes(b"0" * 2 * 1024 * 1024)
    _aged(leftover, 30)

    # Fake a TTY stdin that returns empty line (user pressed Enter).
    class _TTYStdin(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("cleanup_mac.bundle.DEFAULT_APP_ROOTS", (Path("/nonexistent"),))
    monkeypatch.setattr("cleanup_mac.cli.scan_package_managers", lambda aggressive=False: [])
    monkeypatch.setattr("sys.stdin", _TTYStdin("\n"))
    rc = main(["--no-log"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "Proceed? [Y/n]" in err


def test_main_dry_run_via_tty_prompt_rejects_n(fake_home, monkeypatch, capsys):
    """Typing 'n' at the prompt aborts with exit 3 — nothing scanned."""
    import io

    class _TTYStdin(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("cleanup_mac.bundle.DEFAULT_APP_ROOTS", (Path("/nonexistent"),))
    monkeypatch.setattr("cleanup_mac.cli.scan_package_managers", lambda aggressive=False: [])
    monkeypatch.setattr("sys.stdin", _TTYStdin("n\n"))
    rc = main(["--no-log"])
    assert rc == 3
    # Scanner line would follow the prompt on success; ensure it did not run.
    err = capsys.readouterr().err
    assert "Scanning for cleanup candidates" not in err
