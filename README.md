# cleanup-mac

A safe, auditable CLI to reclaim disk space on macOS. Dry-run by default. No external dependencies — just Python 3 stdlib.

## Usage

    cleanup-mac                         # dry-run — show what would be freed
    cleanup-mac -i                      # interactive (y/N per category)
    cleanup-mac --apply                 # delete (to Trash)
    cleanup-mac --apply --permanent     # delete (irreversible)
    cleanup-mac --aggressive            # include system caches + temp files
    cleanup-mac --only leftover,xcode   # restrict categories
    cleanup-mac --skip log              # exclude a category
    cleanup-mac --help                  # full flag reference

> **⚠️ `--aggressive`** touches system-wide caches (sudo) and prunes dangling Docker images (`docker image prune -f`). Use only on machines you back up regularly.

## Install

Download from [**GitHub Releases**](https://github.com/theartofdevel/cleanup-mac/releases/latest). Pick `arm64` for Apple Silicon, `x86_64` for Intel. macOS 11+.

- **`.pkg`** — double-click installer, installs to `/usr/local/bin`. Signed + notarized + stapled (works offline).
- **`.tar.gz`** — bare binary for scripts/CI. `tar -xzf`, move to `~/bin/`.

Verify checksum before running:

    shasum -a 256 -c cleanup-mac-<version>-<arch>.pkg.sha256

Built-in self-update:

    cleanup-mac --check-update
    cleanup-mac --update

## How it works

1. Scans `~/Library/Caches`, `Logs`, leftovers, Xcode junk, package manager caches.
2. Shows dry-run report by default — nothing is deleted without `--apply`.
3. `--apply` moves items to Finder Trash (recoverable). `--permanent` skips Trash.
4. Hardcoded never-touch paths (Apple, iCloud, Keychains, Mail, iOS backups).
5. Audit log at `~/Library/Logs/cleanup-mac/<timestamp>.log`.

Full safety model, path resolution rules, troubleshooting, development setup: [MANUAL.md](MANUAL.md).

## Uninstall

    sudo rm /usr/local/bin/cleanup-mac      # .pkg install
    rm -f ~/bin/cleanup-mac                 # tarball install
    rm -rf ~/Library/Caches/cleanup-mac     # temp unpack cache
    rm -rf ~/Library/Logs/cleanup-mac       # audit log
    rm -rf ~/.config/cleanup-mac            # optional ignore-file

## Support

If cleanup-mac saved you time or disk space:

- [Boosty](https://boosty.to/artdevs.org)
- **BTC:** `bc1qfvk036j4y3kv6pstmzysvje8s49kc7gy586cq7`
- **ETH:** `0x97a2502CB114618eCc661f71AAb971cb518Ee3dB`
- **TRX / USDT-TRC20:** `TQoAAfhexSkY14zEA3QgU47zQTfbRe5h2S`
- **USDT-TON:** `UQDVQCIw1ZKRCSsEGgM0eUxWw1K0ehg4lUZEClHSnyJCaB-j`

## More

- [**MANUAL.md**](MANUAL.md) — what it cleans, safety invariants, signature verification, troubleshooting, development.
- [CONTRIBUTING.md](CONTRIBUTING.md)
- [LICENSE](LICENSE)
