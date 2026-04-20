"""cleanup-mac — safe macOS disk-space reclamation CLI."""

from __future__ import annotations

from cleanup_mac._util import format_bytes, get_size
from cleanup_mac._version import __version__
from cleanup_mac.bundle import (
    BUNDLE_ID_RE,
    DEFAULT_APP_ROOTS,
    base_id,
    get_installed_bundle_ids,
    is_bundle_id,
)
from cleanup_mac.cli import build_parser, main
from cleanup_mac.execute import (
    CONTAINER_METADATA_PLIST,
    CONTAINER_PREFIXES,
    delete_permanent,
    execute_candidates,
    move_to_trash,
)
from cleanup_mac.logger import setup_logger
from cleanup_mac.packagers import (
    TRUSTED_BIN_PREFIXES,
    scan_package_managers,
)
from cleanup_mac.render import (
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    VALID_CATEGORIES,
    UserQuit,
    prompt_confirm_category,
    render_json,
    render_terminal,
)
from cleanup_mac.safety import (
    ALLOWED_ROOTS_FOR_DELETION,
    BUILTIN_WHITELIST,
    NEVER_TOUCH_ABSOLUTE,
    NEVER_TOUCH_HOME_PATTERNS,
    NEVER_TOUCH_RELATIVE_TO_HOME,
    default_allowed_roots,
    is_in_whitelist,
    is_never_touch,
    is_path_under,
    load_whitelist,
)

# Re-exported for older tests; import from cleanup_mac.safety in new code.
from cleanup_mac.safety import _guard_deletion as _guard_deletion
from cleanup_mac.safety import _path_fingerprint as _path_fingerprint
from cleanup_mac.safety import _verify_unchanged as _verify_unchanged
from cleanup_mac.scanners import (
    DEFAULT_LEFTOVER_LOCATIONS,
    default_leftover_scan_locations,
    scan_leftovers,
    scan_logs,
    scan_system_caches,
    scan_temp_files,
    scan_user_caches,
    scan_xcode,
)
from cleanup_mac.types import Candidate, PackageCleanup

__all__ = [
    "ALLOWED_ROOTS_FOR_DELETION",
    "BUILTIN_WHITELIST",
    "BUNDLE_ID_RE",
    "CATEGORY_LABELS",
    "CATEGORY_ORDER",
    "CONTAINER_METADATA_PLIST",
    "CONTAINER_PREFIXES",
    "DEFAULT_APP_ROOTS",
    "DEFAULT_LEFTOVER_LOCATIONS",
    "NEVER_TOUCH_ABSOLUTE",
    "NEVER_TOUCH_HOME_PATTERNS",
    "NEVER_TOUCH_RELATIVE_TO_HOME",
    "TRUSTED_BIN_PREFIXES",
    "VALID_CATEGORIES",
    "Candidate",
    "PackageCleanup",
    "UserQuit",
    "__version__",
    "base_id",
    "build_parser",
    "default_allowed_roots",
    "default_leftover_scan_locations",
    "delete_permanent",
    "execute_candidates",
    "format_bytes",
    "get_installed_bundle_ids",
    "get_size",
    "is_bundle_id",
    "is_in_whitelist",
    "is_never_touch",
    "is_path_under",
    "load_whitelist",
    "main",
    "move_to_trash",
    "prompt_confirm_category",
    "render_json",
    "render_terminal",
    "scan_leftovers",
    "scan_logs",
    "scan_package_managers",
    "scan_system_caches",
    "scan_temp_files",
    "scan_user_caches",
    "scan_xcode",
    "setup_logger",
]
