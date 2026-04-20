"""Tests for scripts/gen-manifest.py.

The generator writes manifest.json consumed at update time by
cleanup_mac.updater.fetch_manifest. We test two contracts:

1. The output is parseable by updater.fetch_manifest without error
   (schema drift guard).
2. The generator refuses to run when any expected artifact is missing.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "gen-manifest.py"


def _write_artifact(dir: Path, name: str, content: bytes) -> None:
    """Write an artifact file plus its `.sha256` sidecar (shasum -a 256 format)."""
    (dir / name).write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    # shasum -a 256 format: "<hex>  <filename>\n"
    (dir / f"{name}.sha256").write_text(f"{digest}  {name}\n")


def _populate_release_dir(release_dir: Path, version: str) -> None:
    """Populate all 8 artifacts the generator expects for a full release."""
    for arch in ("arm64", "x86_64"):
        _write_artifact(
            release_dir,
            f"cleanup-mac-{version}-{arch}.tar.gz",
            f"fake-tarball-{arch}".encode(),
        )
        _write_artifact(
            release_dir,
            f"cleanup-mac-{version}-{arch}.pkg",
            f"fake-pkg-{arch}".encode(),
        )


@pytest.fixture
def fake_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A populated dist/release/ with a pinned __version__."""
    release_dir = tmp_path / "dist" / "release"
    release_dir.mkdir(parents=True)
    _populate_release_dir(release_dir, "1.2.3")

    # Pin the version by pointing CLEANUP_MAC_VERSION_FILE at a fake _version.py.
    version_file = tmp_path / "_version.py"
    version_file.write_text('__version__ = "1.2.3"\n')
    monkeypatch.setenv("CLEANUP_MAC_VERSION_FILE", str(version_file))
    monkeypatch.setenv("CLEANUP_MAC_RELEASE_DIR", str(release_dir))
    return release_dir


def _run_generator() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
    )


def test_generator_emits_schema_v1(fake_release: Path) -> None:
    result = _run_generator()
    assert result.returncode == 0, result.stderr

    manifest_path = fake_release / "manifest.json"
    assert manifest_path.exists()

    data = json.loads(manifest_path.read_text())
    assert data["schema_version"] == 1
    assert data["version"] == "1.2.3"
    assert set(data["artifacts"]) == {"arm64", "x86_64"}

    for arch in ("arm64", "x86_64"):
        entry = data["artifacts"][arch]
        assert entry["tarball"] == f"cleanup-mac-1.2.3-{arch}.tar.gz"
        assert entry["pkg"] == f"cleanup-mac-1.2.3-{arch}.pkg"
        # 64 hex chars
        assert len(entry["tarball_sha256"]) == 64
        assert len(entry["pkg_sha256"]) == 64


def test_generator_output_round_trips_through_updater(fake_release: Path) -> None:
    """If updater.fetch_manifest cannot parse the output, the schemas have drifted."""
    result = _run_generator()
    assert result.returncode == 0, result.stderr

    manifest_path = fake_release / "manifest.json"
    body = manifest_path.read_bytes()

    # Monkeypatch urllib to return our local manifest, then call the real parser.
    import urllib.request

    from cleanup_mac import updater

    class _FakeResp:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self) -> bytes:
            return self._data

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    def _fake_urlopen(req: object, timeout: int = 0) -> _FakeResp:
        return _FakeResp(body)

    orig = urllib.request.urlopen
    urllib.request.urlopen = _fake_urlopen  # type: ignore[assignment]
    try:
        manifest = updater.fetch_manifest("https://example.test/cleanup-mac/latest")
    finally:
        urllib.request.urlopen = orig  # type: ignore[assignment]

    assert manifest.schema_version == 1
    assert manifest.version == "1.2.3"
    assert set(manifest.artifacts) == {"arm64", "x86_64"}


def test_generator_fails_when_artifact_missing(
    fake_release: Path,
) -> None:
    """If any of the 4 expected files per arch is missing, the generator
    must exit non-zero with a clear message — otherwise we'd publish a
    broken manifest and brick --update for that version."""
    # Remove one required file.
    (fake_release / "cleanup-mac-1.2.3-arm64.pkg").unlink()

    result = _run_generator()
    assert result.returncode != 0
    assert "arm64" in result.stderr
    assert "cleanup-mac-1.2.3-arm64.pkg" in result.stderr


def test_generator_fails_when_sha256_sidecar_missing(fake_release: Path) -> None:
    (fake_release / "cleanup-mac-1.2.3-x86_64.tar.gz.sha256").unlink()

    result = _run_generator()
    assert result.returncode != 0
    assert "x86_64" in result.stderr
