"""aloop-strands CLI. Exit codes follow aloop's gate contract: 0 pass, 2 blocked."""
from __future__ import annotations

import argparse
import shutil
import sys
from importlib import resources
from pathlib import Path

from . import __version__, pins


def _init(root: Path) -> int:
    root.mkdir(parents=True, exist_ok=True)
    data = resources.files("aloop_strands") / "data"
    for sub in ("workflows", "gates"):
        dst = root / sub
        dst.mkdir(exist_ok=True)
        for f in (data / sub).iterdir():
            if f.is_file() and not (dst / f.name).exists():
                (dst / f.name).write_bytes(f.read_bytes())
    pins.init(root)
    print(f"initialized {root}")
    return 0


def _verify(root: Path) -> int:
    diff = pins.verify(root)
    if not diff:
        print("pins ok")
        return 0
    for path, why in diff:
        print(f"BLOCK pins: {path} {why}")
    print(f"approve with: aloop-strands repin {root} {pins.change_code(root)}")
    return 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="aloop-strands")
    ap.add_argument("--version", action="version", version=f"aloop-strands {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("init", "verify"):
        p = sub.add_parser(name)
        p.add_argument("root", nargs="?", default=".")
    p = sub.add_parser("repin")
    p.add_argument("root")
    p.add_argument("code")
    a = ap.parse_args(argv)
    if a.cmd == "init":
        return _init(Path(a.root))
    if a.cmd == "verify":
        return _verify(Path(a.root))
    if a.cmd == "repin":
        try:
            pins.repin(Path(a.root), a.code)
        except pins.ApprovalRequired as e:
            print(f"BLOCK {e}")
            return 2
        print("repinned")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
