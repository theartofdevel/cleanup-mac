"""Command-line interface: argparse + orchestration."""

from __future__ import annotations

import argparse
import contextlib
import logging
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TextIO

from cleanup_mac._util import format_bytes
from cleanup_mac._version import __version__
from cleanup_mac.bundle import get_installed_bundle_ids
from cleanup_mac.execute import execute_candidates
from cleanup_mac.logger import (
    LOG_FORMAT_NAMES,
    LOG_LEVEL_NAMES,
    setup_logger,
    structured_log,
)
from cleanup_mac.packagers import scan_package_managers
from cleanup_mac.render import (
    CATEGORY_ORDER,
    VALID_CATEGORIES,
    UserQuit,
    prompt_confirm_category,
    render_json,
    render_terminal,
)
from cleanup_mac.safety import default_allowed_roots, load_whitelist
from cleanup_mac.scanners import (
    default_leftover_scan_locations,
    scan_leftovers,
    scan_logs,
    scan_system_caches,
    scan_temp_files,
    scan_user_caches,
    scan_xcode,
)
from cleanup_mac.types import Candidate


def _category_list(value: str) -> list[str]:
    items = [x.strip() for x in value.split(",") if x.strip()]
    for item in items:
        if item not in VALID_CATEGORIES:
            raise argparse.ArgumentTypeError(
                f"unknown category {item!r}; valid: {', '.join(VALID_CATEGORIES)}"
            )
    return items


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cleanup-mac",
        description="Safe macOS cleanup for caches, logs, and uninstalled-app leftovers.",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "-i", "--interactive", action="store_true", help="ask y/N per category"
    )
    mode.add_argument(
        "-f", "--apply", action="store_true", help="delete without prompting"
    )

    p.add_argument(
        "-a",
        "--aggressive",
        action="store_true",
        help="include system_caches and temp_files (requires sudo for /Library/Caches)",
    )
    p.add_argument(
        "--only",
        type=_category_list,
        help="comma-separated category subset",
    )
    p.add_argument(
        "--skip",
        type=_category_list,
        help="comma-separated categories to exclude",
    )
    p.add_argument(
        "--permanent",
        action="store_true",
        help="real rm instead of Trash",
    )
    p.add_argument(
        "--min-age",
        type=int,
        default=7,
        help="skip files modified in last N days (default: 7)",
    )
    p.add_argument(
        "--min-size",
        type=int,
        default=1,
        help="hide candidates smaller than N MB (default: 1)",
    )

    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-log", action="store_true")
    p.add_argument(
        "--log-format",
        choices=LOG_FORMAT_NAMES,
        default="text",
        help=(
            "audit-log format (default: text). "
            "json emits one schema-versioned JSON object per line."
        ),
    )
    p.add_argument(
        "--log-level",
        choices=LOG_LEVEL_NAMES,
        default="info",
        help=(
            "audit-log verbosity (default: info). "
            "debug adds per-candidate records and duration_ms timing."
        ),
    )
    p.add_argument(
        "--log-retention",
        type=int,
        default=20,
        help="keep this many newest log files in ~/Library/Logs/cleanup-mac/ "
             "(default: 20). 0 disables cleanup.",
    )
    p.add_argument(
        "--ignore-file",
        type=Path,
        default=None,
        help="extra whitelist file (one glob per line)",
    )

    p.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help=(
            "skip the pre-run confirmation prompt. "
            "Required for scripted/non-interactive use of --apply."
        ),
    )

    p.add_argument(
        "--update",
        action="store_true",
        help="download + install the latest release, then re-exec into it",
    )
    p.add_argument(
        "--check-update",
        action="store_true",
        help="check whether a newer release is available; exit 0 if not, 1 if newer",
    )
    p.add_argument(
        "--version", action="version", version=f"cleanup-mac {__version__}"
    )
    return p


def _progress(msg: str, enabled: bool, stream: TextIO | None = None) -> None:
    if not enabled:
        return
    (stream if stream is not None else sys.stderr).write(msg + "\n")
    (stream if stream is not None else sys.stderr).flush()


def _describe_scope(args: argparse.Namespace) -> str:
    base = [*CATEGORY_ORDER, "packages"]
    if not args.aggressive:
        base = [c for c in base if c not in ("system_cache", "temp")]
    if args.only:
        base = [c for c in base if c in args.only]
    elif args.skip:
        base = [c for c in base if c not in args.skip]
    return ", ".join(base) if base else "(none — check --only/--skip)"


