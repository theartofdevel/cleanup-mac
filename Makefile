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

BIN_DIR      ?= $(HOME)/bin
SOURCE       := $(CURDIR)/cleanup_mac.py
VERSION_FILE := $(CURDIR)/cleanup_mac/_version.py
LINK         := $(BIN_DIR)/cleanup-mac
VERSION      := $(shell grep '^__version__' $(VERSION_FILE) | cut -d '"' -f 2)
VENV_PYTHON  := $(CURDIR)/.venv/bin/python

# Universal2 Python (python.org installer) — Nuitka emits both archs from one host.
UNIVERSAL_PYTHON ?= /usr/local/bin/python3.12
BUILD_VENV       := $(CURDIR)/.venv-universal2
BUILD_PYTHON     := $(BUILD_VENV)/bin/python

# Per-arch build layout.
BUILD_ARM64     := dist/build-arm64
BUILD_X86_64    := dist/build-x86_64
BIN_ARM64       := $(BUILD_ARM64)/cleanup-mac
BIN_X86_64      := $(BUILD_X86_64)/cleanup-mac
RELEASE_DIR     := dist/release
NAME_ARM64      := cleanup-mac-$(VERSION)-arm64
NAME_X86_64     := cleanup-mac-$(VERSION)-x86_64
TARBALL_ARM64   := $(RELEASE_DIR)/$(NAME_ARM64).tar.gz
TARBALL_X86_64  := $(RELEASE_DIR)/$(NAME_X86_64).tar.gz
PKG_ARM64       := $(RELEASE_DIR)/$(NAME_ARM64).pkg
PKG_X86_64      := $(RELEASE_DIR)/$(NAME_X86_64).pkg

# Dev convenience: ad-hoc-signed arm64 for local smoke tests.
DEV_BUILD_DIR   := dist
DEV_BINARY      := $(DEV_BUILD_DIR)/cleanup-mac

# Developer ID for signed/notarized release builds.
DEVELOPER_ID           ?= Developer ID Application: Artur Karapetov (D3XP794W84)
DEVELOPER_ID_INSTALLER ?= Developer ID Installer: Artur Karapetov (D3XP794W84)

# notarytool keychain profile name (see README for setup).
NOTARY_PROFILE ?= cleanup-mac-notary

# .pkg payload installs binary at /usr/local/bin/cleanup-mac.
PKG_BUNDLE_ID  := dev.artdevs.cleanup-mac
PKG_INSTALL_TO := /usr/local/bin

# ---------- Dev workflow ----------

install:
	@mkdir -p $(BIN_DIR)
	@chmod +x $(SOURCE)
	@ln -sf $(SOURCE) $(LINK)
	@echo "Installed: $(LINK) -> $(SOURCE)"
	@echo "Ensure $(BIN_DIR) is in your PATH."

uninstall:
	@rm -f $(LINK)
	@echo "Removed: $(LINK)"

test:
	$(VENV_PYTHON) -m pytest

lint:
	ruff check cleanup_mac/ tests/ scripts/

clean:
	rm -rf dist build *.build *.dist *.onefile-build

# ---------- Dev build (local, arm64 ad-hoc signed) ----------

# Convenience target for local smoke tests — arm64, ad-hoc signed.
build:
	@test -x $(VENV_PYTHON) || (echo "run: python3 -m venv .venv && .venv/bin/pip install 'Nuitka[onefile]' pytest"; exit 1)
	$(VENV_PYTHON) -m nuitka \
		--onefile \
		--onefile-tempdir-spec='{CACHE_DIR}/cleanup-mac/{VERSION}' \
		--product-version=$(VERSION) \
		--output-filename=cleanup-mac \
		--output-dir=$(DEV_BUILD_DIR) \
		--assume-yes-for-downloads \
		--remove-output \
		--quiet \
		$(SOURCE)
	codesign --sign - --force $(DEV_BINARY)
	@echo "built: $(DEV_BINARY) ($$(du -h $(DEV_BINARY) | cut -f1), ad-hoc signed, arm64)"

# ---------- Release build — dual-arch via universal2 Python ----------

# Pinned Nuitka — unpinned breaks sha256 reproducibility across patch versions.
NUITKA_SPEC := Nuitka[onefile]==2.7.12

build-venv:
	@test -x $(UNIVERSAL_PYTHON) || (echo "error: UNIVERSAL_PYTHON=$(UNIVERSAL_PYTHON) not found. Install python.org's universal2 Python 3.12+ (see README 'Releasing (maintainer notes)' section)."; exit 1)
	@lipo -info $(UNIVERSAL_PYTHON) 2>/dev/null | grep -q 'x86_64' || (echo "error: UNIVERSAL_PYTHON=$(UNIVERSAL_PYTHON) is not universal2. Need both arm64 and x86_64 slices."; exit 1)
	@test -x $(BUILD_PYTHON) || $(UNIVERSAL_PYTHON) -m venv $(BUILD_VENV)
	@$(BUILD_PYTHON) -m pip install --quiet --upgrade pip
	@$(BUILD_PYTHON) -m pip install --quiet '$(NUITKA_SPEC)'
	@echo "build venv ready: $(BUILD_VENV)"

