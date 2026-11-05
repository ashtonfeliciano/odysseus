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


# --- Fork tools inside upstream's request authority (review 2026-11-05). ---


class _FakeGBrainMCP:
    """Minimal MCP manager with a connected gbrain01 server."""

    def __init__(self):
        self.calls = []
        self._tools = {"gbrain01": [{"name": "search"}, {"name": "delete_page"}]}

    def resource_identity(self, qualified_name):
        from src.agent_runtime.resources import ExternalResource
        server = qualified_name.split("__")[1]
        return ExternalResource("mcp", f"mcp:{server}", server, qualified_name, "fake")

    async def call_tool(self, tool, args):
        self.calls.append((tool, args))
        return {"output": "called", "exit_code": 0}


def _install_fake_mcp(monkeypatch):
    import src.tool_utils as tool_utils

    fake = _FakeGBrainMCP()
    monkeypatch.setattr(tool_utils, "get_mcp_manager", lambda: fake)
    monkeypatch.setattr(tool_execution, "get_mcp_manager", lambda: fake)
    return fake


def _authority_grants(text, owner):
    from src.agent_runtime.authority import create_request_authority

    authority = create_request_authority(text, owner=owner, session_id="session-1")
    return authority, {grant.tool for grant in authority.grants}


def test_request_authority_admits_fork_tools_for_admin(tmp_path, monkeypatch):
    _build_ckb(tmp_path, monkeypatch)
    _install_fake_mcp(monkeypatch)

    _, grants = _authority_grants("update the Example project notes in the CKB", "ashton")

    assert "ckb_preflight" in grants
    assert "mcp__gbrain01__search" in grants
    assert "mcp__gbrain01__delete_page" in grants


def test_request_authority_gives_non_admin_only_gbrain_reads(tmp_path, monkeypatch):
    _build_ckb(tmp_path, monkeypatch)
    _install_fake_mcp(monkeypatch)

    _, grants = _authority_grants("search gbrain for onboarding", "regular-user")

    assert "mcp__gbrain01__search" in grants
    assert "mcp__gbrain01__delete_page" not in grants
    assert "ckb_preflight" not in grants


def test_ckb_preflight_runs_under_real_request_authority(tmp_path, monkeypatch):
    project = _build_ckb(tmp_path, monkeypatch)
    _install_fake_mcp(monkeypatch)
    authority, _ = _authority_grants("check the Example project rules", "ashton")

    desc, result = asyncio.run(tool_execution.execute_tool_block(
        ToolBlock("ckb_preflight", json.dumps({"task": "rules", "target_paths": [str(project)]})),
        session_id="session-1",
        owner="ashton",
        security_context=tool_execution.NO_TOOL_SECURITY_CONTEXT,
        request_authority=authority,
    ))

    assert desc == "ckb_preflight"
    assert result["exit_code"] == 0, result


def test_approved_gbrain_call_runs_exactly_once_through_dispatcher(tmp_path, monkeypatch):
    project = _build_ckb(tmp_path, monkeypatch)
    _preflight(project)
    fake = _install_fake_mcp(monkeypatch)

    def run():
        authority, _ = _authority_grants("Approve once", "ashton")
        return asyncio.run(tool_execution.execute_tool_block(
            ToolBlock("mcp__gbrain01__delete_page", json.dumps({"slug": "example"})),
            session_id="session-1",
            owner="ashton",
            security_context=tool_execution.NO_TOOL_SECURITY_CONTEXT,
            request_authority=authority,
        ))

    _, first = run()
    assert "ask_user" in first and fake.calls == []
    assert guard.process_approval_response("Approve once", "session-1", "ashton")["decision"] == "approved"
    _, second = run()
    assert second.get("exit_code") == 0 and len(fake.calls) == 1
    _, third = run()
    assert "ask_user" in third and len(fake.calls) == 1


