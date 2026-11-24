"""Worktree helpers. Tree hashes identify exactly what was tested."""
from __future__ import annotations

import hashlib
from pathlib import Path

SKIP = {".git", ".aladdin", "__pycache__", ".pytest_cache"}


def tree_hash(root: Path) -> str:
    root = Path(root)
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if SKIP & set(rel.parts) or not p.is_file():
            continue
        h.update(rel.as_posix().encode() + b"\0" + hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()
