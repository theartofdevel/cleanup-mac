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