def test_turn_contract_offers_fork_tools(tmp_path, monkeypatch):
    from routes.chat_routes import _fork_ckb_contract_tools
    from src.tool_policy import build_effective_tool_policy
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.turn_contract import requested_capabilities, resolve_turn_contract

    _build_ckb(tmp_path, monkeypatch)
    _install_fake_mcp(monkeypatch)
    schemas = list(FUNCTION_TOOL_SCHEMAS) + [
        {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
        for name in ("mcp__gbrain01__search", "mcp__gbrain01__delete_page")
    ]

    def offered(message):
        contract = resolve_turn_contract(
            capabilities=requested_capabilities(message, []),
            schemas=schemas,
            policy=build_effective_tool_policy(disabled_tools=set(), last_user_message=message),
            always_available_tools=_fork_ckb_contract_tools("ashton", "session-1", message),
            message=message,
        )
        return set(contract.offered)

    fork = {"ckb_preflight", "mcp__gbrain01__search", "mcp__gbrain01__delete_page"}
    # A CKB/GBrain turn offers the fork tools.
    assert fork <= offered("search gbrain for onboarding")
    # An unrelated turn keeps upstream's narrow list.
    assert not fork & offered("Approve once")
    # An approval reply offers them while a GBrain call is pending.
    project = tmp_path / "CKB" / "Projects" / "Example"
    _preflight(project)
    guard.check_gbrain_call("mcp__gbrain01__delete_page", {"slug": "x"}, "session-1", "ashton")
    assert fork <= offered("Approve once")


def test_ckb_preflight_is_recorded_as_dispatched(tmp_path, monkeypatch):
    from src.agent_runtime import journal

    project = _build_ckb(tmp_path, monkeypatch)
    _install_fake_mcp(monkeypatch)
    authority, _ = _authority_grants("check the Example project rules", "ashton")
    seen = []
    real = journal.mark_dispatch
    monkeypatch.setattr(journal, "mark_dispatch", lambda: (seen.append(True), real())[1])

    _, result = asyncio.run(tool_execution.execute_tool_block(
        ToolBlock("ckb_preflight", json.dumps({"task": "rules", "target_paths": [str(project)]})),
        session_id="session-1",
        owner="ashton",
        security_context=tool_execution.NO_TOOL_SECURITY_CONTEXT,
        request_authority=authority,
    ))

    assert result["exit_code"] == 0
    assert seen == [True]


# --- Google Drive MCP exception and one-click approval (2026-11-05 follow-up). ---

_DRIVE_ID = "gd7f3a21"


class _FakeForkMCP(_FakeGBrainMCP):
    """gbrain01, a Google Drive MCP server and an unrelated add-on server."""

    def __init__(self, extra_drive_tools=()):
        super().__init__()
        self._tools[_DRIVE_ID] = [
            {"name": name}
            for name in sorted(guard.GDRIVE_READ_TOOLS | guard.GDRIVE_WRITE_TOOLS) + list(extra_drive_tools)
        ]
        self._tools["other123"] = [{"name": "search"}, {"name": "files_list"}]


def _install_fork_mcp(monkeypatch, **kwargs):
    import src.tool_utils as tool_utils

    fake = _FakeForkMCP(**kwargs)
    monkeypatch.setattr(tool_utils, "get_mcp_manager", lambda: fake)
    monkeypatch.setattr(tool_execution, "get_mcp_manager", lambda: fake)
    monkeypatch.delenv(guard.GDRIVE_SERVER_ENV, raising=False)
    # The dispatcher's own admin check, pointed at the same test admin.
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: owner == "ashton")
    return fake


def _run(tool, args, owner="ashton", text="list my google drive files", **kwargs):
    authority, _ = _authority_grants(text, owner)
    return asyncio.run(tool_execution.execute_tool_block(
        ToolBlock(tool, json.dumps(args)),
        session_id="session-1",
        owner=owner,
        security_context=kwargs.pop("security_context", tool_execution.NO_TOOL_SECURITY_CONTEXT),
        request_authority=kwargs.pop("request_authority", authority),
        **kwargs,
    ))


