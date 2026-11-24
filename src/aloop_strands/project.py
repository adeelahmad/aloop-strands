"""Project setup: a git repo whose main branch carries the code, the workflows and the gate
config, versioned together (`.aladdin/` run state and `.worktrees/` stay out of git)."""
from __future__ import annotations

import subprocess
from importlib import resources
from pathlib import Path

from . import pins

AUTHOR = {"GIT_AUTHOR_NAME": "aloop-strands", "GIT_AUTHOR_EMAIL": "aloop-strands@localhost",
          "GIT_COMMITTER_NAME": "aloop-strands", "GIT_COMMITTER_EMAIL": "aloop-strands@localhost"}


def git(root: Path, *args: str, check: bool = True) -> str:
    import os
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                       env={**os.environ, **AUTHOR}, timeout=120)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def copy_data(root: Path) -> None:
    data = resources.files("aloop_strands") / "data"
    for sub in ("workflows", "gates", "box"):
        src = data / sub
        if not src.is_dir():
            continue
        dst = root / sub
        dst.mkdir(exist_ok=True)
        for f in src.iterdir():
            if f.is_file() and f.name != ".keep" and not (dst / f.name).exists():
                (dst / f.name).write_bytes(f.read_bytes())


def init(root: Path | str) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        git(root, "init", "-q", "-b", "main")
    copy_data(root)
    gi = root / ".gitignore"
    lines = gi.read_text().splitlines() if gi.exists() else []
    for pat in (".aladdin/", ".worktrees/", "__pycache__/", ".pytest_cache/"):
        if pat not in lines:
            lines.append(pat)
    gi.write_text("\n".join(lines) + "\n")
    if not (root / ".aladdin" / "pins.json").exists():
        pins.init(root)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "aloop-strands init", "--allow-empty")
    return root
