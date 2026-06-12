import asyncio
import json
from pathlib import Path

from src.agent_tools import ToolBlock
from src import ckb_guardrails as guard
from src import tool_execution


def _build_ckb(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "CKB"
    project = root / "Projects" / "Example"
    lessons = root / "📦 Lessons_learned"
    project.mkdir(parents=True)
    lessons.mkdir()
    (root / "AGENTS.md").write_text("# Root rules\nNever expose secrets.\n", encoding="utf-8")
    (root / "index.md").write_text("# Index\n", encoding="utf-8")
    (project / "AGENTS.md").write_text("# Project rules\nAsk before deletion.\n", encoding="utf-8")
    (lessons / "lesson.md").write_text(
        "# GBrain approval\nUse exact one-time approval for destructive calls.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(guard, "CKB_ROOT", root)
    monkeypatch.setattr(guard, "_state_path", lambda: tmp_path / "state.json")
    return project


def _preflight(project: Path) -> None:
    result = guard.run_ckb_preflight(
        json.dumps({"task": "GBrain approval", "target_paths": [str(project)]}),
        session_id="session-1",
        owner="ashton",
    )
    assert result["exit_code"] == 0


def test_preflight_reads_root_dox_chain_and_lessons(tmp_path, monkeypatch):
    project = _build_ckb(tmp_path, monkeypatch)

    result = guard.run_ckb_preflight(
        json.dumps({"task": "GBrain approval", "target_paths": [str(project)]}),
        session_id="session-1",
        owner="ashton",
    )

    assert result["exit_code"] == 0
    assert "Never expose secrets." in result["output"]
    assert "Ask before deletion." in result["output"]
    assert "exact one-time approval" in result["output"]
    assert result["preflight"]["lesson_files_scanned"] == 1


def test_risky_call_requires_preflight(tmp_path, monkeypatch):
    _build_ckb(tmp_path, monkeypatch)

    gate = guard.check_gbrain_call(
        "mcp__gbrain01__delete_page",
        {"slug": "example"},
        session_id="session-1",
        owner="ashton",
    )

    assert gate["allowed"] is False
    assert "ckb_preflight" in gate["result"]["error"]


def test_approval_is_exact_and_consumed_once(tmp_path, monkeypatch):
    project = _build_ckb(tmp_path, monkeypatch)
    _preflight(project)
    tool = "mcp__gbrain01__delete_page"
    args = {"slug": "example"}

    first = guard.check_gbrain_call(tool, args, "session-1", "ashton")
    assert first["allowed"] is False
    assert first["result"]["ask_user"]["options"][0]["label"] == "Approve once"

    decision = guard.process_approval_response(
        "Approve once", session_id="session-1", owner="ashton"
    )
    assert decision["decision"] == "approved"
    assert decision["qualified_tool"] == tool

    approved = guard.check_gbrain_call(tool, args, "session-1", "ashton")
    assert approved == {"allowed": True, "approval_consumed": True}

    repeated = guard.check_gbrain_call(tool, args, "session-1", "ashton")
    assert repeated["allowed"] is False
    assert "ask_user" in repeated["result"]


def test_changed_arguments_do_not_use_existing_approval(tmp_path, monkeypatch):
    project = _build_ckb(tmp_path, monkeypatch)
    _preflight(project)
    tool = "mcp__gbrain01__delete_page"

    guard.check_gbrain_call(tool, {"slug": "one"}, "session-1", "ashton")
    guard.process_approval_response("Approve once", "session-1", "ashton")
    changed = guard.check_gbrain_call(tool, {"slug": "two"}, "session-1", "ashton")

    assert changed["allowed"] is False
    assert "ask_user" in changed["result"]


def test_sensitive_approval_arguments_are_redacted(tmp_path, monkeypatch):
    project = _build_ckb(tmp_path, monkeypatch)
    _preflight(project)

    gate = guard.check_gbrain_call(
        "mcp__gbrain01__file_upload",
        {"path": "report.md", "api_token": "do-not-show"},
        "session-1",
        "ashton",
    )

    question = gate["result"]["ask_user"]["question"]
    assert "do-not-show" not in question
    assert "[REDACTED]" in question


def test_safe_gbrain_call_does_not_require_approval(tmp_path, monkeypatch):
    _build_ckb(tmp_path, monkeypatch)

    assert guard.check_gbrain_call(
        "mcp__gbrain01__query",
        {"query": "CKB"},
        "session-1",
        "ashton",
    ) == {"allowed": True}


def test_dispatcher_pauses_before_calling_mcp(tmp_path, monkeypatch):
    project = _build_ckb(tmp_path, monkeypatch)
    _preflight(project)

    class FakeMCP:
        def __init__(self):
            self.called = False

        async def call_tool(self, tool, args):
            self.called = True
            return {"output": "called", "exit_code": 0}

    fake = FakeMCP()
    monkeypatch.setattr(tool_execution, "get_mcp_manager", lambda: fake)
    desc, result = asyncio.run(tool_execution.execute_tool_block(
        ToolBlock("mcp__gbrain01__delete_page", json.dumps({"slug": "example"})),
        session_id="session-1",
        owner="ashton",
    ))

    assert "CKB guardrail" in desc
    assert "ask_user" in result
    assert fake.called is False
