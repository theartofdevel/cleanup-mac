"""Package-manager cache detection. Only binaries resolved to trusted
system prefixes are invoked — a shim in a user-writable PATH entry
would otherwise run arbitrary code on our behalf."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TextIO

from cleanup_mac import _util
from cleanup_mac.types import PackageCleanup

TRUSTED_BIN_PREFIXES: tuple[str, ...] = (
    "/opt/homebrew/",
    "/usr/local/",
    "/usr/bin/",
    "/usr/sbin/",
    "/bin/",
    "/sbin/",
)


def _resolve_trusted_binary(name: str) -> str | None:
    """Return the realpath of `name` on PATH only if it lives under a
    trusted system prefix. Symlinks are followed so Homebrew's
    /opt/homebrew/bin/* → Cellar/* still qualifies."""
    found = shutil.which(name)
    if found is None:
        return None
    real = os.path.realpath(found)
    if any(real.startswith(prefix) for prefix in TRUSTED_BIN_PREFIXES):
        return real
    return None


def _warn_untrusted_binary(name: str, stderr: TextIO) -> None:
    found = shutil.which(name)
    if found is None:
        return
    real = os.path.realpath(found)
    stderr.write(
        f"warning: skipping {name} — resolved to {real}, "
        f"not under a trusted system prefix\n"
    )


def _brew_cache_dir(brew: str) -> Path | None:
    try:
        out = subprocess.run(
            [brew, "--cache"], capture_output=True, text=True, check=True, timeout=10
        )
        return Path(out.stdout.strip())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _go_cache_dir(go: str) -> Path | None:
    try:
        out = subprocess.run(
            [go, "env", "GOCACHE"], capture_output=True, text=True, check=True, timeout=10
        )
        return Path(out.stdout.strip())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None


def scan_package_managers(
    aggressive: bool = False,
    stderr: TextIO | None = None,
) -> list[PackageCleanup]:
    """Detect package managers and report cache size + cleanup command.
    Untrusted resolutions are skipped with a stderr warning."""
    results: list[PackageCleanup] = []
    home = Path.home()
    if stderr is None:
        stderr = sys.stderr

    brew = _resolve_trusted_binary("brew")
    if brew:
        cache = _brew_cache_dir(brew)
        size = _util.get_size(cache) if cache else 0
        results.append(
            PackageCleanup(
                tool="brew",
                current_size_bytes=size,
                apply_command=[brew, "cleanup", "--prune=all"],
            )
        )
    else:
        _warn_untrusted_binary("brew", stderr)

    npm = _resolve_trusted_binary("npm")
    if npm:
        size = _util.get_size(home / ".npm")
        results.append(
            PackageCleanup(
                tool="npm",
                current_size_bytes=size,
                apply_command=[npm, "cache", "clean", "--force"],
            )
        )
    else:
        _warn_untrusted_binary("npm", stderr)

    yarn = _resolve_trusted_binary("yarn")
    if yarn:
        size = _util.get_size(home / ".yarn/cache")
        results.append(
            PackageCleanup(
                tool="yarn",
                current_size_bytes=size,
                apply_command=[yarn, "cache", "clean"],
            )
        )
    else:
        _warn_untrusted_binary("yarn", stderr)

    pip = _resolve_trusted_binary("pip3") or _resolve_trusted_binary("pip")
    if pip:
        size = _util.get_size(home / "Library/Caches/pip")
        results.append(
            PackageCleanup(
                tool="pip",
                current_size_bytes=size,
                apply_command=[pip, "cache", "purge"],
            )
        )
    elif shutil.which("pip3"):
        _warn_untrusted_binary("pip3", stderr)
    elif shutil.which("pip"):
        _warn_untrusted_binary("pip", stderr)

    go = _resolve_trusted_binary("go")
    if go:
        cache = _go_cache_dir(go)
        size = _util.get_size(cache) if cache else 0
        results.append(
            PackageCleanup(
                tool="go",
                current_size_bytes=size,
                apply_command=[go, "clean", "-cache"],
            )
        )
    else:
        _warn_untrusted_binary("go", stderr)

    if aggressive:
        docker = _resolve_trusted_binary("docker")
        if docker:
            results.append(
                PackageCleanup(
                    tool="docker",
                    current_size_bytes=0,
                    apply_command=[docker, "image", "prune", "-f"],
                )
            )
        else:
            _warn_untrusted_binary("docker", stderr)

    return results
