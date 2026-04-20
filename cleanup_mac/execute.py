"""Destructive primitives. Every filesystem write goes through here
and routes through the safety module."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from cleanup_mac import _util
from cleanup_mac.logger import structured_log
from cleanup_mac.safety import (
    _guard_deletion,
    _path_fingerprint,
    _verify_unchanged,
)
from cleanup_mac.types import Candidate

CONTAINER_PREFIXES: tuple[str, ...] = (
    "Library/Containers",
    "Library/Group Containers",
)

CONTAINER_METADATA_PLIST = ".com.apple.containermanagerd.metadata.plist"


def _is_container_path(path: Path) -> bool:
    """True iff `path` is a direct child of ~/Library/Containers or
    ~/Library/Group Containers — i.e. a Container root, not deeper."""
    home = Path.home().resolve()
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False
    return any(
        resolved.parent == (home / rel).resolve() for rel in CONTAINER_PREFIXES
    )


def _unique_trash_dest(name: str, trash_dir: Path) -> Path:
    dest = trash_dir / name
    if dest.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = trash_dir / f"{name} {stamp}"
    return dest


def _trash_container_contents(container: Path, allowed_roots: list[Path]) -> None:
    """Move every child of a Container to Trash except the SIP-locked
    metadata plist. Sandbox blocks renaming the container root itself,
    so the ~40 KB stub stays on disk.

    Each child is re-guarded against allowed_roots and never-touch so a
    sandboxed app can't plant a symlink escaping the safe tree.

    Raises RuntimeError on any failure with both success and failure
    counts in the message.
    """
    trash_dir = Path.home() / ".Trash"
    trash_dir.mkdir(exist_ok=True)
    moved = 0
    errors: list[str] = []
    for child in container.iterdir():
        if child.name == CONTAINER_METADATA_PLIST:
            continue
        try:
            guarded = _guard_deletion(child, allowed_roots)
            fp = _path_fingerprint(guarded)
        except (PermissionError, FileNotFoundError) as e:
            errors.append(f"{child.name}: {e}")
            continue
        dest = _unique_trash_dest(f"{container.name}--{child.name}", trash_dir)
        try:
            _verify_unchanged(guarded, fp)
            os.rename(guarded, dest)
            moved += 1
        except (OSError, PermissionError) as e:
            errors.append(f"{child.name}: {e}")
    if errors:
        raise RuntimeError(
            f"{moved} moved, {len(errors)} failed from {container.name}: "
            + "; ".join(errors)
        )


def delete_permanent(target: Path, allowed_roots: list[Path]) -> None:
    """Irrecoverable delete. Raises PermissionError on safety violations.
    For Containers, leaves the sandbox-locked stub."""
    resolved = _guard_deletion(target, allowed_roots)
    fingerprint = _path_fingerprint(resolved)

    if _is_container_path(resolved):
        _verify_unchanged(resolved, fingerprint)
        removed = 0
        errors: list[str] = []
        for child in resolved.iterdir():
            if child.name == CONTAINER_METADATA_PLIST:
                continue
            try:
                guarded = _guard_deletion(child, allowed_roots)
                child_fp = _path_fingerprint(guarded)
            except (PermissionError, FileNotFoundError) as e:
                errors.append(f"{child.name}: {e}")
                continue
            try:
                _verify_unchanged(guarded, child_fp)
                if guarded.is_dir() and not guarded.is_symlink():
                    shutil.rmtree(guarded)
                else:
                    guarded.unlink()
                removed += 1
            except (OSError, PermissionError) as e:
                errors.append(f"{child.name}: {e}")
        if errors:
            raise RuntimeError(
                f"{removed} removed, {len(errors)} failed from {resolved.name}: "
                + "; ".join(errors)
            )
        return

    _verify_unchanged(resolved, fingerprint)
    if resolved.is_dir() and not resolved.is_symlink():
        shutil.rmtree(resolved)
    else:
        resolved.unlink(missing_ok=True)


def move_to_trash(target: Path, allowed_roots: list[Path]) -> None:
    """Move `target` to ~/.Trash via atomic os.rename. For Containers,
    moves the contents rather than the root (sandbox locks the root)."""
    resolved = _guard_deletion(target, allowed_roots)
    fingerprint = _path_fingerprint(resolved)

    if _is_container_path(resolved):
        _verify_unchanged(resolved, fingerprint)
        _trash_container_contents(resolved, allowed_roots)
        return

    trash_dir = Path.home() / ".Trash"
    trash_dir.mkdir(exist_ok=True)
    dest = _unique_trash_dest(resolved.name, trash_dir)
    _verify_unchanged(resolved, fingerprint)
    os.rename(resolved, dest)


def execute_candidates(
    candidates: list[Candidate],
    apply: bool,
    permanent: bool,
    allowed_roots: list[Path],
    logger: logging.Logger,
) -> int:
    """Run deletions (or dry-run). Returns total bytes freed.

    Per candidate: exactly one log record — `would_delete`, `trashed`,
    `permanent_deleted`, or `failed`. A container's partial progress
    before an error is accounted for via a pre/post size delta.
    """
    freed = 0
    for c in candidates:
        if not apply:
            structured_log(
                logger,
                logging.INFO,
                "would_delete",
                path=str(c.path),
                size_bytes=c.size_bytes,
                category=c.category,
            )
            continue
        t0 = time.perf_counter()
        try:
            if permanent:
                delete_permanent(c.path, allowed_roots)
                event = "permanent_deleted"
            else:
                move_to_trash(c.path, allowed_roots)
                event = "trashed"
            duration_ms = int((time.perf_counter() - t0) * 1000)
            structured_log(
                logger,
                logging.INFO,
                event,
                path=str(c.path),
                size_bytes=c.size_bytes,
                duration_ms=duration_ms,
            )
            freed += c.size_bytes
        except (
            PermissionError,
            RuntimeError,
            OSError,
            shutil.Error,
            subprocess.TimeoutExpired,
        ) as e:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            partial = max(0, c.size_bytes - _util.get_size(c.path))
            freed += partial
            structured_log(
                logger,
                logging.ERROR,
                "failed",
                path=str(c.path),
                reason=str(e),
                duration_ms=duration_ms,
                partial_freed_bytes=partial,
            )
    return freed
