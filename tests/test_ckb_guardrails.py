import asyncio
import json
from pathlib import Path

from src.agent_tools import ToolBlock
from src import ckb_guardrails as guard
from src import tool_execution
from src.agent_runtime.authority import OperationGrant, RequestAuthority


def _build_ckb(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "CKB"
    project = root / "Projects" / "Example"
    lessons = root / "📦 Lessons_learned"
    project.mkdir(parents=True)
    lessons.mkdir()
    (root / "GLOBAL_AI_ROUTER.md").write_text(
        "# Global AI router\nUse direct language and progressive skill loading.\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("# Root rules\nNever expose secrets.\n", encoding="utf-8")
    (root / "index.md").write_text("# Index\n", encoding="utf-8")
    (project / "AGENTS.md").write_text("# Project rules\nAsk before deletion.\n", encoding="utf-8")
    (lessons / "lesson.md").write_text(
        "# GBrain approval\nUse exact one-time approval for destructive calls.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(guard, "CKB_ROOT", root)
    monkeypatch.setattr(guard, "_state_path", lambda: tmp_path / "state.json")
    # Upstream treats the pre-setup window as non-admin, so tests name the
    # admin explicitly. Tests that need a different split re-patch this.
    import src.tool_security as ts
    monkeypatch.setattr(ts, "owner_is_admin_or_single_user", lambda owner: owner == "ashton")
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
    assert "Use direct language and progressive skill loading." in result["output"]
    assert "Ask before deletion." in result["output"]
    assert "exact one-time approval" in result["output"]
    assert result["preflight"]["lesson_files_scanned"] == 1


def test_policy_prompt_injects_global_router(tmp_path, monkeypatch):
    _build_ckb(tmp_path, monkeypatch)

    prompt = guard.get_policy_prompt()

    assert "Injected global AI router" in prompt
    assert "Use direct language and progressive skill loading." in prompt


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


def test_non_admin_can_use_readonly_gbrain_tool(tmp_path, monkeypatch):
    """Phase 6: non-admin users may call read-only GBrain tools."""
    _build_ckb(tmp_path, monkeypatch)
    import src.tool_security as ts
    monkeypatch.setattr(ts, "owner_is_admin_or_single_user", lambda owner: owner == "ashto")

    result = guard.check_gbrain_call(
        "mcp__gbrain01__search",
        {"query": "CKB"},
        session_id="session-1",
        owner="anne-marie",
    )

    assert result == {"allowed": True}


def test_non_admin_blocked_from_write_gbrain_tool(tmp_path, monkeypatch):
    """Phase 6: non-admin users cannot call write/destructive GBrain tools."""
    _build_ckb(tmp_path, monkeypatch)
    import src.tool_security as ts
    monkeypatch.setattr(ts, "owner_is_admin_or_single_user", lambda owner: owner == "ashto")

    result = guard.check_gbrain_call(
        "mcp__gbrain01__delete_page",
        {"slug": "example"},
        session_id="session-1",
        owner="anne-marie",
    )

    assert result["allowed"] is False
    assert "admin" in result["result"]["error"].lower()


def test_admin_still_reaches_approval_flow_for_write_tool(tmp_path, monkeypatch):
    """Phase 6: admin users still go through the approval card for write tools."""
    project = _build_ckb(tmp_path, monkeypatch)
    import src.tool_security as ts
    monkeypatch.setattr(ts, "owner_is_admin_or_single_user", lambda owner: owner == "ashton")
    _preflight(project)

    result = guard.check_gbrain_call(
        "mcp__gbrain01__delete_page",
        {"slug": "example"},
        session_id="session-1",
        owner="ashton",
    )

    assert result["allowed"] is False
    assert "ask_user" in result["result"]


def test_dispatcher_pauses_before_calling_mcp(tmp_path, monkeypatch):
    project = _build_ckb(tmp_path, monkeypatch)
    _preflight(project)

    class FakeMCP:
        def __init__(self):
            self.called = False

        def resource_identity(self, qualified_name):
            from src.agent_runtime.resources import ExternalResource
            server = qualified_name.split("__")[1]
            return ExternalResource("mcp", f"mcp:{server}", server, qualified_name, "fake")

        async def call_tool(self, tool, args):
            self.called = True
            return {"output": "called", "exit_code": 0}

    fake = FakeMCP()
    monkeypatch.setattr(tool_execution, "get_mcp_manager", lambda: fake)
    desc, result = asyncio.run(tool_execution.execute_tool_block(
        ToolBlock("mcp__gbrain01__delete_page", json.dumps({"slug": "example"})),
        session_id="session-1",
        owner="ashton",
        security_context=tool_execution.NO_TOOL_SECURITY_CONTEXT,
        request_authority=RequestAuthority(
            "ckb-test", "ashton", "session-1", "",
            (OperationGrant("mcp__gbrain01__delete_page"),),
        ),
    ))

    assert "CKB guardrail" in desc
    assert "ask_user" in result
    assert fake.called is False


# --- Guard rules that had no direct test before the upstream catch-up. ---


def test_denied_approval_does_not_run_the_call(tmp_path, monkeypatch):
    project = _build_ckb(tmp_path, monkeypatch)
    _preflight(project)
    tool = "mcp__gbrain01__delete_page"
    args = {"slug": "example"}

    guard.check_gbrain_call(tool, args, "session-1", "ashton")
    decision = guard.process_approval_response("Deny", "session-1", "ashton")
    assert decision["decision"] == "denied"

    again = guard.check_gbrain_call(tool, args, "session-1", "ashton")
    assert again["allowed"] is False
    assert "ask_user" in again["result"]


def test_gated_call_without_session_is_refused(tmp_path, monkeypatch):
    _build_ckb(tmp_path, monkeypatch)

    gate = guard.check_gbrain_call(
        "mcp__gbrain01__delete_page", {"slug": "x"}, session_id=None, owner="ashton"
    )

    assert gate["allowed"] is False
    assert "session" in gate["result"]["error"]


def test_expired_approval_is_not_used(tmp_path, monkeypatch):
    project = _build_ckb(tmp_path, monkeypatch)
    _preflight(project)
    tool = "mcp__gbrain01__delete_page"
    args = {"slug": "example"}

    guard.check_gbrain_call(tool, args, "session-1", "ashton")
    guard.process_approval_response("Approve once", "session-1", "ashton")
    now = guard.time.time()
    monkeypatch.setattr(guard.time, "time", lambda: now + guard.APPROVAL_TTL_SECONDS + 1)

    late = guard.check_gbrain_call(tool, args, "session-1", "ashton")
    assert late["allowed"] is False


def test_approval_does_not_cross_sessions(tmp_path, monkeypatch):
    project = _build_ckb(tmp_path, monkeypatch)
    _preflight(project)
    tool = "mcp__gbrain01__delete_page"
    args = {"slug": "example"}

    guard.check_gbrain_call(tool, args, "session-1", "ashton")
    guard.process_approval_response("Approve once", "session-1", "ashton")

    other = guard.check_gbrain_call(tool, args, "session-2", "ashton")
    assert other["allowed"] is False


def test_preflight_rejects_path_outside_ckb(tmp_path, monkeypatch):
    _build_ckb(tmp_path, monkeypatch)

    result = guard.run_ckb_preflight(
        json.dumps({"task": "x", "target_paths": [str(tmp_path / "elsewhere")]}),
        session_id="session-1",
        owner="ashton",
    )

    assert result["exit_code"] == 1
    assert "outside CKB" in result["error"]


def test_preflight_stops_when_required_file_missing(tmp_path, monkeypatch):
    project = _build_ckb(tmp_path, monkeypatch)
    (guard.CKB_ROOT / "index.md").unlink()

    result = guard.run_ckb_preflight(
        json.dumps({"task": "x", "target_paths": [str(project)]}),
        session_id="session-1",
        owner="ashton",
    )

    assert result["exit_code"] == 1
    assert "index.md" in result["error"]
    # A failed preflight must not unlock gated calls.
    gate = guard.check_gbrain_call(
        "mcp__gbrain01__delete_page", {"slug": "x"}, "session-1", "ashton"
    )
    assert "ckb_preflight" in gate["result"]["error"]


def test_policy_prompt_reports_missing_router(tmp_path, monkeypatch):
    _build_ckb(tmp_path, monkeypatch)
    (guard.CKB_ROOT / "GLOBAL_AI_ROUTER.md").unlink()

    prompt = guard.get_policy_prompt()

    assert "Required router unavailable" in prompt
    assert "CKB operating contract" in prompt


def test_public_blocklist_passes_only_gbrain_mcp_through():
    from src.tool_security import is_public_blocked_tool

    assert is_public_blocked_tool("mcp__gbrain01__search") is False
    assert is_public_blocked_tool("mcp__other__search") is True
    for tool in ("bash", "python", "send_email", "manage_mcp", "manage_calendar"):
        assert is_public_blocked_tool(tool) is True


def test_ckb_preflight_is_always_offered_and_parseable():
    from src.tool_index import ALWAYS_AVAILABLE
    from src.tool_types import TOOL_TAGS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    assert "ckb_preflight" in ALWAYS_AVAILABLE
    assert "ckb_preflight" in TOOL_TAGS
    assert any(s["function"]["name"] == "ckb_preflight" for s in FUNCTION_TOOL_SCHEMAS)
