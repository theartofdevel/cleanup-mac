from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Absolute path — bare "du" would be vulnerable to PATH hijack.
DU_BIN = "/usr/bin/du"
_SIZE_WORKERS = 8


def format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    units = ["KB", "MB", "GB", "TB"]
    value = float(n)
    for unit in units:
        value /= 1024
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} {units[-1]}"


def get_size(path: Path) -> int:
    """Size in bytes via `du -sk`. Returns 0 on error."""
    if not path.exists():
        return 0
    try:
        result = subprocess.run(
            [DU_BIN, "-sk", str(path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        kb = int(result.stdout.split()[0])
        return kb * 1024
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
        return 0


def get_sizes(paths: list[Path]) -> dict[Path, int]:
    """Parallel get_size for many paths. Missing entries default to 0."""
    if not paths:
        return {}
    with ThreadPoolExecutor(max_workers=_SIZE_WORKERS) as pool:
        results = list(pool.map(get_size, paths))
    return dict(zip(paths, results, strict=True))
