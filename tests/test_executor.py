"""Tests for move_to_trash and delete_permanent."""

from __future__ import annotations

from pathlib import Path

import pytest

from cleanup_mac import (
    CONTAINER_METADATA_PLIST,
    _path_fingerprint,
    _verify_unchanged,
    delete_permanent,
    move_to_trash,
)


def test_delete_permanent_refuses_outside_allowed_roots(tmp_path: Path):
    """Any path not under ALLOWED_ROOTS must raise."""
    target = tmp_path / "random"
    target.mkdir()
    with pytest.raises(PermissionError):
        delete_permanent(target, allowed_roots=[tmp_path / "other"])


def test_delete_permanent_refuses_never_touch(tmp_path: Path, monkeypatch):
    """Never-touch wins over allowed_roots."""
    home = tmp_path / "home"
    (home / "Library/Keychains").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    target = home / "Library/Keychains/foo"
    target.write_text("x")
    with pytest.raises(PermissionError):
        delete_permanent(target, allowed_roots=[home])


def test_delete_permanent_removes_file(tmp_path: Path):
    f = tmp_path / "x.txt"
    f.write_text("data")
    delete_permanent(f, allowed_roots=[tmp_path])
    assert not f.exists()


def test_delete_permanent_removes_directory(tmp_path: Path):
    d = tmp_path / "sub"
    d.mkdir()
    (d / "x").write_text("1")
    delete_permanent(d, allowed_roots=[tmp_path])
    assert not d.exists()


