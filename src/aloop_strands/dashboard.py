"""Small local dashboard: shows the plan and gate decisions, approves a plan with its code.
Standard library only; binds to 127.0.0.1."""
from __future__ import annotations

import html
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import approvals
from .events import EventLog


def _page(root: Path, sprint: int) -> str:
    plan_f = approvals.plan_files(root, sprint)[2]
    plan = plan_f.read_text() if plan_f.exists() else "(no plan yet)"
    ok, why = approvals.approved(root, sprint) if plan_f.exists() else (False, "no plan")
    code = approvals.code_for(root, sprint) if plan_f.exists() else "-"
    rows = "".join(
        f"<tr><td>{e['seq']}</td><td>{html.escape(e['kind'])}</td><td>{html.escape(str(e.get('rule', '')))}</td>"
        f"<td>{html.escape(str(e.get('verdict', '')))}</td><td>{html.escape(str(e.get('role', '')))}</td>"
        f"<td>{html.escape(str(e.get('task', '')))}</td><td>{html.escape(str(e.get('reason', '')))}</td></tr>"
        for e in list(EventLog(root).query())[-200:])
    return f"""<!doctype html><meta charset=utf-8><title>aloop-strands</title>
<style>body{{font:14px system-ui;margin:24px;max-width:1100px}}pre{{background:#f4f4f4;padding:12px;overflow:auto}}
td{{border-bottom:1px solid #ddd;padding:4px 8px;vertical-align:top}}</style>
<h1>aloop-strands</h1><p><i>Models explore. Gates decide.</i></p>
<h2>Sprint {sprint} plan</h2><p>Status: <b>{html.escape(why)}</b> &middot; approval code <code>{code}</code></p>
<pre>{html.escape(plan)}</pre>
<form method=post action=/approve><input type=hidden name=sprint value={sprint}>
Type the code to approve this exact plan: <input name=code size=10> <button>Approve</button></form>
<h2>Gate decisions</h2><table><tr><th>#</th><th>kind</th><th>rule</th><th>verdict</th><th>role</th><th>task</th><th>reason</th></tr>{rows}</table>"""


def make_server(root: Path | str, port: int = 8787, sprint: int = 1, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    root = Path(root)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body: str, ctype="text/html"):
            data = body.encode()
            self.send_response(code)
            self.send_header("content-type", ctype + "; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/events.json":
                return self._send(200, json.dumps(list(EventLog(root).query())), "application/json")
            self._send(200, _page(root, sprint))

        def do_POST(self):
            if self.path != "/approve":
                return self._send(404, "not found")
            n = int(self.headers.get("content-length", "0"))
            form = urllib.parse.parse_qs(self.rfile.read(n).decode())
            try:
                approvals.approve(root, int(form.get("sprint", [sprint])[0]), form.get("code", [""])[0],
                                  actor="human:dashboard")
            except (approvals.ApprovalRefused, ValueError) as e:
                return self._send(403, f"refused: {html.escape(str(e))}")
            self._send(200, "approved. <a href=/>back</a>")

    return ThreadingHTTPServer((host, port), H)
