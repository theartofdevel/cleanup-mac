"""Tests for path-safety primitives."""

from __future__ import annotations

from pathlib import Path

from cleanup_mac import (
    NEVER_TOUCH_ABSOLUTE,
    NEVER_TOUCH_RELATIVE_TO_HOME,
    is_never_touch,
    is_path_under,
)


def test_is_path_under_direct_child(tmp_path: Path):
    parent = tmp_path / "a"
    parent.mkdir()
    child = parent / "b"
    child.mkdir()
    assert is_path_under(child, [parent]) is True


def test_is_path_under_rejects_sibling(tmp_path: Path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert is_path_under(tmp_path / "b", [tmp_path / "a"]) is False


def test_is_path_under_resolves_symlinks(tmp_path: Path):
    """A symlink pointing outside allowed roots must be rejected."""
    outside = tmp_path / "outside"
    outside.mkdir()
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    link = allowed / "link"
    link.symlink_to(outside)
    assert is_path_under(link, [allowed]) is False


def test_never_touch_system_path():
    assert is_never_touch(Path("/System/Library/CoreServices/Finder.app")) is True


def test_never_touch_keychains(fake_home: Path):
    kc = fake_home / "Library/Keychains"
    kc.mkdir()
    assert is_never_touch(kc) is True


def test_never_touch_allows_normal_cache(fake_home: Path):
    cache = fake_home / "Library/Caches/com.example.app"
    cache.mkdir()
    assert is_never_touch(cache) is False


def test_never_touch_apple_container(fake_home: Path):
    c = fake_home / "Library/Containers/com.apple.Safari"
    c.mkdir()
    assert is_never_touch(c) is True


def test_never_touch_self_log_dir(fake_home: Path):
    d = fake_home / "Library/Logs/cleanup-mac"
    d.mkdir()
    assert is_never_touch(d) is True


def test_never_touch_constants_nonempty():
    assert NEVER_TOUCH_ABSOLUTE
    assert NEVER_TOUCH_RELATIVE_TO_HOME


def test_never_touch_cloud_storage(fake_home: Path):
    """Third-party cloud sync providers (iCloud Drive, Dropbox, OneDrive,
    Google Drive) mount under ~/Library/CloudStorage. Deleting there
    triggers sync and removes the remote copy — catastrophic."""
    p = fake_home / "Library/CloudStorage/iCloud Drive/Documents"
    p.mkdir(parents=True)
    assert is_never_touch(p) is True


def test_never_touch_autosave(fake_home: Path):
    """NSDocument autosave holds unsaved work from TextEdit/Pages/Preview."""
    p = fake_home / "Library/Autosave Information/com.example.App"
    p.mkdir(parents=True)
    assert is_never_touch(p) is True


def test_never_touch_accounts(fake_home: Path):
    """Account credentials (iCloud, iMessage token) under Accounts."""
    p = fake_home / "Library/Accounts"
    p.mkdir()
    assert is_never_touch(p) is True


def test_never_touch_address_book(fake_home: Path):
    p = fake_home / "Library/Application Support/AddressBook"
    p.mkdir(parents=True)
    assert is_never_touch(p) is True


def test_never_touch_call_history(fake_home: Path):
    p = fake_home / "Library/Application Support/CallHistoryDB"
    p.mkdir(parents=True)
    assert is_never_touch(p) is True


def test_is_owned_by_current_uid_true(tmp_path):
    """Files the test creates are owned by the current effective UID."""
    from cleanup_mac.safety import is_owned_by_current_uid

    f = tmp_path / "mine"
    f.write_text("x")
    assert is_owned_by_current_uid(f) is True


def test_is_owned_by_current_uid_false_for_other_uid(tmp_path, monkeypatch):
    """Simulate sudo: euid == 0 but file owned by test user."""
    from cleanup_mac.safety import is_owned_by_current_uid

    f = tmp_path / "not-mine"
    f.write_text("x")
    monkeypatch.setattr("os.geteuid", lambda: 99999)
    assert is_owned_by_current_uid(f) is False
