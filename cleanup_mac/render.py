"""Terminal + JSON report renderers and interactive prompt."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from cleanup_mac._util import format_bytes
from cleanup_mac._version import __version__
from cleanup_mac.types import Candidate, PackageCleanup

CATEGORY_LABELS: dict[str, str] = {
    "user_cache": "User caches",
    "log": "Logs",
    "leftover": "Leftovers (apps no longer installed)",
    "xcode": "Xcode junk",
    "system_cache": "System caches",
    "temp": "Temp files",
}

CATEGORY_ORDER: tuple[str, ...] = (
    "user_cache",
    "log",
    "leftover",
    "xcode",
    "system_cache",
    "temp",
)

VALID_CATEGORIES = (
    "user_cache",
    "log",
    "leftover",
    "xcode",
    "packages",
    "system_cache",
    "temp",
)


def render_terminal(
    candidates: list[Candidate],
    packages: list[PackageCleanup],
    is_dry_run: bool,
    verbose: bool,
    quiet: bool,
    colors: bool,
    out: TextIO,
    log_path: Path | None,
) -> None:
    cyan = "\x1b[36m" if colors else ""
    dim = "\x1b[2m" if colors else ""
    reset = "\x1b[0m" if colors else ""

    grouped: dict[str, list[Candidate]] = {}
    for c in candidates:
        grouped.setdefault(c.category, []).append(c)
    for items in grouped.values():
        items.sort(key=lambda c: c.size_bytes, reverse=True)

    total = sum(c.size_bytes for c in candidates) + sum(
        p.current_size_bytes for p in packages
    )

    width = 63

    for cat in CATEGORY_ORDER:
        items = grouped.get(cat, [])
        if not items:
            continue
        cat_total = sum(c.size_bytes for c in items)
        label = CATEGORY_LABELS[cat]
        size_str = format_bytes(cat_total)
        pad = " " * max(1, width - 2 - len(label) - len(size_str))
        out.write("═" * width + "\n")
        out.write(f" {cyan}{label}{reset}{pad}{size_str}\n")
        if not quiet:
            out.write("─" * width + "\n")
            to_show = items if verbose else items[:10]
            for c in to_show:
                out.write(f"   {format_bytes(c.size_bytes):>8}  {c.path}\n")
                if c.category == "leftover":
                    out.write(f"{dim}              └ reason: {c.reason}{reset}\n")
            if not verbose and len(items) > 10:
                out.write(f"   ... ({len(items) - 10} more, use -v to see all)\n")
        out.write("\n")

    if packages:
        pkg_total = sum(p.current_size_bytes for p in packages)
        pkg_label = "Package managers"
        pkg_size_str = format_bytes(pkg_total)
        pkg_pad = " " * max(1, width - 2 - len(pkg_label) - len(pkg_size_str))
        out.write("═" * width + "\n")
        out.write(f" {cyan}{pkg_label}{reset}{pkg_pad}{pkg_size_str}\n")
        if not quiet:
            out.write("─" * width + "\n")
            for p in sorted(packages, key=lambda p: p.current_size_bytes, reverse=True):
                cmd = " ".join(p.apply_command)
                out.write(
                    f"   {format_bytes(p.current_size_bytes):>8}  {p.tool}  (will run: {cmd})\n"
                )
            out.write("\n")

    if is_dry_run:
        label, marker = "would free", "[DRY RUN — nothing deleted]"
    else:
        label, marker = "found", "[about to delete]"
    out.write("═" * width + "\n")
    out.write(f" TOTAL {label}: {format_bytes(total)}   {marker}\n")
    out.write("═" * width + "\n")

    if is_dry_run:
        out.write(
            "\nRun with --apply to delete, or -i for interactive confirmation.\n"
        )
        if log_path is not None:
            out.write(f"Log: {log_path}\n")


def render_json(
    candidates: list[Candidate],
    packages: list[PackageCleanup],
    mode: str,
    installed_count: int,
    log_path: Path | None,
    out: TextIO,
) -> None:
    grouped: dict[str, list[Candidate]] = {}
    for c in candidates:
        grouped.setdefault(c.category, []).append(c)

    categories: dict[str, dict] = {}
    for cat, items in grouped.items():
        categories[cat] = {
            "total_bytes": sum(c.size_bytes for c in items),
            "candidates": [
                {
                    "path": str(c.path),
                    "size_bytes": c.size_bytes,
                    "reason": c.reason,
                }
                for c in items
            ],
        }

    pkg_total = sum(p.current_size_bytes for p in packages)
    pkg_block = {
        "total_bytes": pkg_total,
        "tools": [
            {
                "tool": p.tool,
                "current_size_bytes": p.current_size_bytes,
                "apply_command": p.apply_command,
            }
            for p in packages
        ],
    }

    total = sum(c.size_bytes for c in candidates) + pkg_total

    payload = {
        # Bumped on breaking changes to the output shape; consumers must
        # refuse unknown major versions.
        "schema_version": 1,
        "tool_version": __version__,
        "timestamp": datetime.now(UTC).isoformat(),
        "mode": mode,
        "installed_bundle_ids_count": installed_count,
        "categories": categories,
        "packages": pkg_block,
        "total_bytes": total,
        "log_path": str(log_path) if log_path else None,
    }
    out.write(json.dumps(payload, indent=2))
    out.write("\n")


class UserQuit(Exception):
    """User hit 'q' in an interactive prompt."""


def prompt_confirm_category(
    category: str,
    items: list[Candidate],
    in_stream: TextIO,
    out_stream: TextIO,
) -> bool:
    label = CATEGORY_LABELS.get(category, category)
    total = sum(c.size_bytes for c in items)
    while True:
        out_stream.write(
            f"Delete {len(items)} items from {label} totaling {format_bytes(total)}? "
            "[y/N/d(etails)/q(uit)]: "
        )
        out_stream.flush()
        line = in_stream.readline().strip().lower()
        if line in ("", "n", "no"):
            return False
        if line in ("y", "yes"):
            return True
        if line in ("d", "details"):
            for c in items:
                out_stream.write(f"  {format_bytes(c.size_bytes):>8}  {c.path}  ({c.reason})\n")
            continue
        if line in ("q", "quit"):
            raise UserQuit()
        out_stream.write("Please answer y, n, d, or q.\n")
