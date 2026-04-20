from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Candidate:
    path: Path
    size_bytes: int
    category: str  # user_cache | log | leftover | xcode | system_cache | temp
    reason: str


@dataclass(frozen=True)
class PackageCleanup:
    tool: str  # brew | npm | yarn | pip | go | docker
    current_size_bytes: int
    apply_command: list[str]
