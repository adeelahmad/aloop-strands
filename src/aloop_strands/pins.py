"""Gate and workflow integrity (aloop's task.env lesson).

Every file under the pinned roots (gates/, workflows/, box/) is recorded by sha256 in
`.aladdin/pins.json`. The supervisor verifies pins before every dispatch; any difference
blocks. Re-pinning needs the human to type the 8-hex code printed for exactly the new
content, so an agent cannot approve its own change. A missing or unreadable pin file
fails closed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .events import EventLog

PINNED_DIRS = ("gates", "workflows", "box")


class ApprovalRequired(PermissionError):
    pass


def _pin_file(root: Path) -> Path:
    return Path(root) / ".aladdin" / "pins.json"


def compute(root: Path | str) -> dict[str, str]:
    root = Path(root)
    out: dict[str, str] = {}
    for d in PINNED_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() or p.is_symlink():
                data = p.read_bytes() if p.is_file() else ("symlink:" + str(p.readlink())).encode()
                out[p.relative_to(root).as_posix()] = hashlib.sha256(data).hexdigest()
    return out


def _digest(m: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()


def load(root: Path | str) -> dict[str, str] | None:
    f = _pin_file(Path(root))
    if not f.exists():
        raise FileNotFoundError(f)
    data = json.loads(f.read_text())
    files = data["files"]
    if data.get("digest") != _digest(files):
        raise ValueError("pin digest mismatch")
    return files


def _write(root: Path, files: dict[str, str]) -> None:
    f = _pin_file(root)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"files": files, "digest": _digest(files)}, indent=1, sort_keys=True) + "\n")


def init(root: Path | str) -> dict[str, str]:
    root = Path(root)
    if _pin_file(root).exists():
        raise FileExistsError("pins already exist; changes need repin with the human's code")
    files = compute(root)
    _write(root, files)
    EventLog(root).append("pins.init", digest=_digest(files), count=len(files))
    return files


def verify(root: Path | str) -> list[tuple[str, str]]:
    """Empty list = intact. Fails closed on a missing or unreadable pin file."""
    root = Path(root)
    try:
        pinned = load(root)
    except FileNotFoundError:
        return [("<pins>", "missing")]
    except (ValueError, KeyError, TypeError):
        return [("<pins>", "unreadable")]
    now = compute(root)
    diff = []
    for k in sorted(set(pinned) | set(now)):
        if k not in now:
            diff.append((k, "removed"))
        elif k not in pinned:
            diff.append((k, "added"))
        elif pinned[k] != now[k]:
            diff.append((k, "modified"))
    return diff


def change_code(root: Path | str) -> str:
    """8-hex code the human types to approve exactly the current content."""
    return _digest(compute(root))[:8]


def repin(root: Path | str, typed: str) -> None:
    root = Path(root)
    files = compute(root)
    code = _digest(files)[:8]
    if typed.strip().lower() != code:
        EventLog(root).append("pins.refused", rule="pins.integrity", verdict="deny",
                              reason="typed code does not match the current gate/workflow content")
        raise ApprovalRequired(f"type the code for this exact content: {code}")
    _write(root, files)
    EventLog(root).append("pins.approved", rule="pins.integrity", verdict="allow", actor="human",
                          digest=_digest(files), count=len(files))