def test_move_to_trash_moves_file_to_trash_dir(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    f = tmp_path / "x.txt"
    f.write_text("data")
    move_to_trash(f, allowed_roots=[tmp_path])

    trashed = home / ".Trash" / "x.txt"
    assert not f.exists()
    assert trashed.exists()
    assert trashed.read_text() == "data"


def test_move_to_trash_moves_directory(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    d = tmp_path / "sub"
    d.mkdir()
    (d / "inner.txt").write_text("x")
    move_to_trash(d, allowed_roots=[tmp_path])

    trashed = home / ".Trash" / "sub"
    assert not d.exists()
    assert (trashed / "inner.txt").read_text() == "x"


def test_move_to_trash_handles_name_collision(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    # Pre-existing item in Trash with the same name.
    trash = home / ".Trash"
    trash.mkdir()
    (trash / "x.txt").write_text("old")

    f = tmp_path / "x.txt"
    f.write_text("new")
    move_to_trash(f, allowed_roots=[tmp_path])

    # Original preserved, new one got a timestamped name.
    assert (trash / "x.txt").read_text() == "old"
    new_entries = [p for p in trash.iterdir() if p.name != "x.txt"]
    assert len(new_entries) == 1
    assert new_entries[0].name.startswith("x.txt ")
    assert new_entries[0].read_text() == "new"


def test_move_to_trash_path_safety(tmp_path: Path):
    target = tmp_path / "evil"
    target.mkdir()
    with pytest.raises(PermissionError):
        move_to_trash(target, allowed_roots=[tmp_path / "other"])


def test_move_to_trash_container_moves_contents_keeps_metadata(
    tmp_path: Path, monkeypatch
):
    """Container's Data/ etc. move to Trash; SIP metadata plist stays."""
    home = tmp_path / "home"
    (home / "Library/Containers").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    container = home / "Library/Containers/com.example.app"
    container.mkdir()
    (container / "Data").mkdir()
    (container / "Data" / "user.dat").write_text("payload")
    (container / CONTAINER_METADATA_PLIST).write_bytes(b"<plist/>")

    move_to_trash(container, allowed_roots=[home])

    # Container shell + metadata still there — sandbox/SIP wouldn't let us
    # touch them, and that's fine because the stub is tiny.
    assert container.exists()
    assert (container / CONTAINER_METADATA_PLIST).exists()
    assert not (container / "Data").exists()

    # Contents are in Trash, namespaced so different Containers don't collide.
    trashed = home / ".Trash" / "com.example.app--Data"
    assert trashed.is_dir()
    assert (trashed / "user.dat").read_text() == "payload"


def test_move_to_trash_group_container_same_strategy(tmp_path: Path, monkeypatch):
    """Group Containers don't have a Data/ subdir — move each top-level child."""
    home = tmp_path / "home"
    (home / "Library/Group Containers").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    gc = home / "Library/Group Containers/group.example"
    gc.mkdir()
    (gc / "Library").mkdir()
    (gc / "Library" / "prefs.plist").write_text("x")
    (gc / "appstore").mkdir()
    (gc / "appstore" / "blob").write_text("y")
    (gc / CONTAINER_METADATA_PLIST).write_bytes(b"<plist/>")

    move_to_trash(gc, allowed_roots=[home])

    assert gc.exists()
    assert (gc / CONTAINER_METADATA_PLIST).exists()
    assert not (gc / "Library").exists()
    assert not (gc / "appstore").exists()
    assert (home / ".Trash" / "group.example--Library").is_dir()
    assert (home / ".Trash" / "group.example--appstore").is_dir()


def test_delete_permanent_container_keeps_metadata(tmp_path: Path, monkeypatch):
    """Permanent delete on a Container removes contents but leaves the plist."""
    home = tmp_path / "home"
    (home / "Library/Containers").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    container = home / "Library/Containers/com.example.app"
    container.mkdir()
    (container / "Data").mkdir()
    (container / "Data" / "user.dat").write_text("payload")
    (container / CONTAINER_METADATA_PLIST).write_bytes(b"<plist/>")

    delete_permanent(container, allowed_roots=[home])

    assert container.exists()
    assert (container / CONTAINER_METADATA_PLIST).exists()
    assert not (container / "Data").exists()


def test_move_to_trash_container_refuses_child_escaping_allowed_roots(
    tmp_path: Path, monkeypatch
):
    """A symlinked child whose resolved target escapes allowed_roots must
    be refused by the guard — enforces CONTRIBUTING.md §5 invariant 3
    even during Container iteration."""
    home = tmp_path / "home"
    (home / "Library/Containers").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    # A path that exists but is outside allowed_roots.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sensitive.txt").write_text("do not touch")

    container = home / "Library/Containers/com.example.app"
    container.mkdir()
    (container / "Data").mkdir()
    (container / "Data" / "user.dat").write_text("payload")
    # Hostile plant: a symlink child pointing out of the allowed tree.
    (container / "escape").symlink_to(outside)
    (container / CONTAINER_METADATA_PLIST).write_bytes(b"<plist/>")

    with pytest.raises(RuntimeError) as exc:
        move_to_trash(container, allowed_roots=[home / "Library"])

    # The error message mentions the escaping child.
    assert "escape" in str(exc.value)

    # Other children processed normally — partial success.
    trashed_data = home / ".Trash" / "com.example.app--Data"
    assert trashed_data.exists()
    # Outside target is untouched.
    assert (outside / "sensitive.txt").read_text() == "do not touch"
    assert outside.exists()


def test_delete_permanent_container_refuses_child_escaping_allowed_roots(
    tmp_path: Path, monkeypatch
):
    home = tmp_path / "home"
    (home / "Library/Containers").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sensitive.txt").write_text("keep me")

    container = home / "Library/Containers/com.example.app"
    container.mkdir()
    (container / "Data").mkdir()
    (container / "Data" / "user.dat").write_text("payload")
    (container / "escape").symlink_to(outside)
    (container / CONTAINER_METADATA_PLIST).write_bytes(b"<plist/>")

    with pytest.raises(RuntimeError) as exc:
        delete_permanent(container, allowed_roots=[home / "Library"])

    assert "escape" in str(exc.value)
    # Other children processed.
    assert not (container / "Data").exists()
    # Outside target untouched.
    assert (outside / "sensitive.txt").read_text() == "keep me"


def test_verify_unchanged_detects_path_replacement(tmp_path: Path):
    """If the file at `path` is swapped (unlink + re-create) between
    fingerprint capture and verify, _verify_unchanged must refuse."""
    target = tmp_path / "victim"
    target.write_text("original")
    fp = _path_fingerprint(target)

    # Attacker swaps: remove original, plant something else at the same path.
    target.unlink()
    target.write_text("swapped")

    with pytest.raises(PermissionError) as exc:
        _verify_unchanged(target, fp)
    assert "replaced" in str(exc.value)


def test_verify_unchanged_detects_path_vanish(tmp_path: Path):
    target = tmp_path / "victim"
    target.write_text("x")
    fp = _path_fingerprint(target)
    target.unlink()

    with pytest.raises(PermissionError) as exc:
        _verify_unchanged(target, fp)
    assert "vanished" in str(exc.value)


def test_runtime_error_message_reports_success_count(tmp_path: Path, monkeypatch):
    """On partial container failure, the RuntimeError must surface both
    the failure count and the success count — an observer inspecting the
    audit log should know `7 moved, 3 failed`, not just `3 failed`."""
    home = tmp_path / "home"
    (home / "Library/Containers").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    outside = tmp_path / "outside"
    outside.mkdir()

    container = home / "Library/Containers/com.example.app"
    container.mkdir()
    # Two ordinary children that should succeed…
    (container / "Data").mkdir()
    (container / "Data" / "x").write_text("a")
    (container / "Cache").mkdir()
    (container / "Cache" / "y").write_text("b")
    # …and one that escapes allowed_roots and must fail.
    (container / "escape").symlink_to(outside)
    (container / CONTAINER_METADATA_PLIST).write_bytes(b"<plist/>")

    with pytest.raises(RuntimeError) as exc:
        move_to_trash(container, allowed_roots=[home / "Library"])

    msg = str(exc.value)
    # Both counts must be visible for audit-log observers.
    assert "2 moved" in msg
    assert "1 failed" in msg or "1 item" in msg


def test_verify_unchanged_passes_for_same_entry(tmp_path: Path):
    target = tmp_path / "victim"
    target.write_text("x")
    fp = _path_fingerprint(target)
    # No mutation — should pass silently.
    _verify_unchanged(target, fp)


def test_move_to_trash_container_refuses_child_pointing_at_never_touch(
    tmp_path: Path, monkeypatch
):
    """Even if a child is under allowed_roots after resolve, if it resolves
    into a NEVER_TOUCH path (e.g. Keychains) the guard must refuse it."""
    home = tmp_path / "home"
    (home / "Library/Keychains").mkdir(parents=True)
    (home / "Library/Containers").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    # Plant a Keychain victim file so the symlink target actually exists.
    (home / "Library/Keychains/login.keychain-db").write_text("secret")

    container = home / "Library/Containers/com.example.app"
    container.mkdir()
    (container / "Data").mkdir()
    (container / "Data" / "user.dat").write_text("payload")
    (container / "kc").symlink_to(home / "Library/Keychains")
    (container / CONTAINER_METADATA_PLIST).write_bytes(b"<plist/>")

    with pytest.raises(RuntimeError) as exc:
        move_to_trash(container, allowed_roots=[home])

    assert "kc" in str(exc.value)
    # Keychain still there.
    assert (home / "Library/Keychains/login.keychain-db").read_text() == "secret"