_check-build-python: build-venv

# Per-arch build via --macos-target-arch; emits single-arch binary.
build-arm64: _check-build-python
	@mkdir -p $(BUILD_ARM64)
	$(BUILD_PYTHON) -m nuitka \
		--onefile \
		--onefile-tempdir-spec='{CACHE_DIR}/cleanup-mac/{VERSION}' \
		--product-version=$(VERSION) \
		--output-filename=cleanup-mac \
		--output-dir=$(BUILD_ARM64) \
		--macos-target-arch=arm64 \
		--assume-yes-for-downloads \
		--remove-output \
		--quiet \
		$(SOURCE)
	@echo "built: $(BIN_ARM64) ($$(du -h $(BIN_ARM64) | cut -f1), arm64)"

build-x86_64: _check-build-python
	@mkdir -p $(BUILD_X86_64)
	$(BUILD_PYTHON) -m nuitka \
		--onefile \
		--onefile-tempdir-spec='{CACHE_DIR}/cleanup-mac/{VERSION}' \
		--product-version=$(VERSION) \
		--output-filename=cleanup-mac \
		--output-dir=$(BUILD_X86_64) \
		--macos-target-arch=x86_64 \
		--assume-yes-for-downloads \
		--remove-output \
		--quiet \
		$(SOURCE)
	@echo "built: $(BIN_X86_64) ($$(du -h $(BIN_X86_64) | cut -f1), x86_64)"

# Sign each arch with the Application cert.
build-signed-arm64: build-arm64
	@test -n "$(DEVELOPER_ID)" || (echo "DEVELOPER_ID is empty"; exit 1)
	codesign --sign "$(DEVELOPER_ID)" --force --options runtime --timestamp $(BIN_ARM64)
	codesign --verify --deep --strict --verbose=2 $(BIN_ARM64)
	@echo "signed: $(BIN_ARM64)"

build-signed-x86_64: build-x86_64
	@test -n "$(DEVELOPER_ID)" || (echo "DEVELOPER_ID is empty"; exit 1)
	codesign --sign "$(DEVELOPER_ID)" --force --options runtime --timestamp $(BIN_X86_64)
	codesign --verify --deep --strict --verbose=2 $(BIN_X86_64)
	@echo "signed: $(BIN_X86_64)"

# Notarize Mach-O. Cannot be stapled — Gatekeeper does online lookup on first run.
notarize-arm64: build-signed-arm64
	@echo "notarizing arm64 binary..."
	cd $(BUILD_ARM64) && zip -q $(NAME_ARM64).zip cleanup-mac
	xcrun notarytool submit $(BUILD_ARM64)/$(NAME_ARM64).zip \
		--keychain-profile $(NOTARY_PROFILE) --wait
	@rm -f $(BUILD_ARM64)/$(NAME_ARM64).zip
	# spctl --type install — only type accepting bare notarized Mach-O on modern macOS.
	spctl --assess --type install --verbose=4 $(BIN_ARM64)

notarize-x86_64: build-signed-x86_64
	@echo "notarizing x86_64 binary..."
	cd $(BUILD_X86_64) && zip -q $(NAME_X86_64).zip cleanup-mac
	xcrun notarytool submit $(BUILD_X86_64)/$(NAME_X86_64).zip \
		--keychain-profile $(NOTARY_PROFILE) --wait
	@rm -f $(BUILD_X86_64)/$(NAME_X86_64).zip
	@# spctl skipped for x86_64 — Rosetta round-trip slow; notarize already confirmed.

# ---------- Tarball artifacts ----------

# COPYFILE_DISABLE=1 + gzip -n strip xattrs/metadata for tarball reproducibility.
tarball-arm64: notarize-arm64
	@mkdir -p $(RELEASE_DIR)
	cd $(BUILD_ARM64) && COPYFILE_DISABLE=1 tar -cf - cleanup-mac | gzip -n > $(CURDIR)/$(TARBALL_ARM64)
	@(cd $(RELEASE_DIR) && shasum -a 256 $(NAME_ARM64).tar.gz > $(NAME_ARM64).tar.gz.sha256)

tarball-x86_64: notarize-x86_64
	@mkdir -p $(RELEASE_DIR)
	cd $(BUILD_X86_64) && COPYFILE_DISABLE=1 tar -cf - cleanup-mac | gzip -n > $(CURDIR)/$(TARBALL_X86_64)
	@(cd $(RELEASE_DIR) && shasum -a 256 $(NAME_X86_64).tar.gz > $(NAME_X86_64).tar.gz.sha256)

# ---------- Stapled .pkg installer ----------