def _print_run_banner(
    args: argparse.Namespace,
    mode: str,
    log_path: Path | None,
    out: TextIO,
) -> None:
    width = 63
    out.write("═" * width + "\n")

    if mode == "dry-run":
        out.write(" Mode: DRY-RUN (scan and report only — nothing deleted)\n")
        out.write(" Outcome: you'll see what would be freed; disk is untouched.\n")
        out.write(" To actually delete, re-run with one of:\n")
        out.write("   --apply              (move items to ~/.Trash, recoverable)\n")
        out.write("   --apply --permanent  (irreversible delete)\n")
        out.write("   -i / --interactive   (per-category y/N prompts)\n")
    elif args.interactive:
        dest = "PERMANENT DELETE" if args.permanent else "move to ~/.Trash"
        out.write(f" Mode: INTERACTIVE — per-category y/N, then {dest}\n")
        out.write(" Outcome: approved categories are touched; declined ones untouched.\n")
    elif args.apply:
        if args.permanent:
            out.write(" Mode: APPLY + PERMANENT  [!!! IRREVERSIBLE !!!]\n")
            out.write(" Outcome: items will be gone. No Trash, no recovery.\n")
        else:
            out.write(" Mode: APPLY — move to ~/.Trash (no per-item prompts)\n")
            out.write(" Outcome: items moved to Trash. Restore from Finder.\n")

    if args.aggressive:
        out.write(" Aggressive: also scans /Library/Caches (sudo needed),\n")
        out.write("             /private/var/folders, /tmp, `docker image prune -f`\n")

    out.write(f" Filters:   min-age={args.min_age}d, min-size={args.min_size}MB\n")
    out.write(f" Scope:     {_describe_scope(args)}\n")
    if log_path is not None:
        out.write(f" Audit log: {log_path}\n")
    else:
        out.write(" Audit log: disabled (--no-log)\n")

    out.write("═" * width + "\n")
    out.flush()


def _confirm_start(
    args: argparse.Namespace,
    in_stream: TextIO,
    out: TextIO,
) -> bool:
    """Consent check before any scan. --yes bypasses. Non-TTY dry-runs
    auto-proceed (no risk); non-TTY --apply/-i refuse — scripts must
    pass --yes explicitly."""
    if args.yes:
        return True

    tty = in_stream.isatty()
    if args.apply:
        desc = "PERMANENT DELETE" if args.permanent else "Trash deletion"
        hint = "[y/N]"
        default_yes = False
    elif args.interactive:
        desc = "interactive run (per-category prompts follow)"
        hint = "[Y/n]"
        default_yes = True
    else:
        desc = "dry-run scan (no changes)"
        hint = "[Y/n]"
        default_yes = True

    if not tty:
        if args.apply or args.interactive:
            out.write(
                f"cleanup-mac: stdin is not a TTY — cannot confirm {desc}.\n"
                f"Re-run with --yes to skip the prompt explicitly.\n"
            )
            out.flush()
            return False
        return True

    out.write(f"About to start {desc}. Proceed? {hint} ")
    out.flush()
    ans = in_stream.readline().strip().lower()
    if ans == "":
        return default_yes
    return ans in ("y", "yes")


def _run_check_update() -> int:
    # Lazy import keeps updater (urllib, tarfile, hashlib) off the
    # default scan path.
    from cleanup_mac.updater import UpdateError, check_update

    try:
        newer, latest = check_update()
    except UpdateError as e:
        sys.stderr.write(f"cleanup-mac: update check failed: {e}\n")
        return 2
    if newer:
        sys.stdout.write(
            f"newer version available: {latest} (running {__version__})\n"
            f"run `cleanup-mac --update` to install\n"
        )
        return 1
    sys.stdout.write(f"cleanup-mac {__version__} is the latest version\n")
    return 0


def _run_update(logger: logging.Logger, yes: bool) -> int:
    """Successful updates os.execv into the new binary and never return."""
    from cleanup_mac.updater import (
        UpdateDeclined,
        UpdateError,
        is_newer,
        perform_update,
    )

    try:
        new_version = perform_update(logger=logger, yes=yes)
    except UpdateDeclined as e:
        sys.stdout.write(f"cleanup-mac: {e}\n")
        return 3
    except UpdateError as e:
        sys.stderr.write(f"cleanup-mac: update failed: {e}\n")
        structured_log(logger, logging.ERROR, "update_failed", reason=str(e))
        return 2

    if is_newer(new_version, __version__):
        # execv should have fired; defensive branch.
        sys.stdout.write(f"updated to {new_version} (previous: {__version__})\n")
        return 0
    sys.stdout.write(f"cleanup-mac {__version__} is already the latest\n")
    return 0


