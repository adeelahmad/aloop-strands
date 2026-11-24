"""aloop-strands CLI. Exit codes follow aloop's gate contract: 0 pass, 2 blocked."""
from __future__ import annotations

import argparse
import shutil
import sys
from importlib import resources
from pathlib import Path

from . import __version__, pins


def _init(root: Path) -> int:
    from .project import init
    init(root)
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
    p = sub.add_parser("plan", help="intent -> intake, standards, planner; stops for approval")
    p.add_argument("intent")
    p.add_argument("--root", default=".")
    p = sub.add_parser("approve", help="approve the current plan with its code (human only)")
    p.add_argument("code")
    p.add_argument("--root", default=".")
    p.add_argument("--sprint", type=int, default=1)
    p = sub.add_parser("door", help="authorize a one-way door for one task (human only)")
    p.add_argument("task")
    p.add_argument("door")
    p.add_argument("code")
    p.add_argument("--root", default=".")
    p = sub.add_parser("status")
    p.add_argument("--root", default=".")
    p.add_argument("--sprint", type=int, default=1)
    p = sub.add_parser("serve", help="local dashboard on 127.0.0.1")
    p.add_argument("--root", default=".")
    p.add_argument("--port", type=int, default=8787)
    a = ap.parse_args(argv)
    if a.cmd == "plan":
        from .models import make_model
        from .supervisor import Supervisor
        st = Supervisor(Path(a.root), model_factory=make_model).plan(a.intent)
        if st.status != "awaiting_approval":
            print(f"BLOCK {st.reason}")
            return 2
        print(f"plan ready. approve with: aloop-strands approve {st.code}")
        return 0
    if a.cmd == "approve":
        from . import approvals
        try:
            approvals.approve(Path(a.root), a.sprint, a.code, actor="human:cli")
        except approvals.ApprovalRefused as e:
            print(f"BLOCK {e}")
            return 2
        print("approved")
        return 0
    if a.cmd == "door":
        from . import doors
        try:
            doors.authorize(Path(a.root), a.task, a.door, a.code, actor="human:cli")
        except doors.DoorRefused as e:
            print(f"BLOCK {e}")
            return 2
        print("authorized")
        return 0
    if a.cmd == "status":
        from . import approvals
        ok, why = approvals.approved(Path(a.root), a.sprint)
        print(f"sprint {a.sprint}: {why}; code {approvals.code_for(Path(a.root), a.sprint)}")
        return 0
    if a.cmd == "serve":
        from .dashboard import make_server
        srv = make_server(Path(a.root), port=a.port)
        print(f"dashboard on http://127.0.0.1:{srv.server_address[1]}")
        srv.serve_forever()
        return 0
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
