"""Tests for scan_package_managers."""

from __future__ import annotations

import io
from unittest.mock import patch

from cleanup_mac import scan_package_managers


def test_skips_missing_tools():
    """If no tools are installed, returns empty list, does not crash."""
    with patch("shutil.which", return_value=None):
        result = scan_package_managers()
    assert result == []


def test_brew_detected(tmp_path):
    def fake_which(name):
        return "/opt/homebrew/bin/brew" if name == "brew" else None

    with (
        patch("shutil.which", side_effect=fake_which),
        patch("cleanup_mac._util.get_size", return_value=2_000_000_000),
    ):
        result = scan_package_managers()

    brews = [p for p in result if p.tool == "brew"]
    assert len(brews) == 1
    assert brews[0].current_size_bytes == 2_000_000_000
    # apply_command uses the absolute resolved path (not just "brew") so
    # subsequent subprocess.run cannot be $PATH-redirected at apply time.
    assert brews[0].apply_command[0].endswith("/brew")
    assert brews[0].apply_command[1:2] == ["cleanup"]


def test_docker_only_under_aggressive():
    def fake_which(name):
        return "/usr/local/bin/docker" if name == "docker" else None

    with (
        patch("shutil.which", side_effect=fake_which),
        # On the developer's machine /usr/local/bin/docker can be a
        # symlink into /Applications/Docker.app — which would make
        # _resolve_trusted_binary reject it. Stub realpath to isolate
        # the test from local install state.
        patch("os.path.realpath", side_effect=lambda p: p),
        patch("cleanup_mac._util.get_size", return_value=10_000_000),
    ):
        non_agg = scan_package_managers(aggressive=False)
        agg = scan_package_managers(aggressive=True)

    assert not any(p.tool == "docker" for p in non_agg)
    assert any(p.tool == "docker" for p in agg)


def test_all_tools_detected_when_all_installed():
    def fake_which(name):
        return f"/usr/bin/{name}"

    with (
        patch("shutil.which", side_effect=fake_which),
        patch("os.path.realpath", side_effect=lambda p: p),
        patch("cleanup_mac._util.get_size", return_value=100_000_000),
    ):
        result = scan_package_managers(aggressive=True)

    tools = {p.tool for p in result}
    assert {"brew", "npm", "yarn", "pip", "go", "docker"}.issubset(tools)


def test_refuses_binary_from_untrusted_path(tmp_path):
    """A PATH-resolved binary outside TRUSTED_BIN_PREFIXES (e.g. a
    hostile shim in ~/.local/bin) must be skipped, not invoked."""
    attacker_shim = str(tmp_path / "evil-bin" / "brew")

    def fake_which(name):
        return attacker_shim if name == "brew" else None

    stderr = io.StringIO()
    with patch("shutil.which", side_effect=fake_which):
        result = scan_package_managers(stderr=stderr)

    assert not any(p.tool == "brew" for p in result)
    # Warning mentions the suspicious path so the user can investigate.
    assert "brew" in stderr.getvalue()
    assert attacker_shim in stderr.getvalue() or "not under a trusted" in stderr.getvalue()


def test_trusted_symlink_followed():
    """A trusted-prefix path that is itself a symlink into e.g.
    Homebrew's Cellar is followed via realpath and should be trusted if
    the final target also lives under a trusted prefix."""
    # Simulate: shutil.which returns /opt/homebrew/bin/brew which is a
    # symlink → /opt/homebrew/Cellar/brew/4.x.y/bin/brew. Both are under
    # /opt/homebrew, so realpath stays trusted.
    def fake_which(name):
        return "/opt/homebrew/bin/brew" if name == "brew" else None

    def fake_realpath(path):
        if path == "/opt/homebrew/bin/brew":
            return "/opt/homebrew/Cellar/brew/4.5.1/bin/brew"
        return path

    with (
        patch("shutil.which", side_effect=fake_which),
        patch("os.path.realpath", side_effect=fake_realpath),
        patch("cleanup_mac._util.get_size", return_value=1_000_000),
    ):
        result = scan_package_managers()

    assert any(p.tool == "brew" for p in result)


def test_warns_when_only_pip_exists_at_untrusted_location(tmp_path):
    """If a user has only `pip` (no `pip3`) installed at an untrusted
    location, the warning must name `pip` — previously we always warned
    about `pip3` so the untrusted `pip` would be silently skipped."""
    attacker_shim = str(tmp_path / "evil-bin" / "pip")

    def fake_which(name):
        # `pip` exists at untrusted path; `pip3` is missing entirely.
        return attacker_shim if name == "pip" else None

    stderr = io.StringIO()
    with patch("shutil.which", side_effect=fake_which):
        result = scan_package_managers(stderr=stderr)

    assert not any(p.tool == "pip" for p in result)
    # The warning must reference the actual binary the user has, not `pip3`.
    out = stderr.getvalue()
    assert attacker_shim in out
    assert " pip " in out or out.startswith("warning: skipping pip ")


def test_untrusted_realpath_rejected_even_if_which_trusted():
    """Defence in depth: /usr/local/bin/brew is a user-writable path on
    some misconfigured systems, but more importantly if its realpath
    escapes trusted prefixes (e.g. a symlink into a user dir), reject."""
    def fake_which(name):
        return "/usr/local/bin/brew" if name == "brew" else None

    def fake_realpath(path):
        if path == "/usr/local/bin/brew":
            return "/Users/victim/.local/bin/brew"
        return path

    stderr = io.StringIO()
    with (
        patch("shutil.which", side_effect=fake_which),
        patch("os.path.realpath", side_effect=fake_realpath),
    ):
        result = scan_package_managers(stderr=stderr)

    assert not any(p.tool == "brew" for p in result)
