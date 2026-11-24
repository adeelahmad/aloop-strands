"""Hash-chained gate/run event log (.aladdin/logs/events.jsonl, aloop's aladdin.event/1 shape)."""
import json

import pytest

from aloop_strands import events


def _log(tmp_path):
    return events.EventLog(tmp_path)


def test_append_writes_aloop_compatible_line(tmp_path):
    log = _log(tmp_path)
    rec = log.append("gate.decision", rule="gate.role", verdict="deny", reason="supervisor may not write",
                     role="supervisor", task="S1-01-T1", commit="abc123")
    path = tmp_path / ".aladdin" / "logs" / "events.jsonl"
    line = json.loads(path.read_text().splitlines()[0])
    assert line["schema"] == "aladdin.event/1"
    assert line["kind"] == "gate.decision"
    for k in ("rule", "verdict", "reason", "role", "task", "commit", "ts", "seq", "prev"):
        assert k in line
    assert line["seq"] == 0 and rec["seq"] == 0


def test_chain_verifies_when_untouched(tmp_path):
    log = _log(tmp_path)
    for i in range(5):
        log.append("gate.decision", rule=f"r{i}", verdict="allow", reason="ok")
    assert log.verify() == (True, None)


def test_edited_line_breaks_chain(tmp_path):
    log = _log(tmp_path)
    for i in range(3):
        log.append("gate.decision", rule=f"r{i}", verdict="deny", reason="no")
    p = log.path
    lines = p.read_text().splitlines()
    lines[1] = lines[1].replace('"deny"', '"allow"')
    p.write_text("\n".join(lines) + "\n")
    ok, bad = log.verify()
    assert not ok and bad == 2  # line 2 no longer chains to the edited line 1


def test_deleted_middle_line_breaks_chain(tmp_path):
    log = _log(tmp_path)
    for i in range(3):
        log.append("x", n=i)
    lines = log.path.read_text().splitlines()
    del lines[1]
    log.path.write_text("\n".join(lines) + "\n")
    assert log.verify()[0] is False


def test_deleted_last_line_detected_by_head(tmp_path):
    log = _log(tmp_path)
    for i in range(3):
        log.append("x", n=i)
    lines = log.path.read_text().splitlines()
    log.path.write_text("\n".join(lines[:-1]) + "\n")
    assert log.verify()[0] is False


def test_append_refuses_on_broken_chain(tmp_path):
    log = _log(tmp_path)
    log.append("x", n=0)
    log.path.write_text(log.path.read_text().replace('"n": 0', '"n": 1'))
    with pytest.raises(events.ChainBroken):
        log.append("x", n=2)


def test_query_filters_by_kind(tmp_path):
    log = _log(tmp_path)
    log.append("a", n=1)
    log.append("b", n=2)
    log.append("a", n=3)
    assert [e["n"] for e in log.query(kind="a")] == [1, 3]
