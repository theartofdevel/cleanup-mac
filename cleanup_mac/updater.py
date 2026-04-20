"""Self-update: fetch manifest → verify SHA-256 → verify Developer ID
notarization pinned to the upstream team → atomic rename → re-exec.

Stdlib only. Override the release mirror with CLEANUP_MAC_UPDATE_BASE."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from cleanup_mac._version import __version__
from cleanup_mac.logger import structured_log

# GitHub's `/releases/latest/download/<asset>` route redirects to the
# matching asset in the current latest release. manifest.json has no
# version in its filename so it resolves directly; versioned tarball
# names from the manifest are resolved via the same redirect. Forks
# override CLEANUP_MAC_UPDATE_BASE.
DEFAULT_UPDATE_BASE = (
    "https://github.com/theartofdevel/cleanup-mac/releases/latest/download"
)
UPDATE_BASE_ENV = "CLEANUP_MAC_UPDATE_BASE"

# Apple Developer Team ID of the upstream signing identity. Pinned via
# -R so an attacker with any other notarized Developer ID cannot pass.
UPSTREAM_TEAM_ID = "D3XP794W84"
CODESIGN_REQUIREMENT = (
    f'anchor apple generic and certificate leaf[subject.OU] = "{UPSTREAM_TEAM_ID}"'
)

# Absolute paths — bare names would be vulnerable to PATH hijack.
CODESIGN_BIN = "/usr/bin/codesign"
SPCTL_BIN = "/usr/sbin/spctl"
XATTR_BIN = "/usr/bin/xattr"

PACKAGE_MANAGED_PREFIXES: tuple[str, ...] = (
    "/opt/homebrew/Cellar/",
    "/usr/local/Cellar/",
    "/opt/local/",   # MacPorts
    "/usr/pkg/",     # pkgsrc
)

USER_AGENT = f"cleanup-mac/{__version__}"
DOWNLOAD_TIMEOUT_S = 60
HTTP_CHUNK = 64 * 1024


class UpdateError(RuntimeError):
    """Non-recoverable update failure. Message is user-facing."""


class UpdateDeclined(UpdateError):
    """User declined the pre-download prompt. cli.py maps it to exit 3."""


def _require_https(url: str) -> None:
    """HTTP would let a MITM swap manifest+tarball with matching SHA-256;
    the codesign pin still catches the binary swap but we reject plain
    HTTP at transport time anyway."""
    if not url.lower().startswith("https://"):
        raise UpdateError(
            f"update base URL must use HTTPS, got: {url!r}. "
            f"Set {UPDATE_BASE_ENV} to an https:// URL."
        )


@dataclass(frozen=True)
class ArtifactInfo:
    tarball: str
    tarball_sha256: str
    pkg: str
    pkg_sha256: str


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    version: str
    released_at: str
    artifacts: dict[str, ArtifactInfo]


def detect_arch() -> str:
    m = platform.machine()
    if m in ("arm64", "aarch64"):
        return "arm64"
    if m in ("x86_64", "amd64"):
        return "x86_64"
    raise UpdateError(
        f"unsupported architecture: {m} — cleanup-mac only ships arm64 and x86_64"
    )


def fetch_manifest(base_url: str) -> Manifest:
    """Download and parse <base_url>/manifest.json. Refuses unknown
    schema_version rather than guessing."""
    _require_https(base_url)
    url = f"{base_url.rstrip('/')}/manifest.json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_S) as resp:
            body = resp.read()
    except urllib.error.URLError as e:
        raise UpdateError(f"cannot reach update server: {e}") from e

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise UpdateError(f"manifest is not valid JSON: {e}") from e

    try:
        schema_version = int(data["schema_version"])
    except (KeyError, TypeError, ValueError) as e:
        raise UpdateError(f"manifest missing schema_version: {e}") from e

    if schema_version != 1:
        raise UpdateError(
            f"manifest schema_version={schema_version} is not supported by "
            f"this build of cleanup-mac ({__version__}). Upgrade manually "
            f"from {base_url.rstrip('/')}/"
        )

    try:
        artifacts = {
            arch: ArtifactInfo(
                tarball=item["tarball"],
                tarball_sha256=item["tarball_sha256"],
                pkg=item["pkg"],
                pkg_sha256=item["pkg_sha256"],
            )
            for arch, item in data["artifacts"].items()
        }
    except (KeyError, TypeError) as e:
        raise UpdateError(f"manifest has malformed 'artifacts': {e}") from e

    return Manifest(
        schema_version=schema_version,
        version=data["version"],
        released_at=data.get("released_at", ""),
        artifacts=artifacts,
    )


def _semver_tuple(v: str) -> tuple[int, ...]:
    parts = v.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise UpdateError(f"not a valid semver version: {v!r}")
    return tuple(int(p) for p in parts)


def is_newer(manifest_version: str, running_version: str) -> bool:
    return _semver_tuple(manifest_version) > _semver_tuple(running_version)


def resolve_install_path(argv0: str) -> Path:
    """Resolve argv[0] through any symlinks to the file that will be
    replaced."""
    return Path(argv0).resolve(strict=False)


def refuse_package_managed(install_path: Path) -> None:
    s = str(install_path)
    for prefix in PACKAGE_MANAGED_PREFIXES:
        if s.startswith(prefix):
            raise UpdateError(
                f"refusing to update a package-manager-managed install at "
                f"{install_path}. Update via your package manager instead "
                f"(e.g. `brew upgrade cleanup-mac`)."
            )


def refuse_source_run(install_path: Path) -> None:
    """`make install` aliases ~/bin/cleanup-mac → $REPO/cleanup_mac.py.
    Overwriting a .py script with a Mach-O would break imports and leave
    a dirty git tree; source-run users should `git pull`."""
    if install_path.suffix == ".py":
        raise UpdateError(
            f"refusing to update a source-run install at {install_path}. "
            f"Update via `git pull` instead."
        )
    for parent in (install_path, *install_path.parents):
        if (parent / ".git").exists():
            raise UpdateError(
                f"refusing to update a source-run install at {install_path}. "
                f"Path lives inside a git worktree at {parent}. "
                f"Update via `git pull` instead."
            )


def _download_to(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with (
            urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_S) as resp,
            open(dest, "wb") as f,
        ):
            while chunk := resp.read(HTTP_CHUNK):
                f.write(chunk)
    except urllib.error.URLError as e:
        raise UpdateError(f"download failed: {url}: {e}") from e


def verify_sha256(path: Path, expected: str) -> None:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(HTTP_CHUNK):
            h.update(chunk)
    actual = h.hexdigest()
    if actual.lower() != expected.lower():
        raise UpdateError(
            f"sha256 mismatch for {path.name}: expected {expected}, got {actual}"
        )


def extract_binary(tarball: Path, into: Path) -> Path:
    """Extract the single `cleanup-mac` entry from the tarball. The
    release pipeline guarantees exactly one file at root; any deviation
    is rejected."""
    with tarfile.open(tarball, "r:gz") as tf:
        members = tf.getmembers()
        if len(members) != 1:
            raise UpdateError(
                f"tarball expected to contain exactly 1 file, got {len(members)}"
            )
        member = members[0]
        if member.name != "cleanup-mac":
            raise UpdateError(
                f"tarball's single file must be named 'cleanup-mac', got {member.name!r}"
            )
        if not member.isfile():
            raise UpdateError("tarball's 'cleanup-mac' entry is not a regular file")
        tf.extract(member, into, filter="data")
    return into / "cleanup-mac"


def verify_notarized(binary: Path) -> None:
    """Run codesign (pinned to UPSTREAM_TEAM_ID) and spctl.

    `--type install` is the spctl type that accepts a bare notarized
    Mach-O: `--type execute` requires a .app bundle, `--type open`
    requires a quarantine xattr we may have already stripped."""
    try:
        subprocess.run(
            [
                CODESIGN_BIN, "--verify", "--deep", "--strict",
                "-R", CODESIGN_REQUIREMENT,
                str(binary),
            ],
            check=True, capture_output=True, text=True, timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        stderr = getattr(e, "stderr", "") or ""
        raise UpdateError(
            f"codesign verification failed on downloaded binary: {stderr.strip() or e}"
        ) from e

    try:
        subprocess.run(
            [SPCTL_BIN, "--assess", "--type", "install", str(binary)],
            check=True, capture_output=True, text=True, timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        stderr = getattr(e, "stderr", "") or ""
        raise UpdateError(
            f"spctl notarization check failed on downloaded binary: "
            f"{stderr.strip() or e}"
        ) from e


def strip_quarantine(binary: Path) -> None:
    with contextlib.suppress(subprocess.TimeoutExpired, FileNotFoundError):
        subprocess.run(
            [XATTR_BIN, "-d", "com.apple.quarantine", str(binary)],
            check=False, capture_output=True, timeout=5,
        )


def atomic_replace(new: Path, current: Path) -> None:
    """Rename `new` over `current` preserving the existing mode bits.
    Both must be on the same filesystem for the rename to be atomic."""
    try:
        st = current.stat()
        os.chmod(new, st.st_mode)
    except FileNotFoundError:
        os.chmod(new, 0o755)
    os.rename(new, current)


def _print_update_banner(
    running: str,
    manifest: Manifest,
    artifact: ArtifactInfo,
    install_path: Path,
    out: TextIO,
) -> None:
    out.write(f"cleanup-mac {running} → {manifest.version}\n")
    if manifest.released_at:
        out.write(f"released: {manifest.released_at}\n")
    out.write(f"artifact: {artifact.tarball}\n")
    out.write(f"sha256:   {artifact.tarball_sha256}\n")
    out.write(f"install:  {install_path}\n")
    out.write("\n")
    out.flush()


def _confirm_update(yes: bool, in_stream: TextIO, out: TextIO) -> bool:
    """--yes bypasses. Non-TTY refuses — the successful path re-execs
    into the new binary, so silent auto-update from cron is unsafe."""
    if yes:
        return True
    if not in_stream.isatty():
        out.write(
            "cleanup-mac: stdin is not a TTY — cannot confirm update.\n"
            "Re-run with --yes to approve the update explicitly.\n"
        )
        out.flush()
        return False
    out.write("Download, verify codesign, and replace? [y/N] ")
    out.flush()
    ans = in_stream.readline().strip().lower()
    return ans in ("y", "yes")


def perform_update(
    logger: logging.Logger | None = None,
    base_url: str | None = None,
    argv0: str | None = None,
    skip_exec: bool = False,
    yes: bool = False,
    in_stream: TextIO | None = None,
    out_stream: TextIO | None = None,
) -> str:
    """Returns the new version on success. On success the function
    normally does not return — it os.execv's into the new binary.
    Returns only if the running version is already latest, the user
    declines, or skip_exec=True (for tests)."""
    if logger is None:
        logger = logging.getLogger("cleanup_mac")
    if base_url is None:
        base_url = os.environ.get(UPDATE_BASE_ENV, DEFAULT_UPDATE_BASE)
    if argv0 is None:
        argv0 = sys.argv[0]

    _require_https(base_url)

    # Downloading + writing as root amplifies any MITM into root RCE.
    # .pkg installs at /usr/local/bin are root-owned; users should
    # re-download the .pkg rather than sudo --update.
    if os.geteuid() == 0:
        raise UpdateError(
            "refusing to run --update as root. Re-run without sudo; "
            "for /usr/local/bin installs, re-download the .pkg from the "
            "release mirror and install via double-click."
        )

    structured_log(logger, logging.INFO, "update_started", base_url=base_url)

    manifest = fetch_manifest(base_url)
    if not is_newer(manifest.version, __version__):
        structured_log(
            logger, logging.INFO, "update_not_needed",
            current=__version__, available=manifest.version,
        )
        return manifest.version

    arch = detect_arch()
    if arch not in manifest.artifacts:
        raise UpdateError(
            f"manifest has no artifact for this architecture ({arch}). "
            f"Available: {sorted(manifest.artifacts)}"
        )
    artifact = manifest.artifacts[arch]

    install_path = resolve_install_path(argv0)
    refuse_package_managed(install_path)
    refuse_source_run(install_path)

    out = out_stream or sys.stderr
    ins = in_stream or sys.stdin
    _print_update_banner(__version__, manifest, artifact, install_path, out)
    if not _confirm_update(yes, ins, out):
        structured_log(
            logger, logging.INFO, "update_declined",
            current=__version__, available=manifest.version,
        )
        raise UpdateDeclined(
            f"update from {__version__} to {manifest.version} declined by user"
        )

    # Tempdir must share a filesystem with install_path — os.rename is
    # atomic only within one filesystem.
    workdir = Path(tempfile.mkdtemp(
        prefix="cleanup-mac-update-",
        dir=install_path.parent,
    ))
    try:
        tarball_url = f"{base_url.rstrip('/')}/{artifact.tarball}"
        tarball_path = workdir / artifact.tarball
        _download_to(tarball_url, tarball_path)
        verify_sha256(tarball_path, artifact.tarball_sha256)

        extracted = extract_binary(tarball_path, workdir)
        verify_notarized(extracted)
        strip_quarantine(extracted)

        atomic_replace(extracted, install_path)
        structured_log(
            logger, logging.INFO, "update_installed",
            from_version=__version__, to_version=manifest.version,
            install_path=str(install_path),
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    # Re-exec with --version — gives the user immediate confirmation of
    # the installed version. The original --update argv is dropped so
    # the new binary does not start a second update round.
    if not skip_exec:
        os.execv(str(install_path), [str(install_path), "--version"])

    return manifest.version


def check_update(base_url: str | None = None) -> tuple[bool, str]:
    """Return (newer_available, latest_version). Read-only."""
    if base_url is None:
        base_url = os.environ.get(UPDATE_BASE_ENV, DEFAULT_UPDATE_BASE)
    _require_https(base_url)
    manifest = fetch_manifest(base_url)
    return is_newer(manifest.version, __version__), manifest.version
