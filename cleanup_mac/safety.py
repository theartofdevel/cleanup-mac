"""Path-safety primitives. Every deletion must go through
`_guard_deletion` + `_path_fingerprint` + `_verify_unchanged`."""

from __future__ import annotations

import fnmatch
import os
import unicodedata
from pathlib import Path

NEVER_TOUCH_ABSOLUTE: tuple[str, ...] = (
    "/System",
    "/Library/Apple",
    "/Library/Developer/CommandLineTools",
)

NEVER_TOUCH_RELATIVE_TO_HOME: tuple[str, ...] = (
    "Library/Mobile Documents",              # iCloud Drive
    "Library/Application Support/MobileSync",  # iOS backups
    "Library/Keychains",
    "Library/Mail",
    "Library/Messages",
    "Library/Safari",
    "Library/Cookies",
    "Library/Passwords",
    "Library/Logs/cleanup-mac",              # our own audit log
    "Library/CloudStorage",                  # iCloud/Dropbox/OneDrive mounts
    "Library/Autosave Information",          # unsaved NSDocument state
    "Library/Accounts",
    "Library/IdentityServices",
    "Library/HomeKit",
    "Library/Metadata/CoreSpotlight",
    "Library/Metadata/CoreDuet",
    "Library/Application Support/AddressBook",
    "Library/Application Support/CallHistoryDB",
    "Library/Application Support/CallHistoryTransactions",
    "Library/Application Support/CloudDocs",
    "Library/Suggestions",
    "Library/PersonalizationPortrait",
)

NEVER_TOUCH_HOME_PATTERNS: tuple[str, ...] = (
    "Library/Containers/com.apple.",
    "Library/Group Containers/group.com.apple.",
)


def is_owned_by_current_uid(path: Path) -> bool:
    """True iff `path` is owned by the current euid (no symlink follow).
    Used under sudo to avoid touching another user's files."""
    try:
        return path.lstat().st_uid == os.geteuid()
    except OSError:
        return False


def is_path_under(child: Path, parents: list[Path]) -> bool:
    """True iff the resolved `child` is at or below any resolved parent.
    Symlinks are followed; links escaping all parents are rejected."""
    try:
        resolved_child = child.resolve()
    except (OSError, RuntimeError):
        return False
    for p in parents:
        try:
            resolved_parent = p.resolve()
        except (OSError, RuntimeError):
            continue
        if resolved_child == resolved_parent:
            return True
        if resolved_child.is_relative_to(resolved_parent):
            return True
    return False


def _nfc(s: str) -> str:
    # HFS+ stores NFD, APFS preserves whatever was written. Normalize
    # both sides of every comparison to NFC so safety rules don't depend
    # on filesystem encoding.
    return unicodedata.normalize("NFC", s)


def is_never_touch(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return True  # fail closed
    s = _nfc(str(resolved))
    for prefix in NEVER_TOUCH_ABSOLUTE:
        if s == prefix or s.startswith(prefix + "/"):
            return True
    home = _nfc(str(Path.home().resolve()))
    for rel in NEVER_TOUCH_RELATIVE_TO_HOME:
        full = _nfc(f"{home}/{rel}")
        if s == full or s.startswith(full + "/"):
            return True
    return any(
        s.startswith(_nfc(f"{home}/{pat}")) for pat in NEVER_TOUCH_HOME_PATTERNS
    )


BUILTIN_WHITELIST: tuple[str, ...] = (
    "com.apple.*",
    "group.com.apple.*",
    "iCloud*",
    "Adobe*",
    "Microsoft*",
    "Google/Chrome",
    "JetBrains",
    "SyncedPreferences",
    "CrashReporter",
)


def load_whitelist(user_ignore_file: Path | None) -> tuple[str, ...]:
    patterns = list(BUILTIN_WHITELIST)
    if user_ignore_file and user_ignore_file.is_file():
        for raw in user_ignore_file.read_text().splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                patterns.append(line)
    return tuple(patterns)


def is_in_whitelist(name: str, whitelist: tuple[str, ...]) -> bool:
    nname = _nfc(name)
    return any(fnmatch.fnmatchcase(nname, _nfc(pat)) for pat in whitelist)


def default_allowed_roots(aggressive: bool = False) -> list[Path]:
    home = Path.home()
    roots = [
        home / "Library/Caches",
        home / "Library/Logs",
        home / "Library/Application Support",
        home / "Library/Containers",
        home / "Library/Group Containers",
        home / "Library/Preferences",
        home / "Library/LaunchAgents",
        home / "Library/Saved Application State",
        home / "Library/Developer",
    ]
    if aggressive:
        roots.extend(
            [
                Path("/Library/Caches"),
                Path("/private/var/folders"),
                Path("/private/tmp"),
                Path("/tmp"),
            ]
        )
    return roots


ALLOWED_ROOTS_FOR_DELETION: list[Path] = []


def _guard_deletion(target: Path, allowed_roots: list[Path]) -> Path:
    """Resolve `target` and raise PermissionError if it points into
    never-touch territory or outside the allowed roots.

    Callers MUST pair this with `_path_fingerprint` + `_verify_unchanged`
    right before the destructive syscall to close the TOCTOU window.
    """
    resolved = target.resolve()
    if is_never_touch(resolved):
        raise PermissionError(f"never-touch path: {resolved}")
    if not is_path_under(resolved, allowed_roots):
        raise PermissionError(f"path outside allowed roots: {resolved}")
    return resolved


def _path_fingerprint(path: Path) -> tuple[int, int]:
    """Return (device, inode) for TOCTOU detection. lstat — no follow."""
    st = os.lstat(path)
    return (st.st_dev, st.st_ino)


def _verify_unchanged(path: Path, expected: tuple[int, int]) -> None:
    """Raise if `path` no longer identifies the fingerprinted entry —
    some other process swapped it between guard and syscall."""
    try:
        actual = _path_fingerprint(path)
    except FileNotFoundError as e:
        raise PermissionError(f"path vanished during delete: {path}") from e
    if actual != expected:
        raise PermissionError(
            f"path replaced during delete (inode/device changed): {path}"
        )
