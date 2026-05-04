# cleanup-mac manual

Full documentation. [README.md](README.md) has the short version.

## What it cleans

- **User caches** in `~/Library/Caches/`
- **Logs** in `~/Library/Logs/` (DiagnosticReports kept for 30 days)
- **Leftovers** — data folders/files in `~/Library/Application Support/`, `Caches/`, `Containers/`, `Preferences/`, etc. whose bundle ID no longer matches any installed `.app`
- **Xcode junk** — `DerivedData`, `iOS DeviceSupport` (>90 days), `CoreSimulator/Caches`
- **Package manager caches** — `brew cleanup`, `npm cache clean`, `yarn cache clean`, `pip cache purge`, `go clean -cache`

Under `--aggressive`: system caches (`/Library/Caches`, needs sudo), temp files (`/private/var/folders/*/T/`, `/tmp/`), and dangling Docker images (`docker image prune -f` — stopped containers, named volumes, tagged images preserved).

## What it never touches

- `/System`, `/Library/Apple`
- `~/Library/Keychains`, `Mail`, `Messages`, `Safari`, `Mobile Documents`
- iOS backups (`MobileSync`)
- Apple's own `Containers` / `Group Containers`
- Anything in the built-in whitelist (`com.apple.*`, `Adobe*`, `Microsoft*`, `iCloud*`, `JetBrains`, `Google/Chrome`, `SyncedPreferences`)

Extend the whitelist with `--ignore-file path/to/list.txt` (one glob pattern per line).

## Safety model

1. **Dry-run by default.** You must pass `--apply` to delete anything.
2. **Trash by default.** `--apply` moves items to the Finder Trash (recoverable). Pass `--permanent` only when you're sure.
3. **Never-touch paths.** Hardcoded safeguards for Apple, iCloud, Keychains, Mail, Messages, iOS backups — enforced at every deletion.
4. **Path resolution.** All deletions are preceded by `Path.resolve()` + allowed-root check. Symlinks pointing outside allowed roots are refused.
5. **Conservative leftover detection.** Reverse-DNS bundle-ID names are matched against installed `.app` bundle IDs. Plain app names are considered only when a removed bundle-ID leftover provides a strong anchor token, generic tokens such as `desktop` or `client` are ignored, and never-touch/whitelist rules still apply.
6. **No shell interpolation.** Every subprocess invocation is a list of arguments; no `shell=True` anywhere.
7. **Full audit log.** Every run writes to `~/Library/Logs/cleanup-mac/YYYY-MM-DD-HHMMSS.log` unless `--no-log`.

## Verifying the signature

Beyond the SHA-256 checksum, verify Developer ID + notarization:

    codesign --verify --deep --strict --verbose=2 cleanup-mac
    # Expect: "cleanup-mac: valid on disk" and "satisfies its Designated Requirement"

    spctl --assess --type install --verbose=4 cleanup-mac
    # Expect: "cleanup-mac: accepted"
    #         "source=Notarized Developer ID"
    #         "origin=Developer ID Application: Artur Karapetov (D3XP794W84)"

If either check fails, **do not run the binary.** Report via [GitHub Security Advisories](https://github.com/theartofdevel/cleanup-mac/security/advisories/new).

## Shell cache quirk (first run after .pkg install)

Installer drops the binary into `/usr/local/bin` (default `$PATH` on macOS, first entry in `/etc/paths`), so a **new** terminal window sees `cleanup-mac` immediately. Already-open shells cache command lookup — refresh with:

    hash -r     # bash
    rehash      # zsh
    exec $SHELL # either

Not a `$PATH` issue; `which cleanup-mac` in a fresh window confirms `/usr/local/bin/cleanup-mac`.

## Updating

    cleanup-mac --check-update    # exits 0 if current, 1 if newer exists
    cleanup-mac --update          # download, verify, replace, re-exec

`--update` flow:

1. Fetches `manifest.json` from GitHub Releases `latest/download/` redirect (~500 bytes).
2. If running version is already latest, exits without touching anything.
3. Downloads matching tarball for your arch, verifies sha256 against the manifest.
4. Runs `codesign --verify` + `spctl --assess`. Aborts on any failure — no change.
5. Atomic rename over existing install + re-exec into the new binary.

Package-manager installs (Homebrew, MacPorts, pkgsrc) are refused — use your package manager's upgrade command.

`.pkg`-installed binaries at `/usr/local/bin/cleanup-mac` are root-owned. `--update` refuses root execution (MITM risk amplification) — re-download the `.pkg` from GitHub Releases and install via double-click.

Override the upstream mirror:

    CLEANUP_MAC_UPDATE_BASE=https://my-mirror.example.com/cleanup-mac/latest \
        cleanup-mac --update

## Troubleshooting

**"Permission denied" on a specific path.** Some items in `Containers/` are locked by macOS sandbox or SIP. cleanup-mac logs `event=failed` with the path and continues — the rest of the run is unaffected. Check `~/Library/Logs/cleanup-mac/<run>.log` for the full list.

**A file is open and won't move.** cleanup-mac refuses to move files currently held by another process (under `--aggressive` temp-file cleanup, checked via `lsof`). Close the app or wait for it to exit, then re-run.

**Ctrl-C during a run.** Dry-run: safe, nothing to undo. `--apply` mode: the current item either moved to Trash (intact) or didn't (untouched) — atomic per item. Partial runs are logged up to the interruption point.

**"warning: skipping brew — resolved to …, not under a trusted system prefix".** cleanup-mac refuses package managers outside `/opt/homebrew/`, `/usr/local/`, `/usr/bin/`, `/usr/sbin/`, `/bin/`, `/sbin/` — defence against PATH-hijacking. If you installed a package manager elsewhere (`asdf`, `mise`, custom prefix), run its clean command yourself; cleanup-mac will not touch those cache dirs.

**Running with `sudo` (plain, not for `--aggressive`).** Supported but usually unnecessary. `sudo` may set `HOME=/var/root` depending on your sudoers config; prefer `sudo -H cleanup-mac --aggressive` when sudo is actually needed.

**External drives.** cleanup-mac only looks at `~/Library/…` and (under `--aggressive`) `/Library/Caches` and `/tmp`. External-drive data is never scanned.

**Can I undo a deletion?** `--apply` without `--permanent` moves items to Finder Trash — drag back or ⌘+Z in Finder. `--apply --permanent` is irreversible; the dry-run default and Trash behaviour exist precisely to avoid needing undo.

## Development

    python3 -m venv .venv
    .venv/bin/pip install pytest ruff
    .venv/bin/python -m pytest

All tests use pytest `tmp_path` fixtures and monkeypatch `Path.home()`. They never read or write outside the per-test temp directory.

### Install from source

    git clone https://github.com/theartofdevel/cleanup-mac.git
    cd cleanup-mac
    make install

Creates symlink `~/bin/cleanup-mac` → `cleanup_mac.py`. No build step, no dependencies. Ensure `~/bin` is in your `PATH`.

See [CONTRIBUTING.md](CONTRIBUTING.md) for PR conventions and the safety invariants code review enforces.
