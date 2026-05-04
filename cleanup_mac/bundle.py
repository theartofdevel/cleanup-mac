"""Bundle-ID recognition and installed-app indexing for scan_leftovers."""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
from pathlib import Path

BUNDLE_ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9-]*(\.[a-zA-Z0-9-]+){1,}$")

_STRIPPABLE_SUFFIXES = (".plist", ".savedState")


def is_bundle_id(name: str) -> bool:
    return bool(BUNDLE_ID_RE.match(base_id(name)))


def base_id(name: str) -> str:
    for suffix in _STRIPPABLE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


DEFAULT_APP_ROOTS: tuple[Path, ...] = (
    Path("/Applications"),
    Path.home() / "Applications",
    Path("/System/Applications"),
)

_BUNDLE_SUFFIXES = (".app", ".appex", ".xpc")
_NESTED_BUNDLE_DIRS = (
    "Contents/Applications",
    "Contents/Helpers",
    "Contents/PlugIns",
    "Contents/XPCServices",
)
_CODESIGN_BIN = "/usr/bin/codesign"


def _walk_apps(root: Path):
    """Yield application-owned bundle paths under `root`.

    Recurses into plain directories (e.g. /Applications/Utilities) and
    into selected bundle subdirectories for nested apps, app extensions,
    and XPC services. Does not descend into Frameworks or Resources.
    """
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            it = os.scandir(current)
        except OSError:
            continue
        with it:
            for entry in it:
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if not is_dir:
                    continue
                name = entry.name
                if name.endswith(_BUNDLE_SUFFIXES):
                    yield Path(entry.path)
                    for sub in _NESTED_BUNDLE_DIRS:
                        stack.append(Path(entry.path) / sub)
                elif "." not in name:
                    stack.append(Path(entry.path))


def get_installed_bundle_ids(roots: list[Path] | None = None) -> set[str]:
    if roots is None:
        roots = [p for p in DEFAULT_APP_ROOTS if p.exists()]

    ids: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for app_path in _walk_apps(root):
            plist_path = app_path / "Contents" / "Info.plist"
            if not plist_path.is_file():
                continue
            try:
                with open(plist_path, "rb") as f:
                    data = plistlib.load(f)
            except (OSError, plistlib.InvalidFileException, ValueError):
                continue
            bid = data.get("CFBundleIdentifier")
            if isinstance(bid, str) and bid:
                ids.add(bid)
            ids.update(_get_application_group_ids(app_path))
    return ids


def _get_application_group_ids(app_path: Path) -> set[str]:
    try:
        proc = subprocess.run(
            [_CODESIGN_BIN, "-d", "--entitlements", ":-", str(app_path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return set()
    if proc.returncode != 0 or not proc.stdout:
        return set()
    try:
        entitlements = plistlib.loads(proc.stdout)
    except (plistlib.InvalidFileException, ValueError):
        return set()
    groups = entitlements.get("com.apple.security.application-groups")
    if not isinstance(groups, list):
        return set()
    return {group for group in groups if isinstance(group, str) and is_bundle_id(group)}
