"""Path root for read_local. write_report never takes a caller path."""

from __future__ import annotations

from pathlib import Path


def resolve_under_root(root: Path, rel: str) -> Path:
    if rel.startswith("/") or "\\" in rel:
        raise PermissionError("path_escape")
    base = root.resolve()
    candidate = (base / rel).resolve()
    if candidate != base and base not in candidate.parents:
        raise PermissionError("path_escape")
    return candidate
