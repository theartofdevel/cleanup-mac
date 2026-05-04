"""Per-category scanners. Each returns a list[Candidate]; no side effects."""

from __future__ import annotations

import os
import re
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
    "Library/Caches",
    "Library/Containers",
    "Library/Group Containers",
    "Library/Preferences",
    "Library/LaunchAgents",
    "Library/Saved Application State",
)

_GENERIC_BUNDLE_TOKENS: frozenset[str] = frozenset(
    {
        "agent",
        "app",
        "application",
        "client",
        "cloud",
        "desktop",
        "desktopclient",
        "dev",
        "electron",
        "helper",
        "io",
        "launcher",
        "mac",
        "macos",
        "manager",
        "net",
        "org",
        "service",
        "sync",
        "updater",
    }
)

_DANGEROUS_INFERRED_NAMES: frozenset[str] = frozenset(
    {
        "applications",
        "desktop",
        "developer",
        "documents",
        "downloads",
        "library",
        "mobile documents",
        "movies",
        "music",
        "pictures",
        "public",
    }
)

_INFERRED_STRIPPABLE_SUFFIXES: tuple[str, ...] = (
    ".log",
    ".plist",
    ".savedstate",
    ".sqlite",
)


def _strong_bundle_tokens(bundle_id: str) -> set[str]:
    tokens: set[str] = set()
    for raw in re.split(r"[^a-zA-Z0-9]+", bundle_id):
        token = raw.lower()
        if len(token) < 4:
            continue
        if token in _GENERIC_BUNDLE_TOKENS:
            continue
        tokens.add(token)
        without_version = re.sub(r"\d+$", "", token)
        if len(without_version) >= 4 and without_version not in _GENERIC_BUNDLE_TOKENS:
            tokens.add(without_version)
    return tokens


def _plain_name_matches_token(name: str, token: str) -> bool:
    lowered = name.lower()
    for suffix in _INFERRED_STRIPPABLE_SUFFIXES:
        if lowered.endswith(suffix):
            lowered = lowered[: -len(suffix)]
            break
    if lowered in _DANGEROUS_INFERRED_NAMES:
        return False
    if lowered == token:
        return True
    return any(lowered.startswith(f"{token}{sep}") for sep in (" ", "-", "_", "."))


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
    Plain names are inferred only from strong removed-app bundle-ID anchors."""
    now = time.time()
    strict_prelim: list[tuple[Path, str, bool]] = []
    plain_prelim: list[Path] = []
    for loc in scan_locations:
        if not loc.is_dir():
            continue
        for entry in loc.iterdir():
            if is_never_touch(entry):
                continue
            if is_in_whitelist(entry.name, whitelist):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            age_days = (now - mtime) / 86400
            if is_bundle_id(entry.name):
                bid = base_id(entry.name)
                if bid in installed_ids:
                    continue
                strict_prelim.append((entry, bid, age_days >= min_age_days))
            else:
                if age_days < min_age_days:
                    continue
                plain_prelim.append(entry)

    inferred: list[tuple[Path, str, str]] = []
    inferred_bids: set[str] = set()
    anchor_prelim = [(entry, bid) for entry, bid, age_ok in strict_prelim if age_ok]
    installed_tokens = set().union(*(_strong_bundle_tokens(bid) for bid in installed_ids))
    for entry in plain_prelim:
        for _, bid in anchor_prelim:
            for token in _strong_bundle_tokens(bid):
                if token in installed_tokens:
                    continue
                if _plain_name_matches_token(entry.name, token):
                    inferred.append((entry, bid, token))
                    inferred_bids.add(bid)
                    break
            if inferred and inferred[-1][0] == entry:
                break

    sizes = _util.get_sizes([p for p, _, _ in strict_prelim] + [p for p, _, _ in inferred])

    results: list[Candidate] = []
    seen: set[Path] = set()
    for entry, bid, age_ok in strict_prelim:
        size = sizes.get(entry, 0)
        if bid not in inferred_bids and (not age_ok or size < min_size_bytes):
            continue
        results.append(
            Candidate(
                path=entry,
                size_bytes=size,
                category="leftover",
                reason=f"no app with bundle id {bid}",
            )
        )
        seen.add(entry)
    for entry, bid, token in inferred:
        if entry in seen:
            continue
        size = sizes.get(entry, 0)
        results.append(
            Candidate(
                path=entry,
                size_bytes=size,
                category="leftover",
                reason=f'inferred leftover for {bid} via token "{token}"',
            )
        )
        seen.add(entry)
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
