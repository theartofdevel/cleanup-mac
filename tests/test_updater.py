"""Tests for cleanup_mac.updater — the --update command."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from cleanup_mac import updater
from cleanup_mac.updater import (
    ArtifactInfo,
    Manifest,
    UpdateError,
    _require_https,
    _semver_tuple,
    detect_arch,
    extract_binary,
    fetch_manifest,
    is_newer,
    refuse_package_managed,
    refuse_source_run,
    resolve_install_path,
    verify_notarized,
    verify_sha256,
)

# --- semver / version comparison -----------------------------------------

def test_semver_tuple_parses_basic():
    assert _semver_tuple("0.3.1") == (0, 3, 1)
    assert _semver_tuple("10.20.30") == (10, 20, 30)


def test_semver_tuple_rejects_malformed():
    for bad in ["0.3", "0.3.1.2", "0.3.x", "v0.3.1", "0.3-rc1"]:
        with pytest.raises(UpdateError):
            _semver_tuple(bad)


def test_is_newer_true_when_greater():
    assert is_newer("0.4.0", "0.3.1") is True
    assert is_newer("0.3.2", "0.3.1") is True
    assert is_newer("1.0.0", "0.99.99") is True


def test_is_newer_false_when_same_or_older():
    assert is_newer("0.3.1", "0.3.1") is False
    assert is_newer("0.3.0", "0.3.1") is False
    assert is_newer("0.2.99", "0.3.0") is False


# --- arch detection ------------------------------------------------------

def test_detect_arch_arm64():
    with patch("platform.machine", return_value="arm64"):
        assert detect_arch() == "arm64"
    with patch("platform.machine", return_value="aarch64"):
        assert detect_arch() == "arm64"


def test_detect_arch_x86_64():
    with patch("platform.machine", return_value="x86_64"):
        assert detect_arch() == "x86_64"
    with patch("platform.machine", return_value="amd64"):
        assert detect_arch() == "x86_64"


def test_detect_arch_rejects_unsupported():
    with (
        patch("platform.machine", return_value="riscv64"),
        pytest.raises(UpdateError, match="riscv64"),
    ):
        detect_arch()


# --- install path + package-manager refusal ------------------------------

def test_resolve_install_path_follows_symlink(tmp_path: Path):
    real = tmp_path / "real-binary"
    real.write_text("x")
    link = tmp_path / "link-binary"
    link.symlink_to(real)
    assert resolve_install_path(str(link)) == real.resolve()


def test_refuse_homebrew_cellar_arm64():
    with pytest.raises(UpdateError, match="package-manager"):
        refuse_package_managed(Path("/opt/homebrew/Cellar/cleanup-mac/0.3.0/bin/cleanup-mac"))


def test_refuse_homebrew_cellar_x86_64():
    with pytest.raises(UpdateError, match="package-manager"):
        refuse_package_managed(Path("/usr/local/Cellar/cleanup-mac/0.3.0/bin/cleanup-mac"))


def test_refuse_macports():
    with pytest.raises(UpdateError, match="package-manager"):
        refuse_package_managed(Path("/opt/local/bin/cleanup-mac"))


def test_accept_user_bin():
    # Not raised — ~/bin is user-owned, safe to replace.
    refuse_package_managed(Path("/Users/foo/bin/cleanup-mac"))


def test_refuse_source_run_rejects_py_file(tmp_path: Path):
    """`make install` creates ~/bin/cleanup-mac → $REPO/cleanup_mac.py.
    Replacing a .py script with a Mach-O binary breaks the repo."""
    py = tmp_path / "cleanup_mac.py"
    py.write_text("#!/usr/bin/env python3\n")
    with pytest.raises(UpdateError, match="source-run"):
        refuse_source_run(py)


def test_refuse_source_run_rejects_git_worktree(tmp_path: Path):
    """If the install path lives inside a git worktree, the Mach-O would
    show up in `git status` and diff; refuse so users go through the
    release channel instead."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    target = repo / "cleanup-mac"
    target.write_text("binary")
    with pytest.raises(UpdateError, match="source-run"):
        refuse_source_run(target)


