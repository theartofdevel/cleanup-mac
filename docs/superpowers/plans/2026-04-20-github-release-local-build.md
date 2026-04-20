# GitHub Release — Local Build Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate release pipeline from self-hosted CI to local-build + GitHub Releases for hosting, with a separate minimal GitHub Actions workflow that only runs tests on push/PR.

**Architecture:** Maintainer runs `make publish BUMP=patch|minor|major` locally — bumps version, commits, builds both archs via existing `make release`, generates `manifest.json`, tags, pushes commit+tag, and creates GitHub Release with all assets via `gh`. Origin is never touched until artifacts are built and verified. CI is reduced to pytest + ruff on `macos-14` GH-hosted runner.

**Tech Stack:** Python 3.12 stdlib (no new deps for the release pipeline), GitHub CLI (`gh`), existing Nuitka/codesign/notarytool pipeline from `Makefile`, GitHub Actions.

---

## File Structure

**New files:**
- `.github/workflows/tests.yml` — CI workflow (pytest + ruff, macos-14).
- `scripts/gen-manifest.py` — generates `manifest.json` for `--update` consumption.
- `tests/test_gen_manifest.py` — exercises the manifest generator.

**Modified files:**
- `Makefile` — new `manifest` target; `publish` target rewritten to drive the full local flow.
- `scripts/bump-version.sh` — trimmed to bump + commit only (no tag, no push).
- `README.md` — "Releasing (maintainer notes)" section rewritten; self-hosted runner / GH secrets subsections removed.

**Deletions (already staged in git — committed as part of this migration):**
- `.gitea/certs/selectel-root-r6.crt`
- `.gitea/workflows/release.yml`
- `scripts/s3-cleanup.sh`
- `scripts/promote_changelog.py`
- `tests/test_promote_changelog.py`
- `CHANGELOG.md`
- `SECURITY.md`
- `docs/` (entire tree — `.gitignore` now covers it).

---

## Task 1: Add `.gitignore` entry for `docs/` (already done in conversation, verify only)

**Files:**
- Modify: `.gitignore:73-77`

- [ ] **Step 1: Verify `.gitignore` lists `docs/`**

Run: `grep '^docs/' .gitignore`
Expected output:
```
docs/
```

If missing, append this block at the end of the "Agent / planning scaffolding" section:
```
docs/
```

- [ ] **Step 2: Verify git does not track the plan file itself**

Run: `git check-ignore -v docs/superpowers/plans/2026-04-20-github-release-local-build.md`
Expected: a line showing the `docs/` rule in `.gitignore` is ignoring the file.

No commit for this step — change is already in working tree alongside other edits.

---

## Task 2: CI workflow — pytest + ruff on `macos-14`

**Files:**
- Create: `.github/workflows/tests.yml`

- [ ] **Step 1: Create the workflow file**

```yaml
name: Tests

on:
  push:
    branches: ["**"]
    tags-ignore: ["**"]          # tag pushes are release-only, no duplicate test run
  pull_request:
    branches: [main]

concurrency:
  group: tests-${{ github.ref }}
  cancel-in-progress: true

jobs:
  test:
    runs-on: macos-14            # arm64, GitHub-hosted
    timeout-minutes: 15

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Install dev dependencies
        run: |
          python -m pip install --upgrade pip
          pip install pytest ruff

      - name: Lint
        run: ruff check cleanup_mac/ tests/ scripts/

      - name: Test
        run: python -m pytest -q
```

Key choices:
- `tags-ignore: ["**"]` prevents tests re-running on the release tag push (commit is already tested when it landed on `main`).
- `concurrency` cancels older in-progress runs on the same ref.
- `timeout-minutes: 15` bounds flaky hangs; full suite today runs <60s on M-series.

- [ ] **Step 2: Validate YAML syntax locally**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/tests.yml'))"`
Expected: no output (success). If `yaml` is missing: `pip install pyyaml` first.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/tests.yml
git commit -m "ci: add tests workflow (pytest + ruff on macos-14)"
```

---

## Task 3: `scripts/gen-manifest.py` — write failing tests first

**Files:**
- Create: `tests/test_gen_manifest.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for scripts/gen-manifest.py.

