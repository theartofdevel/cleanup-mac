# Contributing to cleanup-mac

Thanks for your interest. cleanup-mac deletes files on behalf of the
user, so contribution rules here lean conservative — please read
before opening a PR.

## Before you start

- **Bug fixes** are welcome without prior discussion.
- **New cleanup categories or behaviour changes** — please open an
  issue first. The safety model constrains what changes make sense,
  and up-front alignment saves rewrites.
- **Documentation improvements** — welcome without discussion.

## Dev setup

See [`MANUAL.md`](MANUAL.md#development) for the canonical setup. In
short:

```bash
python3 -m venv .venv
.venv/bin/pip install pytest ruff
.venv/bin/python -m pytest
```

`make test` runs pytest. `make lint` runs `ruff check`.

## Commit style

This project uses [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/).
Prefixes in use: `feat:`, `fix:`, `refactor:`, `ci:`, `docs:`,
`test:`, `chore:`. A quick look at `git log --oneline` shows the
pattern.

## DCO (Developer Certificate of Origin)

Every commit must be signed off:

```bash
git commit -s -m "feat: your change"
```

This adds a `Signed-off-by:` trailer certifying the
[Developer Certificate of Origin 1.1](https://developercertificate.org/).
We do not use a CLA.

## Safety invariants

Code review will reject any PR that relaxes the following without
explicit maintainer sign-off. These are the invariants that make
cleanup-mac safe to run.

1. **Dry-run remains the default.** A user who types `cleanup-mac`
   with no arguments sees a report, not a deletion. Only `--apply`
   performs deletion. Only `--permanent` performs irreversible
   deletion.
2. **`NEVER_TOUCH` path list can be extended (more paths refused) but
   never shrunk.** The list in `cleanup_mac/safety.py` is the single
   source of truth for what cleanup-mac will never delete.
3. **Every deletion goes through `Path.resolve()` + allowed-root
   membership check.** No deletion ever happens outside the allowed
   roots, even via symlink.
4. **Every `subprocess` invocation takes `list[str]` arguments. No
   `shell=True`, anywhere, for any reason.** This is not negotiable.
5. **Leftover detection matches only reverse-DNS bundle-ID folder
   names** (e.g. `com.example.App`). Human-readable folder names
   (`Spotify`, `JetBrains`) must continue to be skipped — leftover
   classification relies on `Info.plist` bundle-ID lookup, not folder
   names.

## Tests

- Every PR must pass `make test` (pytest) and `make lint` (ruff).
- New feature PRs must add tests.
- Existing tests use `tmp_path` and `monkeypatch(Path.home)`. They
  never read or write outside the per-test temp directory. **New
  tests must follow this pattern** — tests that touch real
  `~/Library/` paths will be rejected.

## PR process

1. Branch from `main`.
2. Rebase onto `main` before requesting review — do not merge `main`
   into feature branches.
3. Squash-merge into `main` on approval. The squash-commit message
   should follow Conventional Commits.

## Behaviour

Be kind. Assume good faith. Focus criticism on code, not people.
