"""Per-category scanners. Each returns a list[Candidate]; no side effects."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from cleanup_mac import _util
from cleanup_mac.bundle import base_id, is_bundle_id
from cleanup_mac.safety import is_in_whitelist, is_never_touch, is_owned_by_current_uid
from cleanup_mac.types import Candidate

# Absolute path — bare "lsof" would be vulnerable to PATH hijack.
LSOF_BIN = "/usr/sbin/lsof"

DIAGNOSTIC_REPORTS_MIN_AGE_DAYS = 30
XCODE_IOS_DEVICE_SUPPORT_MIN_AGE_DAYS = 90


def scan_user_caches(
    root: Path,
    whitelist: tuple[str, ...],
    min_age_days: int,
    min_size_bytes: int,
) -> list[Candidate]:
    if not root.is_dir():
        return []

    now = time.time()
    prelim: list[tuple[Path, float]] = []
    for entry in root.iterdir():
        if is_never_touch(entry):
            continue
        if is_in_whitelist(entry.name, whitelist):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        age_days = (now - mtime) / 86400
        if age_days < min_age_days:
            continue
        prelim.append((entry, age_days))

    sizes = _util.get_sizes([p for p, _ in prelim])

    results: list[Candidate] = []
    for entry, age_days in prelim:
        size = sizes.get(entry, 0)
        if size < min_size_bytes:
            continue
        results.append(
            Candidate(
                path=entry,
                size_bytes=size,
                category="user_cache",
                reason=f"cache dir age={age_days:.0f}d size={_util.format_bytes(size)}",
            )
        )
    return results


def scan_logs(
    root: Path,
    whitelist: tuple[str, ...],
    min_age_days: int,
    min_size_bytes: int,
) -> list[Candidate]:
    if not root.is_dir():
        return []

    now = time.time()
    prelim: list[tuple[Path, float]] = []
    for entry in root.iterdir():
        if is_never_touch(entry):
            continue
        if is_in_whitelist(entry.name, whitelist):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        age_days = (now - mtime) / 86400

        effective_min_age = min_age_days
        if entry.name == "DiagnosticReports":
            effective_min_age = max(min_age_days, DIAGNOSTIC_REPORTS_MIN_AGE_DAYS)
        if age_days < effective_min_age:
            continue
        prelim.append((entry, age_days))

    sizes = _util.get_sizes([p for p, _ in prelim])

    results: list[Candidate] = []
    for entry, age_days in prelim:
        size = sizes.get(entry, 0)
        if size < min_size_bytes:
            continue
        results.append(
            Candidate(
                path=entry,
                size_bytes=size,
                category="log",
                reason=f"log dir age={age_days:.0f}d size={_util.format_bytes(size)}",
            )
        )
    return results


DEFAULT_LEFTOVER_LOCATIONS: tuple[str, ...] = (
    "Library/Application Support",
    "Library/Containers",
    "Library/Group Containers",
    "Library/Preferences",
    "Library/LaunchAgents",
    "Library/Saved Application State",
)


def default_leftover_scan_locations() -> list[Path]:
    home = Path.home()
    return [home / sub for sub in DEFAULT_LEFTOVER_LOCATIONS]


def scan_leftovers(
    scan_locations: list[Path],
    installed_ids: set[str],
    whitelist: tuple[str, ...],
    min_age_days: int,
    min_size_bytes: int,
) -> list[Candidate]:
    """Data folders with a bundle-ID name whose app is no longer installed.
    Non-bundle-ID names are always skipped — false-negative bias."""
    now = time.time()
    prelim: list[tuple[Path, str]] = []
    for loc in scan_locations:
        if not loc.is_dir():
            continue
        for entry in loc.iterdir():
            if is_never_touch(entry):
                continue
            if is_in_whitelist(entry.name, whitelist):
                continue
            if not is_bundle_id(entry.name):
                continue
            bid = base_id(entry.name)
            if bid in installed_ids:
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            age_days = (now - mtime) / 86400
            if age_days < min_age_days:
                continue
            prelim.append((entry, bid))

    sizes = _util.get_sizes([p for p, _ in prelim])

    results: list[Candidate] = []
    for entry, bid in prelim:
        size = sizes.get(entry, 0)
        if size < min_size_bytes:
            continue
        results.append(
            Candidate(
                path=entry,
                size_bytes=size,
                category="leftover",
                reason=f"no app with bundle id {bid}",
            )
        )
    return results


def scan_xcode(
    developer_root: Path,
    min_age_days: int,
    min_size_bytes: int,
    aggressive: bool,
) -> list[Candidate]:
    now = time.time()
    prelim: list[tuple[Path, str]] = []

    def _consider(entry: Path, reason: str, effective_min_age: int) -> None:
        if is_never_touch(entry):
            return
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            return
        age_days = (now - mtime) / 86400
        if age_days < effective_min_age:
            return
        prelim.append((entry, reason))

    dd = developer_root / "Xcode/DerivedData"
    if dd.is_dir():
        # Respect --min-age: wiping DerivedData during an active
        # xcodebuild corrupts the in-progress build.
        for entry in dd.iterdir():
            _consider(entry, "Xcode DerivedData", min_age_days)

    if aggressive:
        arch_root = developer_root / "Xcode/Archives"
        if arch_root.is_dir():
            for day in arch_root.iterdir():
                if not day.is_dir():
                    continue
                for arch in day.iterdir():
                    # Users drop loose notes into Archives day folders;
                    # only real .xcarchive bundles are safe to remove.
                    if arch.suffix != ".xcarchive":
                        continue
                    _consider(arch, "Xcode Archive (aggressive)", min_age_days)

    ds = developer_root / "Xcode/iOS DeviceSupport"
    if ds.is_dir():
        for entry in ds.iterdir():
            _consider(
                entry,
                "iOS DeviceSupport older than 90 days",
                max(min_age_days, XCODE_IOS_DEVICE_SUPPORT_MIN_AGE_DAYS),
            )

    cs = developer_root / "CoreSimulator/Caches"
    if cs.is_dir():
        # Active simulators touch these paths; respect --min-age.
        for entry in cs.iterdir():
            _consider(entry, "CoreSimulator cache", min_age_days)

    sizes = _util.get_sizes([p for p, _ in prelim])

    results: list[Candidate] = []
    for entry, reason in prelim:
        size = sizes.get(entry, 0)
        if size < min_size_bytes:
            continue
        results.append(
            Candidate(path=entry, size_bytes=size, category="xcode", reason=reason)
        )
    return results


def scan_system_caches(
    root: Path,
    whitelist: tuple[str, ...],
    min_age_days: int,
    min_size_bytes: int,
) -> list[Candidate]:
    """Scan /Library/Caches. Returns [] when not root."""
    if os.geteuid() != 0:
        return []
    if not root.is_dir():
        return []

    now = time.time()
    prelim: list[tuple[Path, float]] = []
    for entry in root.iterdir():
        if is_never_touch(entry):
            continue
        if is_in_whitelist(entry.name, whitelist):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        age_days = (now - mtime) / 86400
        if age_days < min_age_days:
            continue
        prelim.append((entry, age_days))

    sizes = _util.get_sizes([p for p, _ in prelim])

    results: list[Candidate] = []
    for entry, age_days in prelim:
        size = sizes.get(entry, 0)
        if size < min_size_bytes:
            continue
        results.append(
            Candidate(
                path=entry,
                size_bytes=size,
                category="system_cache",
                reason=f"system cache age={age_days:.0f}d size={_util.format_bytes(size)}",
            )
        )
    return results


def _is_file_open(path: Path) -> bool:
    """True iff any process holds `path` open. Fail closed on error:
    `lsof` returns non-zero both for 'no matches' and for permission
    errors, so we key off stdout content, not the return code."""
    try:
        result = subprocess.run(
            [LSOF_BIN, "-F", "n", "--", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, UnicodeDecodeError):
        return True
    return bool(result.stdout.strip())


def scan_temp_files(
    roots: list[Path],
    min_age_days: int,
    min_size_bytes: int,
) -> list[Candidate]:
    """Scan /tmp and /private/var/folders/*/T. Under sudo, only
    current-uid-owned entries are considered."""
    now = time.time()
    effective_min_age = max(min_age_days, 1)
    prelim: list[tuple[Path, float]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if is_never_touch(entry):
                continue
            if not is_owned_by_current_uid(entry):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            age_days = (now - mtime) / 86400
            if age_days < effective_min_age:
                continue
            if _is_file_open(entry):
                continue
            prelim.append((entry, age_days))

    sizes = _util.get_sizes([p for p, _ in prelim])

    results: list[Candidate] = []
    for entry, age_days in prelim:
        size = sizes.get(entry, 0)
        if size < min_size_bytes:
            continue
        results.append(
            Candidate(
                path=entry,
                size_bytes=size,
                category="temp",
                reason=f"temp file age={age_days:.0f}d size={_util.format_bytes(size)}",
            )
        )
    return results
