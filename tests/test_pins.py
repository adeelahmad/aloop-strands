"""Gate/workflow integrity: files pinned by sha256; a change needs the human's typed code."""
import pytest

from aloop_strands import pins


def _tree(tmp_path):
    (tmp_path / "gates").mkdir()
    (tmp_path / "gates" / "role.yaml").write_text("deny: write\n")
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "tdd.yaml").write_text("steps: [red, green]\n")
    return tmp_path


def test_init_then_verify_clean(tmp_path):
    root = _tree(tmp_path)
    pins.init(root)
    assert pins.verify(root) == []


def test_modified_file_is_reported(tmp_path):
    root = _tree(tmp_path)
    pins.init(root)
    (root / "gates" / "role.yaml").write_text("deny: nothing\n")
    assert pins.verify(root) == [("gates/role.yaml", "modified")]


def test_added_and_removed_files_are_reported(tmp_path):
    root = _tree(tmp_path)
    pins.init(root)
    (root / "workflows" / "tdd.yaml").unlink()
    (root / "gates" / "new.yaml").write_text("x\n")
    assert sorted(pins.verify(root)) == [("gates/new.yaml", "added"), ("workflows/tdd.yaml", "removed")]


def test_missing_pin_file_fails_closed(tmp_path):
    root = _tree(tmp_path)
    assert pins.verify(root) == [("<pins>", "missing")]


def test_tampered_pin_file_fails_closed(tmp_path):
    root = _tree(tmp_path)
    pins.init(root)
    (root / ".aladdin" / "pins.json").write_text("{not json")
    assert pins.verify(root) == [("<pins>", "unreadable")]


def test_repin_requires_matching_human_code(tmp_path):
    root = _tree(tmp_path)
    pins.init(root)
    (root / "gates" / "role.yaml").write_text("deny: everything\n")
    code = pins.change_code(root)
    assert len(code) == 8
    with pytest.raises(pins.ApprovalRequired):
        pins.repin(root, typed="deadbeef")
    pins.repin(root, typed=code)
    assert pins.verify(root) == []


def test_code_changes_when_content_changes(tmp_path):
    root = _tree(tmp_path)
    pins.init(root)
    (root / "gates" / "role.yaml").write_text("a\n")
    c1 = pins.change_code(root)
    (root / "gates" / "role.yaml").write_text("b\n")
    assert pins.change_code(root) != c1


def test_repin_is_logged(tmp_path):
    from aloop_strands.events import EventLog
    root = _tree(tmp_path)
    pins.init(root)
    (root / "gates" / "role.yaml").write_text("z\n")
    pins.repin(root, typed=pins.change_code(root))
    kinds = [e["kind"] for e in EventLog(root).query()]
    assert "pins.init" in kinds and "pins.approved" in kinds