# pkgbuild = component, productbuild = distribution wrapper; payload at /usr/local/bin/cleanup-mac.
pkg-arm64: notarize-arm64
	@# Flat payload avoids /usr BOM entry (SSV read-only); xattr -cr strips AppleDouble sidecars.
	@mkdir -p $(RELEASE_DIR) dist/pkg-root-arm64 dist/pkg-build-arm64
	@cp $(BIN_ARM64) dist/pkg-root-arm64/cleanup-mac
	@chmod +x dist/pkg-root-arm64/cleanup-mac
	@xattr -cr dist/pkg-root-arm64
	pkgbuild \
		--root dist/pkg-root-arm64 \
		--identifier $(PKG_BUNDLE_ID) \
		--version $(VERSION) \
		--install-location $(PKG_INSTALL_TO) \
		dist/pkg-build-arm64/component.pkg
	productbuild \
		--package dist/pkg-build-arm64/component.pkg \
		--identifier $(PKG_BUNDLE_ID) \
		--version $(VERSION) \
		--sign "$(DEVELOPER_ID_INSTALLER)" \
		$(PKG_ARM64)
	@rm -rf dist/pkg-root-arm64 dist/pkg-build-arm64
	@echo "built+signed: $(PKG_ARM64)"
	@# Notarize + staple — .pkg containers carry stapled tickets (offline-first Gatekeeper).
	xcrun notarytool submit $(PKG_ARM64) \
		--keychain-profile $(NOTARY_PROFILE) --wait
	xcrun stapler staple $(PKG_ARM64)
	@(cd $(RELEASE_DIR) && shasum -a 256 $(NAME_ARM64).pkg > $(NAME_ARM64).pkg.sha256)

pkg-x86_64: notarize-x86_64
	@# See pkg-arm64 for rationale.
	@mkdir -p $(RELEASE_DIR) dist/pkg-root-x86_64 dist/pkg-build-x86_64
	@cp $(BIN_X86_64) dist/pkg-root-x86_64/cleanup-mac
	@chmod +x dist/pkg-root-x86_64/cleanup-mac
	@xattr -cr dist/pkg-root-x86_64
	pkgbuild \
		--root dist/pkg-root-x86_64 \
		--identifier $(PKG_BUNDLE_ID) \
		--version $(VERSION) \
		--install-location $(PKG_INSTALL_TO) \
		dist/pkg-build-x86_64/component.pkg
	productbuild \
		--package dist/pkg-build-x86_64/component.pkg \
		--identifier $(PKG_BUNDLE_ID) \
		--version $(VERSION) \
		--sign "$(DEVELOPER_ID_INSTALLER)" \
		$(PKG_X86_64)
	@rm -rf dist/pkg-root-x86_64 dist/pkg-build-x86_64
	@echo "built+signed: $(PKG_X86_64)"
	xcrun notarytool submit $(PKG_X86_64) \
		--keychain-profile $(NOTARY_PROFILE) --wait
	xcrun stapler staple $(PKG_X86_64)
	@(cd $(RELEASE_DIR) && shasum -a 256 $(NAME_X86_64).pkg > $(NAME_X86_64).pkg.sha256)

# ---------- Full per-arch release ----------

release-arm64: tarball-arm64 pkg-arm64

release-x86_64: tarball-x86_64 pkg-x86_64

# Full release: both arches, all artifacts.
release: release-arm64 release-x86_64
	@echo ""
	@echo "=============================================================="
	@echo "  Release artifacts ready in $(RELEASE_DIR)/"
	@echo ""
	@ls -la $(RELEASE_DIR)/
	@echo "=============================================================="

# Generate manifest.json from sha256 sidecars; schema in cleanup_mac/updater.py.
manifest: release
	python3 scripts/gen-manifest.py
	@test -f $(RELEASE_DIR)/manifest.json || (echo "error: manifest.json not produced"; exit 1)
	@echo "manifest.json ready: $(RELEASE_DIR)/manifest.json"

# ---------- Full local release ----------

# make publish: bump → release → manifest → publish-finish. Origin untouched until all artifacts built+signed.
publish:
	@test -n "$(BUMP)" || (echo "error: set BUMP=patch|minor|major (e.g. 'make publish BUMP=patch')"; exit 1)
	@./scripts/bump-version.sh "$(BUMP)"
	@$(MAKE) release
	@$(MAKE) manifest
	@$(MAKE) publish-finish

# publish-finish: tag, push, gh release create. Rerun: git tag -d v$(VERSION); optionally git push --delete origin v$(VERSION); rerun.
publish-finish:
	@command -v gh >/dev/null 2>&1 || (echo "error: 'gh' CLI not found. Install with: brew install gh && gh auth login"; exit 1)
	@gh auth status >/dev/null 2>&1 || (echo "error: 'gh' not authenticated. Run: gh auth login"; exit 1)
	@test -f $(RELEASE_DIR)/manifest.json || (echo "error: $(RELEASE_DIR)/manifest.json missing — run 'make manifest' first"; exit 1)
	@test -f $(TARBALL_ARM64) || (echo "error: $(TARBALL_ARM64) missing — run 'make release' first"; exit 1)
	@test -f $(TARBALL_X86_64) || (echo "error: $(TARBALL_X86_64) missing — run 'make release' first"; exit 1)
	@test -f $(PKG_ARM64) || (echo "error: $(PKG_ARM64) missing — run 'make release' first"; exit 1)
	@test -f $(PKG_X86_64) || (echo "error: $(PKG_X86_64) missing — run 'make release' first"; exit 1)
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