def test_refuse_source_run_accepts_plain_bin(tmp_path: Path):
    """A plain binary in ~/bin (no .git, no .py suffix) is acceptable."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    target = bindir / "cleanup-mac"
    target.write_text("binary")
    refuse_source_run(target)  # must not raise


# --- manifest parsing ----------------------------------------------------

def _minimal_manifest(version: str = "0.4.0") -> dict:
    return {
        "schema_version": 1,
        "version": version,
        "released_at": "2026-04-18T10:00:00Z",
        "artifacts": {
            "arm64": {
                "tarball": f"cleanup-mac-{version}-arm64.tar.gz",
                "tarball_sha256": "a" * 64,
                "pkg": f"cleanup-mac-{version}-arm64.pkg",
                "pkg_sha256": "b" * 64,
            },
            "x86_64": {
                "tarball": f"cleanup-mac-{version}-x86_64.tar.gz",
                "tarball_sha256": "c" * 64,
                "pkg": f"cleanup-mac-{version}-x86_64.pkg",
                "pkg_sha256": "d" * 64,
            },
        },
    }


def _mock_urlopen(body: bytes):
    """Build a context manager imitating urllib.request.urlopen."""
    class _Resp:
        def __init__(self, b):
            self._b = b
        def read(self, n: int = -1):
            if n < 0:
                out, self._b = self._b, b""
                return out
            out, self._b = self._b[:n], self._b[n:]
            return out
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    return _Resp(body)


def test_fetch_manifest_parses_valid(tmp_path: Path):
    body = json.dumps(_minimal_manifest("0.4.0")).encode()
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(body)):
        m = fetch_manifest("https://example.com/latest")
    assert isinstance(m, Manifest)
    assert m.version == "0.4.0"
    assert m.schema_version == 1
    assert "arm64" in m.artifacts
    assert isinstance(m.artifacts["arm64"], ArtifactInfo)
    assert m.artifacts["arm64"].tarball_sha256 == "a" * 64


def test_fetch_manifest_rejects_unknown_schema():
    payload = _minimal_manifest()
    payload["schema_version"] = 99
    body = json.dumps(payload).encode()
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(body)):
        with pytest.raises(UpdateError, match="schema_version=99"):
            fetch_manifest("https://example.com/latest")


def test_fetch_manifest_rejects_invalid_json():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"not json")):
        with pytest.raises(UpdateError, match="not valid JSON"):
            fetch_manifest("https://example.com/latest")


def test_fetch_manifest_reports_network_failure():
    import urllib.error

    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("DNS lookup failed"),
    ), pytest.raises(UpdateError, match="cannot reach update server"):
        fetch_manifest("https://example.com/latest")


# --- sha verification ----------------------------------------------------

def test_verify_sha256_passes(tmp_path: Path):
    blob = b"hello world"
    expected = hashlib.sha256(blob).hexdigest()
    path = tmp_path / "blob"
    path.write_bytes(blob)
    verify_sha256(path, expected)  # no raise


def test_verify_sha256_fails_on_mismatch(tmp_path: Path):
    path = tmp_path / "blob"
    path.write_bytes(b"hello world")
    with pytest.raises(UpdateError, match="sha256 mismatch"):
        verify_sha256(path, "0" * 64)


def test_verify_sha256_is_case_insensitive(tmp_path: Path):
    blob = b"hello"
    path = tmp_path / "blob"
    path.write_bytes(blob)
    verify_sha256(path, hashlib.sha256(blob).hexdigest().upper())  # no raise


# --- tarball extraction --------------------------------------------------

def _build_tarball(
    tmp_path: Path,
    inner_name: str = "cleanup-mac",
    inner_body: bytes = b"FAKE-MACHO",
) -> Path:
    tar_path = tmp_path / "release.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        info = tarfile.TarInfo(name=inner_name)
        info.size = len(inner_body)
        info.mode = 0o755
        tf.addfile(info, io.BytesIO(inner_body))
    return tar_path


def test_extract_binary_happy_path(tmp_path: Path):
    tar = _build_tarball(tmp_path)
    outdir = tmp_path / "out"
    outdir.mkdir()
    extracted = extract_binary(tar, outdir)
    assert extracted == outdir / "cleanup-mac"
    assert extracted.read_bytes() == b"FAKE-MACHO"


def test_extract_binary_rejects_multiple_files(tmp_path: Path):
    tar_path = tmp_path / "multi.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for name in ("cleanup-mac", "unexpected.txt"):
            info = tarfile.TarInfo(name=name)
            info.size = 1
            tf.addfile(info, io.BytesIO(b"x"))
    outdir = tmp_path / "out"
    outdir.mkdir()
    with pytest.raises(UpdateError, match="exactly 1 file"):
        extract_binary(tar_path, outdir)


def test_extract_binary_rejects_wrong_name(tmp_path: Path):
    tar = _build_tarball(tmp_path, inner_name="something-else")
    outdir = tmp_path / "out"
    outdir.mkdir()
    with pytest.raises(UpdateError, match="must be named 'cleanup-mac'"):
        extract_binary(tar, outdir)


# --- perform_update integration (mocked) ---------------------------------

def test_perform_update_noop_when_already_latest(tmp_path: Path):
    """Running version equal to manifest version — no download, no replace."""
    body = json.dumps(_minimal_manifest(version=updater.__version__)).encode()
    dummy_bin = tmp_path / "cleanup-mac"
    dummy_bin.write_bytes(b"old")

    with (
        patch("urllib.request.urlopen", return_value=_mock_urlopen(body)),
        patch("platform.machine", return_value="arm64"),
    ):
        result = updater.perform_update(
            base_url="https://example.com/latest",
            argv0=str(dummy_bin),
            skip_exec=True,
        )

    # Returns the manifest version without replacing the binary.
    assert result == updater.__version__
    assert dummy_bin.read_bytes() == b"old"


def test_perform_update_refuses_brew_cellar():
    brew_path = "/opt/homebrew/Cellar/cleanup-mac/0.3.0/bin/cleanup-mac"
    body = json.dumps(_minimal_manifest(version="99.0.0")).encode()
    with (
        patch("urllib.request.urlopen", return_value=_mock_urlopen(body)),
        patch("platform.machine", return_value="arm64"),
    ):
        with pytest.raises(UpdateError, match="package-manager-managed"):
            updater.perform_update(
                base_url="https://example.com/latest",
                argv0=brew_path,
                skip_exec=True,
            )


def test_perform_update_sha_mismatch_aborts(tmp_path: Path):
    """Simulate corrupted tarball — update must not touch the install path."""
    manifest_body = json.dumps(_minimal_manifest(version="99.0.0")).encode()
    bogus_tarball = b"not a real tarball"

    install_path = tmp_path / "cleanup-mac"
    install_path.write_bytes(b"OLD-BINARY-BYTES")

    # Two urlopen calls: first the manifest, second the tarball download.
    # Use a counter to return different bodies.
    responses = iter([_mock_urlopen(manifest_body), _mock_urlopen(bogus_tarball)])

    def fake_urlopen(req, **kwargs):
        return next(responses)

    with (
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
        patch("platform.machine", return_value="arm64"),
    ):
        with pytest.raises(UpdateError, match="sha256 mismatch"):
            updater.perform_update(
                base_url="https://example.com/latest",
                argv0=str(install_path),
                skip_exec=True,
                yes=True,  # skip the new pre-download confirmation prompt
            )

    # Critical: the old binary is untouched.
    assert install_path.read_bytes() == b"OLD-BINARY-BYTES"


# --- check_update --------------------------------------------------------

def test_check_update_returns_newer_true(tmp_path: Path):
    body = json.dumps(_minimal_manifest(version="99.0.0")).encode()
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(body)):
        newer, latest = updater.check_update(base_url="https://example.com/latest")
    assert newer is True
    assert latest == "99.0.0"


def test_check_update_returns_newer_false_on_same(tmp_path: Path):
    body = json.dumps(_minimal_manifest(version=updater.__version__)).encode()
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(body)):
        newer, latest = updater.check_update(base_url="https://example.com/latest")
    assert newer is False
    assert latest == updater.__version__


# --- HTTPS enforcement (security) ---------------------------------------

def test_require_https_accepts_https():
    _require_https("https://example.com/latest")  # no raise


def test_require_https_rejects_http():
    with pytest.raises(UpdateError, match="must use HTTPS"):
        _require_https("http://example.com/latest")


def test_require_https_rejects_file_scheme():
    with pytest.raises(UpdateError, match="must use HTTPS"):
        _require_https("file:///tmp/local-manifest")


def test_fetch_manifest_rejects_http_base():
    with pytest.raises(UpdateError, match="must use HTTPS"):
        fetch_manifest("http://attacker.example/latest")


def test_check_update_rejects_http_base():
    with pytest.raises(UpdateError, match="must use HTTPS"):
        updater.check_update(base_url="http://attacker.example/latest")


def test_perform_update_rejects_http_base():
    with pytest.raises(UpdateError, match="must use HTTPS"):
        updater.perform_update(
            base_url="http://attacker.example/latest",
            argv0="/tmp/cleanup-mac",
            skip_exec=True,
        )


# --- sudo refusal (security) ---------------------------------------------

def test_perform_update_refuses_root():
    """Downloading + replacing a binary as root amplifies any TLS/MITM
    failure into a root RCE. Refuse the operation explicitly."""
    with patch("os.geteuid", return_value=0):
        with pytest.raises(UpdateError, match="refusing to run --update as root"):
            updater.perform_update(
                base_url="https://example.com/latest",
                argv0="/usr/local/bin/cleanup-mac",
                skip_exec=True,
            )


# --- notarization verification ------------------------------------------

def test_verify_notarized_raises_on_codesign_failure(tmp_path: Path):
    """codesign's exit is the first and most important gate — if a
    downloaded binary's signature doesn't match the pinned team-ID,
    the update must abort, not continue to spctl or atomic_replace."""
    import subprocess as sp

    fake_bin = tmp_path / "cleanup-mac"
    fake_bin.write_bytes(b"FAKE")

    def fake_run(cmd, **kwargs):
        if cmd[0].endswith("codesign"):
            raise sp.CalledProcessError(1, cmd, stderr="code object is not signed")
        raise AssertionError("spctl must not be called after codesign failure")

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(UpdateError, match="codesign verification failed"):
            verify_notarized(fake_bin)


def test_verify_notarized_raises_on_spctl_failure(tmp_path: Path):
    import subprocess as sp

    fake_bin = tmp_path / "cleanup-mac"
    fake_bin.write_bytes(b"FAKE")

    def fake_run(cmd, **kwargs):
        # codesign succeeds; spctl rejects.
        if cmd[0].endswith("codesign"):
            completed = sp.CompletedProcess(cmd, 0, stdout="", stderr="")
            return completed
        if cmd[0].endswith("spctl"):
            raise sp.CalledProcessError(1, cmd, stderr="rejected")
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(UpdateError, match="spctl notarization check failed"):
            verify_notarized(fake_bin)


# --- update prompt (consent before replacement) -------------------------

def test_perform_update_declined_by_user_no_download(tmp_path: Path):
    """User answering 'n' at the prompt must leave the install path
    untouched and raise UpdateDeclined (maps to exit 3 in cli)."""
    import io

    from cleanup_mac.updater import UpdateDeclined

    class _TTYStdin(io.StringIO):
        def isatty(self):
            return True

    install_path = tmp_path / "cleanup-mac"
    install_path.write_bytes(b"OLD-BINARY-BYTES")
    manifest_body = json.dumps(_minimal_manifest(version="99.0.0")).encode()

    with (
        patch("urllib.request.urlopen", return_value=_mock_urlopen(manifest_body)),
        patch("platform.machine", return_value="arm64"),
    ):
        with pytest.raises(UpdateDeclined, match="declined"):
            updater.perform_update(
                base_url="https://example.com/latest",
                argv0=str(install_path),
                skip_exec=True,
                in_stream=_TTYStdin("n\n"),
                out_stream=io.StringIO(),
            )

    # Binary untouched — no download attempted after decline.
    assert install_path.read_bytes() == b"OLD-BINARY-BYTES"


def test_perform_update_non_tty_without_yes_refuses(tmp_path: Path):
    """Scripted `--update` without `--yes` must refuse instead of
    auto-approving — a cron that silently swaps its own binary is a
    footgun we don't enable."""
    import io

    from cleanup_mac.updater import UpdateDeclined

    install_path = tmp_path / "cleanup-mac"
    install_path.write_bytes(b"OLD-BINARY-BYTES")
    manifest_body = json.dumps(_minimal_manifest(version="99.0.0")).encode()
    stdin_nontty = io.StringIO("")  # isatty() is False on StringIO

    with (
        patch("urllib.request.urlopen", return_value=_mock_urlopen(manifest_body)),
        patch("platform.machine", return_value="arm64"),
    ):
        with pytest.raises(UpdateDeclined):
            updater.perform_update(
                base_url="https://example.com/latest",
                argv0=str(install_path),
                skip_exec=True,
                in_stream=stdin_nontty,
                out_stream=io.StringIO(),
            )
    assert install_path.read_bytes() == b"OLD-BINARY-BYTES"


