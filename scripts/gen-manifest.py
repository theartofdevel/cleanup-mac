#!/usr/bin/env python3
"""Generate manifest.json for a cleanup-mac release.

Consumed at update time by cleanup_mac.updater.fetch_manifest. Schema
must stay in lockstep with cleanup_mac.updater.Manifest / ArtifactInfo
— tests/test_gen_manifest.py round-trips the output through the
parser as a drift guard.

Usage:
    scripts/gen-manifest.py
        → writes <release_dir>/manifest.json

Environment:
    CLEANUP_MAC_VERSION_FILE   override path to _version.py (test hook)
    CLEANUP_MAC_RELEASE_DIR    override dist/release/         (test hook)

Failure modes (exit 1):
    - _version.py missing or unreadable
    - any of the 8 expected artifacts (tarball + pkg + their .sha256
      sidecars, per arch) missing from the release dir
    - sha256 sidecar malformed (not shasum -a 256 output)
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1
ARCHES = ("arm64", "x86_64")

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_version() -> str:
    override = os.environ.get("CLEANUP_MAC_VERSION_FILE")
    path = Path(override) if override else REPO_ROOT / "cleanup_mac" / "_version.py"
    try:
        text = path.read_text()
    except OSError as e:
        print(f"error: cannot read {path}: {e}", file=sys.stderr)
        sys.exit(1)
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        print(f"error: no __version__ assignment in {path}", file=sys.stderr)
        sys.exit(1)
    return m.group(1)


def _release_dir() -> Path:
    override = os.environ.get("CLEANUP_MAC_RELEASE_DIR")
    return Path(override) if override else REPO_ROOT / "dist" / "release"


def _read_sha256(sidecar: Path) -> str:
    """Parse the first field of a shasum -a 256 output: '<hex>  <name>'."""
    try:
        line = sidecar.read_text().strip().splitlines()[0]
    except (OSError, IndexError) as e:
        print(f"error: cannot read {sidecar}: {e}", file=sys.stderr)
        sys.exit(1)
    m = re.match(r"^([0-9a-fA-F]{64})\s", line)
    if not m:
        print(
            f"error: {sidecar} does not start with a 64-hex sha256 "
            f"followed by whitespace (shasum -a 256 format)",
            file=sys.stderr,
        )
        sys.exit(1)
    return m.group(1).lower()


def _require(path: Path) -> None:
    if not path.is_file():
        print(f"error: missing required artifact: {path}", file=sys.stderr)
        sys.exit(1)


def main() -> int:
    version = _read_version()
    release_dir = _release_dir()

    artifacts: dict[str, dict[str, str]] = {}
    for arch in ARCHES:
        tarball = f"cleanup-mac-{version}-{arch}.tar.gz"
        pkg = f"cleanup-mac-{version}-{arch}.pkg"
        tarball_path = release_dir / tarball
        pkg_path = release_dir / pkg
        tarball_sha = release_dir / f"{tarball}.sha256"
        pkg_sha = release_dir / f"{pkg}.sha256"

        for p in (tarball_path, pkg_path, tarball_sha, pkg_sha):
            _require(p)

        artifacts[arch] = {
            "tarball": tarball,
            "tarball_sha256": _read_sha256(tarball_sha),
            "pkg": pkg,
            "pkg_sha256": _read_sha256(pkg_sha),
        }

    # ISO-8601 UTC, second-precision (no fractional) to match the
    # style updater.py prints back to the user.
    released_at = (
        datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "version": version,
        "released_at": released_at,
        "artifacts": artifacts,
    }

    out = release_dir / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"wrote: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