def _install_sigterm_handler(logger: logging.Logger) -> None:
    """Log one `user_cancelled` event on SIGTERM and exit 3. SIGINT is
    already handled by main() via KeyboardInterrupt."""

    def _handle(signo, _frame) -> None:
        structured_log(
            logger,
            logging.WARNING,
            "user_cancelled",
            reason="signal",
            signo=signo,
        )
        for h in logger.handlers:
            with contextlib.suppress(OSError):
                h.flush()
        sys.exit(3)

    # ValueError: not in main thread (pytest-xdist). OSError: no SIGTERM.
    with contextlib.suppress(ValueError, OSError):
        signal.signal(signal.SIGTERM, _handle)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    log_dir = Path.home() / "Library/Logs/cleanup-mac"
    logger, log_path = setup_logger(
        log_dir=log_dir,
        enabled=not args.no_log,
        log_format=args.log_format,
        log_level=args.log_level,
        retention=args.log_retention,
    )
    _install_sigterm_handler(logger)

    if args.check_update:
        return _run_check_update()
    if args.update:
        return _run_update(logger, yes=args.yes)

    # Interactive can delete per category — log it accordingly.
    will_delete = args.apply or args.interactive
    mode = ("permanent" if args.permanent else "trash") if will_delete else "dry-run"

    if not args.json:
        _print_run_banner(args, mode, log_path, sys.stderr)
        if not _confirm_start(args, sys.stdin, sys.stderr):
            structured_log(
                logger,
                logging.WARNING,
                "user_cancelled",
                reason="pre_run_prompt",
            )
            return 3

    structured_log(
        logger,
        logging.INFO,
        "scan_started",
        mode=mode,
        aggressive=args.aggressive,
        tool_version=__version__,
    )

    try:
        ignore_file = args.ignore_file
        if ignore_file is None:
            default_ignore = Path.home() / ".config/cleanup-mac/ignore.txt"
            if default_ignore.is_file():
                ignore_file = default_ignore
        whitelist = load_whitelist(ignore_file)
        min_size_bytes = args.min_size * 1024 * 1024
        progress_on = not args.quiet

        _progress("Scanning for cleanup candidates...", progress_on)

        _progress("  · indexing installed applications...", progress_on)
        t0 = time.time()
        installed_ids = get_installed_bundle_ids()
        index_dt = time.time() - t0
        structured_log(
            logger,
            logging.INFO,
            "indexed_apps",
            count=len(installed_ids),
            duration_ms=int(index_dt * 1000),
        )
        _progress(
            f"  ✓ indexed {len(installed_ids)} applications ({index_dt:.1f}s)",
            progress_on,
        )

        requested = set(args.only) if args.only else set(VALID_CATEGORIES)
        if not args.aggressive:
            requested -= {"system_cache", "temp"}
        if args.skip:
            requested -= set(args.skip)

        candidates: list[Candidate] = []

        def _run(label: str, category: str, scan_fn) -> list[Candidate]:
            _progress(f"  · scanning {label}...", progress_on)
            t = time.time()
            found = scan_fn()
            dt = time.time() - t
            size = sum(c.size_bytes for c in found)
            structured_log(
                logger,
                logging.INFO,
                "scan_done",
                category=category,
                count=len(found),
                size_bytes=size,
                duration_ms=int(dt * 1000),
            )
            _progress(
                f"  ✓ {label}: {len(found)} items, "
                f"{format_bytes(size)} ({dt:.1f}s)",
                progress_on,
            )
            return found

        if "user_cache" in requested:
            candidates += _run(
                "user caches",
                "user_cache",
                lambda: scan_user_caches(
                    root=Path.home() / "Library/Caches",
                    whitelist=whitelist,
                    min_age_days=args.min_age,
                    min_size_bytes=min_size_bytes,
                ),
            )
        if "log" in requested:
            candidates += _run(
                "logs",
                "log",
                lambda: scan_logs(
                    root=Path.home() / "Library/Logs",
                    whitelist=whitelist,
                    min_age_days=args.min_age,
                    min_size_bytes=min_size_bytes,
                ),
            )
        if "leftover" in requested:
            candidates += _run(
                "leftovers",
                "leftover",
                lambda: scan_leftovers(
                    scan_locations=default_leftover_scan_locations(),
                    installed_ids=installed_ids,
                    whitelist=whitelist,
                    min_age_days=args.min_age,
                    min_size_bytes=min_size_bytes,
                ),
            )
        if "xcode" in requested:
            candidates += _run(
                "Xcode junk",
                "xcode",
                lambda: scan_xcode(
                    developer_root=Path.home() / "Library/Developer",
                    min_age_days=args.min_age,
                    min_size_bytes=min_size_bytes,
                    aggressive=args.aggressive,
                ),
            )
        if "system_cache" in requested and args.aggressive:
            candidates += _run(
                "system caches",
                "system_cache",
                lambda: scan_system_caches(
                    root=Path("/Library/Caches"),
                    whitelist=whitelist,
                    min_age_days=args.min_age,
                    min_size_bytes=min_size_bytes,
                ),
            )
        if "temp" in requested and args.aggressive:
            candidates += _run(
                "temp files",
                "temp",
                lambda: scan_temp_files(
                    roots=[Path("/private/var/folders"), Path("/tmp")],
                    min_age_days=args.min_age,
                    min_size_bytes=min_size_bytes,
                ),
            )

        packages: list = []
        if "packages" in requested:
            _progress("  · scanning package manager caches...", progress_on)
            t = time.time()
            packages = scan_package_managers(aggressive=args.aggressive)
            dt = time.time() - t
            pkg_total = sum(p.current_size_bytes for p in packages)
            structured_log(
                logger,
                logging.INFO,
                "scan_done",
                category="packages",
                count=len(packages),
                size_bytes=pkg_total,
                duration_ms=int(dt * 1000),
            )
            _progress(
                f"  ✓ package managers: {len(packages)} tools, "
                f"{format_bytes(pkg_total)} ({dt:.1f}s)",
                progress_on,
            )

        _progress("", progress_on)  # blank line before report

        # Emit each identified candidate under DEBUG only — this can be
        # thousands of records on a busy machine, and it's already
        # visible in the terminal report. DEBUG users opt into verbosity.
        for c in candidates:
            structured_log(
                logger,
                logging.DEBUG,
                "candidate",
                category=c.category,
                path=str(c.path),
                size_bytes=c.size_bytes,
                reason=c.reason,
            )

        if args.json:
            render_json(
                candidates=candidates,
                packages=packages,
                mode=mode,
                installed_count=len(installed_ids),
                log_path=log_path,
                out=sys.stdout,
            )
        else:
            colors = sys.stdout.isatty()
            render_terminal(
                candidates=candidates,
                packages=packages,
                is_dry_run=not args.apply,
                verbose=args.verbose,
                quiet=args.quiet,
                colors=colors,
                out=sys.stdout,
                log_path=log_path,
            )

        if args.interactive or args.apply:
            allowed = default_allowed_roots(aggressive=args.aggressive)
            grouped: dict[str, list[Candidate]] = {}
            for c in candidates:
                grouped.setdefault(c.category, []).append(c)

            approved: list[Candidate] = []
            for cat in CATEGORY_ORDER:
                items = grouped.get(cat, [])
                if not items:
                    continue
                if args.interactive and not prompt_confirm_category(
                    cat, items, sys.stdin, sys.stdout
                ):
                    continue
                approved.extend(items)

            freed = execute_candidates(
                approved,
                apply=args.apply or args.interactive,
                permanent=args.permanent,
                allowed_roots=allowed,
                logger=logger,
            )

            pkg_ran: list = []
            for p in packages:
                if args.interactive:
                    cmd_display = " ".join(p.apply_command)
                    size_display = format_bytes(p.current_size_bytes)
                    sys.stdout.write(
                        f"Run `{cmd_display}` ({size_display})? [y/N/q(uit)] "
                    )
                    sys.stdout.flush()
                    ans = sys.stdin.readline().strip().lower()
                    if ans in ("q", "quit"):
                        raise UserQuit()
                    if ans not in ("y", "yes"):
                        continue
                try:
                    subprocess.run(
                        p.apply_command, check=False, timeout=300
                    )
                    pkg_ran.append(p)
                    structured_log(
                        logger,
                        logging.INFO,
                        "package_cleanup",
                        tool=p.tool,
                        cmd=p.apply_command,
                    )
                except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                    structured_log(
                        logger,
                        logging.ERROR,
                        "package_cleanup_failed",
                        tool=p.tool,
                        reason=str(e),
                    )

            structured_log(
                logger, logging.INFO, "scan_finished", freed_bytes=freed
            )

            candidates_total = sum(c.size_bytes for c in candidates)
            summary = f"\nFiles freed: {format_bytes(freed)}"
            if freed < candidates_total:
                summary += f" (of {format_bytes(candidates_total)} identified)"
            if pkg_ran:
                pkg_est = sum(p.current_size_bytes for p in pkg_ran)
                summary += (
                    f"\nPackage caches cleaned: {len(pkg_ran)} tool(s), "
                    f"up to {format_bytes(pkg_est)} (actual freed varies per tool)"
                )
            sys.stdout.write(summary + "\n")
            if log_path is not None:
                sys.stdout.write(f"Log: {log_path}\n")

        return 0

    except KeyboardInterrupt:
        structured_log(
            logger, logging.WARNING, "user_cancelled", reason="KeyboardInterrupt"
        )
        return 3
    except UserQuit:
        structured_log(
            logger, logging.WARNING, "user_cancelled", reason="UserQuit"
        )
        return 3
    except (OSError, subprocess.SubprocessError, RuntimeError) as e:
        logger.error(
            "runtime_error",
            exc_info=True,
            extra={
                "cleanupmac_event": "runtime_error",
                "cleanupmac_fields": {"reason": str(e)},
            },
        )
        return 2
    # TypeError / AttributeError / … are programmer bugs — let them
    # propagate with a traceback rather than swallowing them here.