def test_perform_update_yes_skips_prompt(tmp_path: Path):
    """`--yes` must skip the prompt and let the update flow proceed to
    the next gate (SHA verification here, which fails with a bogus
    tarball — that proves the prompt did not short-circuit)."""
    import io

    manifest_body = json.dumps(_minimal_manifest(version="99.0.0")).encode()
    bogus = b"not-a-real-tarball"
    responses = iter([_mock_urlopen(manifest_body), _mock_urlopen(bogus)])

    install_path = tmp_path / "cleanup-mac"
    install_path.write_bytes(b"OLD")

    with (
        patch("urllib.request.urlopen", side_effect=lambda *a, **k: next(responses)),
        patch("platform.machine", return_value="arm64"),
    ):
        with pytest.raises(UpdateError, match="sha256 mismatch"):
            updater.perform_update(
                base_url="https://example.com/latest",
                argv0=str(install_path),
                skip_exec=True,
                yes=True,
                in_stream=io.StringIO(""),  # would refuse if yes weren't True
                out_stream=io.StringIO(),
            )


def test_verify_notarized_uses_team_id_requirement(tmp_path: Path):
    """The codesign verify call must include `-R` with the team-ID
    requirement string — otherwise any notarized Developer ID would pass."""
    import subprocess as sp

    fake_bin = tmp_path / "cleanup-mac"
    fake_bin.write_bytes(b"FAKE")

    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(list(cmd))
        return sp.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        verify_notarized(fake_bin)

    codesign_cmd = captured[0]
    assert "-R" in codesign_cmd, "codesign must pin a requirement"
    idx = codesign_cmd.index("-R")
    assert updater.UPSTREAM_TEAM_ID in codesign_cmd[idx + 1]

    spctl_cmd = captured[1]
    # `--type install` is empirically the only type that accepts a bare
    # notarized Mach-O on modern macOS. See verify_notarized() docstring
    # for the full rationale; in short, `--type execute` rejects because
    # it expects a .app bundle.
    assert "install" in spctl_cmd, "spctl must use --type install for bare Mach-O"
    assert "execute" not in spctl_cmd, "--type execute rejects bare Mach-O"
