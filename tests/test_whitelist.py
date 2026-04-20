"""Tests for whitelist scan-skip logic."""

from __future__ import annotations

from pathlib import Path

from cleanup_mac import BUILTIN_WHITELIST, is_in_whitelist, load_whitelist


def test_builtin_includes_apple():
    wl = load_whitelist(None)
    assert is_in_whitelist("com.apple.Safari", wl)
    assert is_in_whitelist("com.apple.nsurlsessiond", wl)


def test_builtin_includes_group_apple():
    wl = load_whitelist(None)
    assert is_in_whitelist("group.com.apple.mail", wl)


def test_builtin_includes_adobe_microsoft():
    wl = load_whitelist(None)
    assert is_in_whitelist("Adobe", wl)
    assert is_in_whitelist("Microsoft Office", wl)


def test_builtin_does_not_match_arbitrary():
    wl = load_whitelist(None)
    assert not is_in_whitelist("com.spotify.client", wl)
    assert not is_in_whitelist("SomeRandomApp", wl)


def test_user_ignore_file_is_applied(tmp_path: Path):
    ignore = tmp_path / "ignore.txt"
    ignore.write_text("com.example.*\n# a comment\n\nVeryCustomApp\n")
    wl = load_whitelist(ignore)
    assert is_in_whitelist("com.example.foo", wl)
    assert is_in_whitelist("VeryCustomApp", wl)
    assert not is_in_whitelist("com.other.app", wl)


def test_builtin_constant_nonempty():
    assert len(BUILTIN_WHITELIST) >= 5


def test_whitelist_matches_across_unicode_normalizations():
    """A folder name stored in NFD (HFS+) must match an NFC-typed pattern."""
    import unicodedata

    nfc_pattern = "café*"
    nfd_name = unicodedata.normalize("NFD", "café-cache")
    assert nfc_pattern != nfd_name  # different byte strings
    assert is_in_whitelist(nfd_name, (nfc_pattern,))


def test_never_touch_matches_across_unicode_normalizations(tmp_path, monkeypatch):
    """If a home-relative never-touch path contains composed chars, an NFD
    equivalent from the filesystem must still match. Current NEVER_TOUCH
    rules are ASCII-only so this is defence-in-depth for future additions."""
    import unicodedata

    from cleanup_mac import is_never_touch

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    nfd = unicodedata.normalize("NFD", "café-secret")
    (home / "Library").mkdir()
    (home / "Library" / nfd).mkdir()

    monkeypatch.setattr(
        "cleanup_mac.safety.NEVER_TOUCH_RELATIVE_TO_HOME",
        ("Library/café-secret",),  # NFC form
    )

    assert is_never_touch(home / "Library" / nfd)
