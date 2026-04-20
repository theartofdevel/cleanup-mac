"""Bundle-ID recognition and installed-app indexing for scan_leftovers."""

from __future__ import annotations

import os
import plistlib
import re
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

_NESTED_APP_DIRS = ("Contents/Applications", "Contents/Helpers")


def _walk_apps(root: Path):
    """Yield .app paths under `root`.

    Recurses into plain directories (e.g. /Applications/Utilities) and
    into an .app's Contents/Applications and Contents/Helpers for
    nested first-class apps (Xcode → Instruments). Does not descend
    into Frameworks, PlugIns, XPCServices, Resources.
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
                if name.endswith(".app"):
                    yield Path(entry.path)
                    for sub in _NESTED_APP_DIRS:
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
    return ids
