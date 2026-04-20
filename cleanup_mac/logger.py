"""Per-run audit logger. One file per run at ~/Library/Logs/cleanup-mac/,
retention trims to the newest N. Text or JSON format, versioned schema.

Use `structured_log(logger, level, event, **fields)` at call sites.
User-controlled field values are json.dumps-escaped in both formats."""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cleanup_mac._version import __version__

LOG_SCHEMA_VERSION = 1

_EVENT_ATTR = "cleanupmac_event"
_FIELDS_ATTR = "cleanupmac_fields"

LOG_LEVEL_NAMES: tuple[str, ...] = ("debug", "info", "warn", "error")
LOG_FORMAT_NAMES: tuple[str, ...] = ("text", "json")


def _level_from_name(name: str) -> int:
    return {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warn": logging.WARNING,
        "error": logging.ERROR,
    }[name]


def structured_log(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    logger.log(
        level,
        event,
        extra={_EVENT_ATTR: event, _FIELDS_ATTR: dict(fields)},
    )


class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=UTC).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        level = record.levelname.ljust(5)
        event = getattr(record, _EVENT_ATTR, None)
        fields: dict[str, Any] = getattr(record, _FIELDS_ATTR, {}) or {}
        if event is not None:
            parts = [f"event={event}"]
            for k, v in fields.items():
                parts.append(f"{k}={json.dumps(v, default=str)}")
            msg = " ".join(parts)
        else:
            msg = record.getMessage()
        out = f"{ts} {level} {msg}"
        if record.exc_info:
            out = f"{out}\n{self.formatException(record.exc_info)}"
        return out


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "schema_version": LOG_SCHEMA_VERSION,
            "tool_version": __version__,
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
        }
        event = getattr(record, _EVENT_ATTR, None)
        fields: dict[str, Any] = getattr(record, _FIELDS_ATTR, {}) or {}
        if event is not None:
            payload["event"] = event
            payload.update(fields)
        else:
            payload["event"] = "message"
            payload["message"] = record.getMessage()
        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _cleanup_old_logs(log_dir: Path, retention: int) -> None:
    """Trim the log dir to the newest `retention` files. 0 = keep all.

    Sorts by filename, not mtime — names are `%Y-%m-%d-%H%M%S.log`, so
    reverse-lex is stable chronological order and safe under concurrent
    runs where two processes may share an mtime tick.
    """
    if retention <= 0:
        return
    try:
        files = sorted(log_dir.glob("*.log"), key=lambda p: p.name, reverse=True)
    except OSError:
        return
    for old in files[retention:]:
        with contextlib.suppress(OSError):
            old.unlink()


def setup_logger(
    log_dir: Path,
    enabled: bool,
    log_format: str = "text",
    log_level: str = "info",
    retention: int = 20,
) -> tuple[logging.Logger, Path | None]:
    """Return (logger, log_path). When `enabled=False`, returns a
    null-handler logger and a None path."""
    if log_format not in LOG_FORMAT_NAMES:
        raise ValueError(
            f"log_format must be one of {LOG_FORMAT_NAMES}, got: {log_format!r}"
        )
    if log_level not in LOG_LEVEL_NAMES:
        raise ValueError(
            f"log_level must be one of {LOG_LEVEL_NAMES}, got: {log_level!r}"
        )

    logger = logging.getLogger("cleanup_mac")
    logger.setLevel(_level_from_name(log_level))
    # Drop prior handlers so a repeat main() call doesn't double-log.
    for h in list(logger.handlers):
        logger.removeHandler(h)
    logger.propagate = False

    if not enabled:
        logger.addHandler(logging.NullHandler())
        return logger, None

    log_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_old_logs(log_dir, retention)

    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    log_path = log_dir / f"{stamp}.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(
        _JSONFormatter() if log_format == "json" else _TextFormatter()
    )
    # Audit log contains absolute paths + uninstalled bundle IDs; treat
    # as sensitive. 0o600 is non-fatal on filesystems without POSIX bits.
    with contextlib.suppress(OSError):
        log_path.chmod(0o600)
    logger.addHandler(handler)
    return logger, log_path
