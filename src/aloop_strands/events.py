"""Hash-chained event log in aloop's `aladdin.event/1` shape.

Every gate decision and run event is one JSON line under `.aladdin/logs/events.jsonl`.
Each line carries `prev` = sha256 of the previous raw line, and `events.head` records the
count and hash of the last line, so editing, deleting or truncating any line is detected
(this closes aloop ROADMAP M0.9's "run log not hash-chained" gap). The log lives on the host;
workers never get it mounted.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Iterator

SCHEMA = "aladdin.event/1"
GENESIS = "0" * 64


class ChainBroken(RuntimeError):
    """The log was edited, truncated or reordered; nothing more may be appended."""


def _h(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class EventLog:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.path = self.root / ".aladdin" / "logs" / "events.jsonl"
        self.head = self.path.with_name("events.head")

    # -- reading -------------------------------------------------------------
    def _lines(self) -> list[str]:
        if not self.path.exists():
            return []
        return [ln for ln in self.path.read_text().splitlines() if ln.strip()]

    def verify(self) -> tuple[bool, int | None]:
        """(True, None) when intact, else (False, index of the first bad line)."""
        lines = self._lines()
        prev = GENESIS
        for i, raw in enumerate(lines):
            try:
                rec = json.loads(raw)
            except ValueError:
                return False, i
            if rec.get("prev") != prev or rec.get("seq") != i:
                return False, i
            prev = _h(raw)
        head = self._read_head()
        if head is None:
            return (True, None) if not lines else (False, len(lines))
        if head != {"count": len(lines), "last": prev}:
            return False, len(lines)
        return True, None

    def _read_head(self) -> dict | None:
        if not self.head.exists():
            return None
        try:
            return json.loads(self.head.read_text())
        except ValueError:
            return {"count": -1, "last": ""}

    def query(self, kind: str | None = None, **match: Any) -> Iterator[dict]:
        for raw in self._lines():
            rec = json.loads(raw)
            if kind is not None and rec.get("kind") != kind:
                continue
            if all(rec.get(k) == v for k, v in match.items()):
                yield rec

    # -- writing -------------------------------------------------------------
    def append(self, kind: str, **fields: Any) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_name("events.lock")
        with open(lock, "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            ok, _ = self.verify()
            if not ok:
                raise ChainBroken(f"{self.path} failed verification; refusing to append")
            lines = self._lines()
            prev = _h(lines[-1]) if lines else GENESIS
            rec = {"schema": SCHEMA, "seq": len(lines), "ts": time.time(), "kind": kind, **fields, "prev": prev}
            raw = json.dumps(rec, sort_keys=True)
            with open(self.path, "a") as f:
                f.write(raw + "\n")
            self.head.write_text(json.dumps({"count": len(lines) + 1, "last": _h(raw)}))
        return rec