def test_drive_tool_lists_are_exact_and_disjoint():
    assert guard.GDRIVE_READ_TOOLS.isdisjoint(guard.GDRIVE_WRITE_TOOLS)
    assert len(guard.GDRIVE_READ_TOOLS) == 9 and len(guard.GDRIVE_WRITE_TOOLS) == 14
    assert not any("*" in name for name in guard.GDRIVE_READ_TOOLS | guard.GDRIVE_WRITE_TOOLS)


def test_request_authority_admits_named_drive_tools_for_admin_only(tmp_path, monkeypatch):
    _build_ckb(tmp_path, monkeypatch)
    _install_fork_mcp(monkeypatch, extra_drive_tools=("file_nuke",))

    _, admin = _authority_grants("list my google drive files", "ashton")
    _, regular = _authority_grants("list my google drive files", "regular-user")

    assert f"mcp__{_DRIVE_ID}__files_list" in admin
    assert f"mcp__{_DRIVE_ID}__file_delete" in admin
    # A tool the server adds later is not on the reviewed list.
    assert f"mcp__{_DRIVE_ID}__file_nuke" not in admin
    # Every other add-on MCP server keeps upstream's block.
    assert not {name for name in admin if name.startswith("mcp__other123__")}
    assert not {name for name in regular if name.startswith(f"mcp__{_DRIVE_ID}__")}


def test_drive_server_comes_from_env_when_set(tmp_path, monkeypatch):
    _build_ckb(tmp_path, monkeypatch)
    _install_fork_mcp(monkeypatch)
    monkeypatch.setenv(guard.GDRIVE_SERVER_ENV, "other123")

    _, grants = _authority_grants("list my google drive files", "ashton")

    assert "mcp__other123__files_list" in grants
    assert "mcp__other123__search" not in grants
    assert f"mcp__{_DRIVE_ID}__files_list" not in grants


def test_admin_drive_read_runs_without_a_card(tmp_path, monkeypatch):
    _build_ckb(tmp_path, monkeypatch)
    fake = _install_fork_mcp(monkeypatch)

    desc, result = _run(f"mcp__{_DRIVE_ID}__files_list", {"q": "name contains 'budget'"})

    assert result.get("exit_code") == 0, result
    assert fake.calls == [(f"mcp__{_DRIVE_ID}__files_list", {"q": "name contains 'budget'"})]


def test_non_admin_cannot_use_drive(tmp_path, monkeypatch):
    _build_ckb(tmp_path, monkeypatch)
    fake = _install_fork_mcp(monkeypatch)

    _, result = _run(f"mcp__{_DRIVE_ID}__files_list", {"q": "x"}, owner="regular-user")

    assert result.get("exit_code") == 1 and fake.calls == []


def test_other_add_on_mcp_server_is_still_blocked(tmp_path, monkeypatch):
    _build_ckb(tmp_path, monkeypatch)
    fake = _install_fork_mcp(monkeypatch)

    desc, result = _run("mcp__other123__search", {"q": "x"}, text="use mcp__other123__search")

    assert result.get("blocked") is True and fake.calls == []


def test_drive_write_needs_card_and_one_approval_covers_one_call(tmp_path, monkeypatch):
    _build_ckb(tmp_path, monkeypatch)
    fake = _install_fork_mcp(monkeypatch)
    tool = f"mcp__{_DRIVE_ID}__file_delete"

    _, first = _run(tool, {"fileId": "abc"})
    assert "ask_user" in first and "Google Drive" in first["ask_user"]["question"]
    assert fake.calls == []
    assert guard.process_approval_response("Approve once", "session-1", "ashton")["decision"] == "approved"
    _, other = _run(tool, {"fileId": "different"})
    assert "ask_user" in other and fake.calls == []
    assert guard.process_approval_response("Approve once", "session-1", "ashton")["decision"] == "approved"
    _, second = _run(tool, {"fileId": "different"})
    assert second.get("exit_code") == 0 and len(fake.calls) == 1
    _, third = _run(tool, {"fileId": "different"})
    assert "ask_user" in third and len(fake.calls) == 1
