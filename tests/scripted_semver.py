"""Scripted role models for the 'a semver parser' e2e (zero tokens, deterministic)."""
import json

from aloop_strands.scripted import ScriptedModel

INTENT = {"actor": "a developer", "want": "a semver parser", "why": "compare release versions",
          "success": ["parse('1.2.3') == (1, 2, 3)"], "failure": ["parse('1.x') raises ValueError"]}
STANDARDS = "# Standards\n\nlanguage: python\ntest_command: python -m pytest -q -p no:cacheprovider\n"
PLAN = {"sprint": 1, "workflow": "tdd", "stories": [{"id": "S1-01", "title": "Parse versions", "tasks": [
    {"id": "S1-01-T1", "title": "parse MAJOR.MINOR.PATCH into a tuple; reject anything else",
     "tests": ["tests/test_semver.py"], "targets": ["src/semver.py"]}]}]}
TESTS = '''import pytest

from semver import parse


def test_parses_three_numbers():
    assert parse("1.2.3") == (1, 2, 3)


def test_rejects_non_numeric():
    with pytest.raises(ValueError):
        parse("1.x.3")


def test_rejects_wrong_arity():
    with pytest.raises(ValueError):
        parse("1.2")
'''
STUB = 'def parse(text):\n    """Parse MAJOR.MINOR.PATCH."""\n    raise NotImplementedError("SUB-AGENT-TODO: parse")\n'
IMPL = '''def parse(text):
    """Parse MAJOR.MINOR.PATCH."""
    parts = text.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"not a semver: {text!r}")
    return tuple(int(p) for p in parts)
'''
CONFTEST = "import pathlib\nimport sys\n\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"


def w(tool, path, content):
    return {"tool": tool, "input": {"path": path, "content": content}}


def models(green_turns=None):
    return {
        "intake": ScriptedModel([w("write_artifact", "docs/agents/intent.json", json.dumps(INTENT)), "ok"]),
        "standards": ScriptedModel([w("write_artifact", "docs/agents/standards.md", STANDARDS), "ok"]),
        "planner": ScriptedModel([w("write_artifact", "docs/agents/sprint1/plan.json", json.dumps(PLAN)), "ok"]),
        "red": ScriptedModel([w("write_file", "tests/conftest.py", CONFTEST),
                              w("write_file", "tests/test_semver.py", TESTS), {"tool": "run_tests"}, "tests fail"]),
        "scaffold": ScriptedModel([w("write_file", "src/semver.py", STUB), "stubbed"]),
        "green": ScriptedModel(green_turns or [w("write_file", "src/semver.py", IMPL), {"tool": "run_tests"}, "done"]),
        "structural_review": ScriptedModel([{"tool": "write_report", "input": {
            "path": "docs/agents/reports/S1-01-T1.md", "content": "no findings"}}, "clean"]),
        "final_verification": ScriptedModel([{"tool": "run_tests"}, "all green"]),
    }