The generator writes manifest.json consumed at update time by
cleanup_mac.updater.fetch_manifest. We test two contracts:

1. The output is parseable by updater.fetch_manifest without error
   (schema drift guard).
2. The generator refuses to run when any expected artifact is missing.
"""

from __future__ import annotations

import hashlib
import io
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

        def __enter__(self) -> "_FakeResp":
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
```

- [ ] **Step 2: Run tests — they must all fail**

Run: `.venv/bin/python -m pytest tests/test_gen_manifest.py -v`
Expected: 4 FAILED (script does not yet exist — pytest reports non-zero return codes / missing file).

---

## Task 4: `scripts/gen-manifest.py` — implementation

**Files:**
- Create: `scripts/gen-manifest.py`

- [ ] **Step 1: Write the generator**

```python
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
from datetime import datetime, timezone
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

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "version": version,
        # ISO-8601 UTC, second-precision (no fractional) to match the
        # style updater.py prints back to the user.
        "released_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "artifacts": artifacts,
    }

    out = release_dir / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"wrote: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/gen-manifest.py
```

- [ ] **Step 3: Run tests — they must all pass**

Run: `.venv/bin/python -m pytest tests/test_gen_manifest.py -v`
Expected: 4 PASSED.

- [ ] **Step 4: Run ruff on the new files**

Run: `ruff check scripts/gen-manifest.py tests/test_gen_manifest.py`
Expected: no issues. Fix any.

- [ ] **Step 5: Commit**

```bash
git add scripts/gen-manifest.py tests/test_gen_manifest.py
git commit -m "feat(release): add manifest.json generator

The old Gitea workflow inlined this logic; moving to local builds
means we need a dedicated script. Tests round-trip the output
through cleanup_mac.updater.fetch_manifest as a schema-drift guard."
```

---

## Task 5: Trim `scripts/bump-version.sh` to bump + commit

**Files:**
- Modify: `scripts/bump-version.sh` (current: bump + commit + tag + push; target: bump + commit only).

- [ ] **Step 1: Replace the full file**

```bash
#!/usr/bin/env bash
# Bump __version__ in cleanup_mac/_version.py by one semver component
# and commit. Tag + push happen later in `make publish`, after the
# release build succeeds — if the build fails, origin stays clean.
#
# Refuses to run on a dirty tree so the release commit carries only
# the version bump.
#
# Usage: bump-version.sh {patch|minor|major}

set -euo pipefail

BUMP="${1:-}"
case "$BUMP" in
    patch|minor|major) ;;
    *)
        echo "usage: $0 {patch|minor|major}" >&2
        exit 1
        ;;
esac

if ! git diff --quiet HEAD -- 2>/dev/null; then
    echo "error: working tree has uncommitted tracked changes." >&2
    echo "       Commit or stash them first (the release commit must be pure)." >&2
    exit 1
fi
if [ -n "$(git ls-files --others --exclude-standard)" ]; then
    echo "error: working tree has untracked files." >&2
    echo "       Add them or add to .gitignore first." >&2
    git ls-files --others --exclude-standard >&2
    exit 1
fi

VERSION_FILE="cleanup_mac/_version.py"
CURRENT=$(grep '^__version__' "$VERSION_FILE" | cut -d '"' -f 2)
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"

case "$BUMP" in
    patch) PATCH=$((PATCH + 1)) ;;
    minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
    major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
esac

NEW="$MAJOR.$MINOR.$PATCH"

# Catch the case where somebody already bumped locally or a tag collides
# on origin — we want to know now, not after 10 min of Nuitka.
if git rev-parse -q --verify "refs/tags/v${NEW}" >/dev/null; then
    echo "error: tag v${NEW} already exists locally" >&2
    exit 1
fi
if git ls-remote --tags origin "refs/tags/v${NEW}" | grep -q "v${NEW}"; then
    echo "error: tag v${NEW} already exists on origin" >&2
    exit 1
fi

echo "==> bumping version: $CURRENT → $NEW"
sed -i '' "s/^__version__ = \".*\"/__version__ = \"$NEW\"/" "$VERSION_FILE"

ACTUAL=$(grep '^__version__' "$VERSION_FILE" | cut -d '"' -f 2)
if [ "$ACTUAL" != "$NEW" ]; then
    echo "error: expected __version__ == $NEW after sed, got $ACTUAL" >&2
    exit 1
fi

echo "==> committing (git commit -am)"
git commit -am "bump to $NEW"

cat <<EOF

Version bumped locally to $NEW (commit $(git rev-parse --short HEAD)).
Next: run 'make release' to build+sign, then 'make publish-finish' to
tag, push, and create the GitHub release.
EOF
```

Note: the script no longer tags or pushes. The last line points at `make publish-finish` (added in Task 6) — this is the intentional split so a maintainer can debug a failed `make release` locally without origin pollution.

- [ ] **Step 2: Verify it still lints cleanly**

Run: `ruff check scripts/` (ruff also checks shell? no — ruff is Python only; but lint target already runs it on scripts/). Run: `shellcheck scripts/bump-version.sh` if shellcheck is installed — otherwise skip.
Expected: no errors.

- [ ] **Step 3: Dry-run check — make sure it's still executable and syntax-valid**

Run: `bash -n scripts/bump-version.sh`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add scripts/bump-version.sh
git commit -m "refactor(release): bump-version.sh only bumps+commits

Tag + push are now owned by 'make publish-finish' so they run after
the local build+sign+notarize succeeds. Prevents orphan tags on
origin when Nuitka or notarytool fails."
```

---

## Task 6: `Makefile` — `manifest` target + rewritten `publish` flow

**Files:**
- Modify: `Makefile` (several targets).

- [ ] **Step 1: Update `.PHONY` line**

Find the current `.PHONY` at `Makefile:1-9`. Replace with:

```makefile
.PHONY: install uninstall test lint clean \
        build-venv _check-build-python \
        build build-arm64 build-x86_64 \
        build-signed-arm64 build-signed-x86_64 \
        notarize-arm64 notarize-x86_64 \
        tarball-arm64 tarball-x86_64 \
        pkg-arm64 pkg-x86_64 \
        manifest \
        release release-arm64 release-x86_64 \
        publish publish-finish
```

- [ ] **Step 2: Add `manifest` target — insert after `release` target (around line 283)**

Find the `release` target. Immediately after its block, insert:

```makefile
# Generate manifest.json from sha256 sidecars already produced by the
# tarball-* / pkg-* targets. Depends on `release` so the full asset
# set is on disk first. Schema defined in cleanup_mac/updater.py;
# tests/test_gen_manifest.py round-trips the output through the
# parser as a drift guard.
manifest: release
	python3 scripts/gen-manifest.py
	@test -f $(RELEASE_DIR)/manifest.json || (echo "error: manifest.json not produced"; exit 1)
	@echo "manifest.json ready: $(RELEASE_DIR)/manifest.json"
```

- [ ] **Step 3: Rewrite `publish` target — replace lines that currently start at `# ---------- Trigger a CI release ----------` through end of file**

Find the `publish:` rule (currently at `Makefile:293-299`) and the preceding comment block (`# ---------- Trigger a CI release ----------`). Replace both with:

```makefile
# ---------- Full local release ----------
#
# `make publish BUMP=patch|minor|major` is the maintainer entry point.
# The flow is split so `origin` is untouched until every artifact is
# built + signed + notarized:
#
#   1. scripts/bump-version.sh — bumps __version__, commits locally
#      (no tag, no push).
#   2. make release — builds both archs, signs with Developer ID,
#      notarizes, produces .tar.gz + .pkg + .sha256 files.
#   3. make manifest — generates manifest.json from the sha256 sidecars.
#   4. make publish-finish — tags, pushes commit+tag, creates GitHub
#      release with all assets.
#
# If step 2 or 3 fails (Nuitka crash, notarytool stall, bad sidecar),
# nothing has touched origin — fix locally and rerun from `make release`.

publish:
	@test -n "$(BUMP)" || (echo "error: set BUMP=patch|minor|major (e.g. 'make publish BUMP=patch')"; exit 1)
	@./scripts/bump-version.sh "$(BUMP)"
	@$(MAKE) release
	@$(MAKE) manifest
	@$(MAKE) publish-finish

# publish-finish runs after a successful local build. It assumes
# the bump commit is already at HEAD and every release artifact
# (including manifest.json) is in $(RELEASE_DIR).
#
# Separated from `publish` so a maintainer can rerun just this step
# if the network flaked on `gh release create` (idempotent — GH will
# reject a duplicate release creation, but asset upload is retriable
# via `gh release upload --clobber`).
publish-finish:
	@command -v gh >/dev/null 2>&1 || (echo "error: 'gh' CLI not found. Install with: brew install gh && gh auth login"; exit 1)
	@gh auth status >/dev/null 2>&1 || (echo "error: 'gh' not authenticated. Run: gh auth login"; exit 1)
	@test -f $(RELEASE_DIR)/manifest.json || (echo "error: $(RELEASE_DIR)/manifest.json missing — run 'make manifest' first"; exit 1)
	@test -f $(TARBALL_ARM64) || (echo "error: $(TARBALL_ARM64) missing — run 'make release' first"; exit 1)
	@test -f $(TARBALL_X86_64) || (echo "error: $(TARBALL_X86_64) missing — run 'make release' first"; exit 1)
	@test -f $(PKG_ARM64) || (echo "error: $(PKG_ARM64) missing — run 'make release' first"; exit 1)
	@test -f $(PKG_X86_64) || (echo "error: $(PKG_X86_64) missing — run 'make release' first"; exit 1)
	@# Verify HEAD's __version__ matches what the artifacts were built for.
	@# If bump-version.sh committed v=1.1.5 but dist/release/ holds 1.1.4
	@# files (stale build), abort — publishing would attach wrong-version
	@# binaries to a right-version tag.
	@HEAD_VER=$$(grep '^__version__' $(VERSION_FILE) | cut -d '"' -f 2); \
	if [ "$$HEAD_VER" != "$(VERSION)" ]; then \
	    echo "error: HEAD __version__=$$HEAD_VER, Makefile VERSION=$(VERSION) — reload with 'make -B publish-finish' or rebuild"; \
	    exit 1; \
	fi
	@echo "==> tagging v$(VERSION)"
	git tag -a "v$(VERSION)" -m "Release v$(VERSION)"
	@echo "==> pushing HEAD and tag to origin"
	git push origin HEAD --follow-tags
	@echo "==> creating GitHub release v$(VERSION)"
	gh release create "v$(VERSION)" \
	    --title "v$(VERSION)" \
	    --generate-notes \
	    $(TARBALL_ARM64) $(TARBALL_ARM64).sha256 \
	    $(TARBALL_X86_64) $(TARBALL_X86_64).sha256 \
	    $(PKG_ARM64) $(PKG_ARM64).sha256 \
	    $(PKG_X86_64) $(PKG_X86_64).sha256 \
	    $(RELEASE_DIR)/manifest.json
	@echo ""
	@echo "=============================================================="
	@echo "  Release v$(VERSION) published: https://github.com/theartofdevel/cleanup-mac/releases/tag/v$(VERSION)"
	@echo "=============================================================="
```

Key points to highlight in the code review:
- `publish-finish` re-checks every precondition because it's designed to be rerunnable standalone.
- Version drift guard: if `_version.py` disagrees with what Makefile read into `VERSION`, abort.
- `gh release create` lists every file explicitly — globbing `dist/release/*` would also upload any stale files from a previous release run that happen to linger.

- [ ] **Step 4: Lint-check the Makefile**

Run: `make -n publish BUMP=patch 2>&1 | head -50`
Expected: prints the command chain without executing (assuming clean tree — if tree is dirty, `bump-version.sh` will error, that's fine for this check). At minimum, syntax must parse — any `make: *** missing separator` would fail immediately.

- [ ] **Step 5: Commit**

```bash
git add Makefile
git commit -m "build(release): rewrite publish flow for local build + GH release

- 'make publish' now drives the full local pipeline: bump, build,
  sign, notarize, manifest, tag, push, gh release create.
- New 'make manifest' target runs scripts/gen-manifest.py.
- New 'make publish-finish' step is rerunnable if network flakes on
  the 'gh release create' call.
- origin is never touched until every artifact is verified locally."
```

---

## Task 7: Rewrite README "Releasing (maintainer notes)" section

**Files:**
- Modify: `README.md:275-404` (the "Releasing (maintainer notes)" section).

- [ ] **Step 1: Replace the entire section**

Find the `## Releasing (maintainer notes)` heading and everything up to (but not including) the next top-level section (`## Design` at line 405). Replace the range with:

```markdown
## Releasing (maintainer notes)

Builds and signs happen on the maintainer's Mac. CI only runs tests.
GitHub Releases hosts the artifacts; `manifest.json` attached to each
release drives `cleanup-mac --update`.

### One-time setup

1. **Developer ID certificates** (free with Apple Developer Program
   membership, both in login keychain):
   - **Developer ID Application** — signs the Mach-O binary.
   - **Developer ID Installer** — signs the `.pkg` installer.

   Verify:
   ```
   security find-identity -v -p codesigning   # Application cert
   security find-identity -v                  # both appear
   ```

2. **Notarytool credentials profile** (one-time):

       xcrun notarytool store-credentials cleanup-mac-notary \
           --apple-id you@example.com \
           --team-id YOURTEAMID \
           --password <app-specific-password>

   App-specific password: <https://appleid.apple.com> → Sign-In and
   Security → App-Specific Passwords.

3. **Universal2 Python 3.12** (python.org installer, not Homebrew —
   Homebrew's Python is single-arch):

   Download <https://www.python.org/downloads/macos/> → "macOS 64-bit
   universal2 installer". Installs at `/usr/local/bin/python3.12`.
   Verify:
   ```
   lipo -info /usr/local/bin/python3.12
   # Architectures in the fat file: x86_64 arm64
   ```

4. **Build venv** (idempotent — re-run is a no-op):

       make build-venv

5. **GitHub CLI + auth**:

       brew install gh
       gh auth login    # choose HTTPS, browser auth

6. **Dev venv** (for `pytest` / `ruff`):

       python3 -m venv .venv
       .venv/bin/pip install pytest ruff

### Each release (one command)

    make publish BUMP=patch        # 1.0.0 → 1.0.1
    make publish BUMP=minor        # 1.0.0 → 1.1.0
    make publish BUMP=major        # 1.0.0 → 2.0.0

What this does end-to-end, in order:

1. `scripts/bump-version.sh` bumps `__version__` and makes a local
   commit. Refuses to run on a dirty tree or if the target tag
   already exists locally or on origin.
2. `make release` builds arm64 and x86_64 via Nuitka + universal2
   Python, signs each with Developer ID Application, notarizes,
   packages as `.tar.gz` + `.pkg`, staples the `.pkg`.
3. `make manifest` runs `scripts/gen-manifest.py` and writes
   `dist/release/manifest.json` matching the schema in
   `cleanup_mac/updater.py`.
4. `make publish-finish` creates the annotated tag `vX.Y.Z`, pushes
   commit + tag to origin, and runs `gh release create vX.Y.Z`
   attaching all 9 assets (per-arch tarball + sha256 + pkg + sha256,
   plus one shared `manifest.json`).

Until step 4, **nothing has reached origin** — if Nuitka crashes
or notarytool stalls, reset local with `git reset --hard HEAD^`
and retry. If `gh release create` flakes on the network, rerun
just `make publish-finish` — it's idempotent modulo the `gh release
create` call itself (which will then fail on duplicate tag — in
that case, use `gh release upload vX.Y.Z dist/release/* --clobber`).

### CI (tests-only)

`.github/workflows/tests.yml` runs `ruff check` + `pytest` on
`macos-14` (GitHub-hosted Apple Silicon). Triggers on every push to
any branch (except tag pushes, which are release-triggered and
already tested on `main`) and on pull requests targeting `main`.

No secrets, no self-hosted runners, no signing in CI — the signing
path is entirely local.

### Notes on Gatekeeper and stapling

A plain Mach-O binary cannot carry a stapled notarization ticket —
stapling only works for `.app`, `.dmg`, `.pkg`, and similar container
formats. This pipeline therefore ships an un-stapled binary and relies
on Gatekeeper's *online* notarization lookup: on first execution
macOS queries Apple for the binary's ticket, caches the approval, and
runs without a prompt. The only case where this is visible to the
user is a completely offline first run — rare for a tool that already
depends on network-ready packages like `brew`. If offline-first is
required, use the `.pkg` (which is stapled).
```

- [ ] **Step 2: Double-check nothing broke by re-reading the boundaries**

Run: `grep -n '^## ' README.md`
Expected: `Releasing (maintainer notes)` appears exactly once, and is immediately followed (in line order) by `## Design`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): rewrite Releasing section for local-build flow

Self-hosted runner and GitHub secrets subsections are gone — the
release pipeline now runs entirely on the maintainer's Mac, and CI
is tests-only."
```

---

## Task 8: Commit the pre-staged deletions (gitea/, s3/, changelog, security, docs/)

**Files:**
- Already deleted in working tree (staged `D` in git status at conversation start):
  - `.gitea/certs/selectel-root-r6.crt`
  - `.gitea/workflows/release.yml`
  - `CHANGELOG.md`
  - `SECURITY.md`
  - `docs/LOG_SCHEMA.md`, `docs/TELEMETRY.md`, `docs/design.md`
  - `scripts/promote_changelog.py`
  - `scripts/s3-cleanup.sh`
  - `tests/test_promote_changelog.py`
- Modified but not yet committed (check at execution time):
  - `CONTRIBUTING.md`, `cleanup_mac/_version.py`, `cleanup_mac/updater.py`

- [ ] **Step 1: Inspect what's still pending**

Run: `git status --short`
Expected: the `D` entries above, plus any `M` entries you haven't touched.

If any `M` entries are NOT related to the other modified files intentionally left for this task (e.g. stray unrelated edits), stop and ask before proceeding.

- [ ] **Step 2: Verify the modified files are coherent with this migration**

Run: `git diff cleanup_mac/updater.py | head -40`
Expected: the `DEFAULT_UPDATE_BASE` already points at
`https://github.com/theartofdevel/cleanup-mac/releases/latest/download`
(which this plan depends on). If it doesn't, STOP and investigate —
the updater must target GitHub Releases for this migration to work.

Run: `git diff CONTRIBUTING.md`
Expected: changes aligned with the local-build flow. If they reference
the deleted Gitea workflow or S3, note them for Task 9 follow-up.

- [ ] **Step 3: Stage and commit the cleanup**

```bash
git add -u                   # picks up all D and M entries under version control
git status --short           # sanity: nothing left except intended files
git commit -m "chore(release): remove legacy Gitea/S3/changelog scaffolding

Migration to local-build + GitHub Releases makes these obsolete:
- .gitea/ workflow and Selectel cert (no more self-hosted CI).
- scripts/s3-cleanup.sh + promote_changelog.py (no S3 mirror,
  no hand-maintained CHANGELOG).
- docs/ (tree is now ignored via .gitignore).
- CHANGELOG.md, SECURITY.md (superseded by git history and GH
  security advisories).

Release prerequisites and flow documented in README."
```

---

## Task 9: End-to-end smoke test of the new flow (no actual release)

**Files:** none created.

This task verifies the new pipeline works without publishing a real release. We use a throwaway scratch tag to exercise every step.

- [ ] **Step 1: Verify `gh` is authenticated**

Run: `gh auth status`
Expected: "Logged in to github.com account ...".

- [ ] **Step 2: Dry-run `make publish` on a scratch branch**

Create a scratch branch so any bump commit can be thrown away:

```bash
git checkout -b scratch-release-dryrun
```

Run (WARNING: this will actually build — takes 5-10 min):

```bash
make release
make manifest
```

Expected: `dist/release/` contains 9 files — 4 per arch (`.tar.gz`,
`.tar.gz.sha256`, `.pkg`, `.pkg.sha256`) + `manifest.json`.

- [ ] **Step 3: Validate manifest round-trips through updater**

```bash
.venv/bin/python -c "
import json, urllib.request
from pathlib import Path
from cleanup_mac import updater

body = Path('dist/release/manifest.json').read_bytes()

class F:
    def __init__(self, d): self.d = d
    def read(self): return self.d
    def __enter__(self): return self
    def __exit__(self, *a): return None

urllib.request.urlopen = lambda req, timeout=0: F(body)
m = updater.fetch_manifest('https://example.test/x')
print(f'OK: v={m.version} archs={sorted(m.artifacts)}')
"
```

Expected: `OK: v=<current_version> archs=['arm64', 'x86_64']`

- [ ] **Step 4: Validate every Mach-O and .pkg signature**

```bash
for f in dist/release/*.tar.gz; do
    tmp=$(mktemp -d)
    tar -xzf "$f" -C "$tmp"
    codesign --verify --deep --strict --verbose=2 "$tmp/cleanup-mac"
    spctl --assess --type install --verbose=4 "$tmp/cleanup-mac"
    rm -rf "$tmp"
done
for f in dist/release/*.pkg; do
    pkgutil --check-signature "$f" | grep -q "signed by a certificate trusted" \
        || (echo "pkg signature bad: $f"; exit 1)
    stapler validate "$f"
done
```

Expected: every file reports valid signature / stapled ticket. Any failure aborts.

- [ ] **Step 5: Cleanup scratch branch**

```bash
git checkout main                      # return to main
git branch -D scratch-release-dryrun   # discard the scratch bump commit
rm -rf dist/release                    # the real publish will rebuild these
```

No commit for this task — purely verification.

---

## Task 10: Final lint + test sweep before handoff

**Files:** none.

- [ ] **Step 1: Full lint**

```bash
ruff check cleanup_mac/ tests/ scripts/
```

Expected: no issues.

- [ ] **Step 2: Full test suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass — including the new `test_gen_manifest.py` quartet.

- [ ] **Step 3: Verify git history is clean**

Run: `git log --oneline origin/main..HEAD`
Expected: a linear series of focused commits —
1. `ci: add tests workflow (pytest + ruff on macos-14)`
2. `feat(release): add manifest.json generator`
3. `refactor(release): bump-version.sh only bumps+commits`
4. `build(release): rewrite publish flow for local build + GH release`
5. `docs(readme): rewrite Releasing section for local-build flow`
6. `chore(release): remove legacy Gitea/S3/changelog scaffolding`

No merge commits, no "WIP" entries, no unrelated changes.

- [ ] **Step 4: Push branch for review (if working on a branch)**

```bash
git push origin HEAD
gh pr create --fill
```

If working directly on `main` (solo project), skip and the first real
`make publish` will push the bump commit plus all migration commits
together.

---

## Self-Review Notes

**Spec coverage check:**
- Section 1 (CI workflow) → Task 2 ✓
- Section 2 (local release flow) → Tasks 5, 6 ✓
- Section 3 (gen-manifest.py) → Tasks 3, 4 ✓
- Section 4 (README updates) → Task 7 ✓
- Section 5 (file structure + deletions) → Tasks 1, 8 ✓
- End-to-end validation → Task 9 ✓

**Placeholder scan:** no TBD/TODO/"handle edge cases" left; every code block is a complete file or complete diff.

**Type consistency:** manifest schema (`schema_version`, `version`, `released_at`, `artifacts[arch].{tarball,tarball_sha256,pkg,pkg_sha256}`) is identical across `updater.py:80-94`, `scripts/gen-manifest.py` (Task 4), and `tests/test_gen_manifest.py` (Task 3). Makefile variable names (`TARBALL_ARM64`, `PKG_ARM64`, `RELEASE_DIR`, `VERSION_FILE`, `VERSION`) all refer to existing definitions at the top of `Makefile`.
