"""AST ban: the cooperative kernel must not call time.sleep."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCAN_PATHS = [
    ROOT / "agentos" / "kernel",
    ROOT / "agentos" / "llm" / "scripted.py",
    ROOT / "agentos" / "workloads",
]


def _py_files() -> list[Path]:
    found: list[Path] = []
    for path in SCAN_PATHS:
        if path.is_file():
            found.append(path)
        elif path.is_dir():
            found.extend(sorted(path.rglob("*.py")))
    return found


def _sleep_calls(path: Path) -> list[str]:
    """Flag time.sleep. The kernel's own sleep(chan) is a state change, not a wall-clock wait."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    time_names = {"time"}
    imported_sleep: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "time":
                    time_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "time":
            for alias in node.names:
                if alias.name == "sleep":
                    imported_sleep.add(alias.asname or alias.name)
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "sleep":
            root = func.value
            if isinstance(root, ast.Name) and root.id in time_names:
                hits.append(f"{path}:{node.lineno}")
        elif isinstance(func, ast.Name) and func.id in imported_sleep:
            hits.append(f"{path}:{node.lineno}")
    return hits


def test_kernel_and_scripted_backend_do_not_call_sleep() -> None:
    files = _py_files()
    assert files, "expected kernel sources to scan"
    hits = [hit for path in files for hit in _sleep_calls(path)]
    assert hits == []
