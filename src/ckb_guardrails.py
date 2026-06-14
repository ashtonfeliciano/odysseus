"""CKB-specific preflight and GBrain approval controls.

This module is intentionally isolated so upstream Odysseus updates can be
merged while keeping the local policy easy to test and re-apply.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


_DEFAULT_CKB_ROOT = Path(r"C:\CKB") if os.name == "nt" else Path("/root/CKB")
CKB_ROOT = Path(os.getenv("CKB_ROOT", str(_DEFAULT_CKB_ROOT)))
APPROVAL_TTL_SECONDS = 30 * 60

APPROVAL_GATED_GBRAIN_TOOLS = frozenset({
    "delete_page",
    "purge_deleted_pages",
    "remove_tag",
    "remove_link",
    "revert_version",
    "sync_brain",
    "put_raw_data",
    "file_upload",
    "submit_job",
    "submit_agent",
    "cancel_job",
    "retry_job",
    "pause_job",
    "resume_job",
    "replay_job",
    "send_job_message",
    "sources_add",
    "sources_remove",
    "forget_fact",
    "code_traversal_cache_clear",
    "schema_apply_mutations",
    "run_onboard",
    "run_skillopt",
})

_TOOL_IMPACTS = {
    "delete_page": "Marks or removes a knowledge page.",
    "purge_deleted_pages": "Permanently purges previously deleted pages.",
    "remove_tag": "Removes a tag relationship from stored knowledge.",
    "remove_link": "Removes a link relationship from stored knowledge.",
    "revert_version": "Replaces the current page state with an older version.",
    "sync_brain": "Synchronizes sources and may change or remove indexed state.",
    "put_raw_data": "Writes raw data into GBrain.",
    "file_upload": "Uploads and ingests a file into GBrain.",
    "submit_job": "Starts a background job that may make changes.",
    "submit_agent": "Starts an autonomous agent job.",
    "cancel_job": "Stops an active background job.",
    "retry_job": "Re-runs a prior background job.",
    "pause_job": "Pauses an active background job.",
    "resume_job": "Resumes a paused background job.",
    "replay_job": "Replays a prior background job.",
    "send_job_message": "Changes instructions for a running job.",
    "sources_add": "Adds a source and may start indexing.",
    "sources_remove": "Removes a configured source and may affect indexed data.",
    "forget_fact": "Removes a stored fact.",
    "code_traversal_cache_clear": "Clears cached code traversal state.",
    "schema_apply_mutations": "Changes GBrain's data schema.",
    "run_onboard": "Runs onboarding actions that may change configuration or data.",
    "run_skillopt": "Runs skill optimization that may rewrite generated skill state.",
}

CKB_POLICY_PROMPT = """
## CKB operating contract
- Read and follow the injected `GLOBAL_AI_ROUTER.md` contract on every turn.
- Before non-trivial CKB work, call `ckb_preflight` with the task and target paths.
- Treat the returned CKB instructions and Lessons Learned excerpts as binding.
- Use direct, concise language. Remove filler, generic praise, AI-writing patterns, and repeated conclusions.
- You may read all task-relevant CKB knowledge, including private content, but never print secrets or unnecessary PII.
- Do not index runtime state, caches, nested repositories, credential stores, auth files, or secret-bearing `.env` files.
- Installed MCP tools may run unattended except the approval-gated GBrain tools.
- Approval-gated GBrain calls require exact, one-time user approval. Never substitute different arguments after approval.
- Never perform a destructive CKB action autonomously. Stop when the guardrail requests approval.
- Searching for, downloading, installing, registering, removing, updating, or reconfiguring MCP servers requires user approval.
""".strip()

_STATE_LOCK = threading.RLock()
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.+-]{2,}", re.IGNORECASE)
_STOPWORDS = {
    "about", "after", "again", "also", "and", "before", "could", "from",
    "have", "into", "must", "need", "only", "other", "should", "that", "the",
    "their", "then", "these", "this", "through", "user", "with", "work",
}
_SENSITIVE_KEY_PARTS = {
    "api_key", "apikey", "auth", "bearer", "credential", "password",
    "secret", "token",
}


def _state_path() -> Path:
    from src.constants import DATA_DIR

    return Path(DATA_DIR) / "ckb_guardrails.json"


def _empty_state() -> Dict[str, Dict[str, Any]]:
    return {"pending": {}, "approved": {}, "preflight": {}}


def _load_state() -> Dict[str, Dict[str, Any]]:
    path = _state_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty_state()
    state = _empty_state()
    for key in state:
        if isinstance(raw.get(key), dict):
            state[key] = raw[key]
    return state


def _save_state(state: Dict[str, Dict[str, Any]]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _scope_key(session_id: Optional[str], owner: Optional[str]) -> Optional[str]:
    if not session_id:
        return None
    return f"{owner or 'local'}::{session_id}"


def _cleanup_expired(state: Dict[str, Dict[str, Any]]) -> None:
    now = time.time()
    for bucket in ("pending", "approved"):
        expired = [
            key for key, value in state[bucket].items()
            if float(value.get("expires_at", 0)) <= now
        ]
        for key in expired:
            state[bucket].pop(key, None)


def _gbrain_tool_name(qualified_tool: str) -> Optional[str]:
    if not qualified_tool.startswith("mcp__"):
        return None
    parts = qualified_tool.split("__", 2)
    if len(parts) != 3:
        return None
    server_id, tool_name = parts[1], parts[2]
    if server_id != "gbrain01":
        return None
    return tool_name


def _call_hash(qualified_tool: str, args: Dict[str, Any]) -> str:
    canonical = json.dumps(
        {"tool": qualified_tool, "args": args},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _redact(value: Any, parent_key: str = "") -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS)
                else _redact(item, str(key))
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, parent_key) for item in value]
    return value


def get_policy_prompt() -> str:
    router_path = CKB_ROOT / "GLOBAL_AI_ROUTER.md"
    try:
        router = router_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return (
            CKB_POLICY_PROMPT
            + "\n- Required router unavailable. Stop before non-trivial CKB work "
            + f"and report the missing file: {router_path}"
        )
    return CKB_POLICY_PROMPT + "\n\n## Injected global AI router\n" + router


def process_approval_response(
    user_text: str,
    session_id: Optional[str],
    owner: Optional[str],
) -> Dict[str, Any]:
    """Convert a pending card response into one exact approved or denied call."""
    scope = _scope_key(session_id, owner)
    if not scope:
        return {}
    answer = (user_text or "").strip().casefold()
    approve = answer in {"approve once", "approve", "approved", "yes"}
    deny = answer in {"deny", "denied", "no", "cancel"}
    if not approve and not deny:
        return {}

    with _STATE_LOCK:
        state = _load_state()
        _cleanup_expired(state)
        pending = state["pending"].pop(scope, None)
        if not pending:
            _save_state(state)
            return {}
        if deny:
            _save_state(state)
            return {
                "decision": "denied",
                "prompt": (
                    f"The user denied the pending `{pending['qualified_tool']}` call. "
                    "Do not execute it. Continue only with non-destructive alternatives."
                ),
            }
        pending["approved_at"] = time.time()
        pending["expires_at"] = time.time() + APPROVAL_TTL_SECONDS
        state["approved"][scope] = pending
        _save_state(state)

    return {
        "decision": "approved",
        "qualified_tool": pending["qualified_tool"],
        "prompt": (
            "The user approved exactly one execution of this pending GBrain call. "
            "Call it now with the exact tool name and arguments below. The approval "
            "expires and is consumed after one matching execution. Any changed call "
            "requires new approval.\n"
            f"Tool: {pending['qualified_tool']}\n"
            f"Arguments: {json.dumps(pending['args'], sort_keys=True, ensure_ascii=True)}"
        ),
    }


def check_gbrain_call(
    qualified_tool: str,
    args: Dict[str, Any],
    session_id: Optional[str],
    owner: Optional[str],
) -> Dict[str, Any]:
    """Allow a safe call, consume exact approval, or return an approval card."""
    tool_name = _gbrain_tool_name(qualified_tool)
    if tool_name not in APPROVAL_GATED_GBRAIN_TOOLS:
        return {"allowed": True}

    scope = _scope_key(session_id, owner)
    if not scope:
        return {
            "allowed": False,
            "result": {
                "error": "This GBrain tool needs a chat session for exact one-time approval.",
                "exit_code": 1,
            },
        }

    call_hash = _call_hash(qualified_tool, args)
    with _STATE_LOCK:
        state = _load_state()
        _cleanup_expired(state)
        approved = state["approved"].get(scope)
        if approved and approved.get("call_hash") == call_hash:
            state["approved"].pop(scope, None)
            _save_state(state)
            return {"allowed": True, "approval_consumed": True}

        if scope not in state["preflight"]:
            _save_state(state)
            return {
                "allowed": False,
                "result": {
                    "error": (
                        "CKB preflight has not been completed for this chat. "
                        "Call `ckb_preflight` before requesting a CKB-changing GBrain action."
                    ),
                    "exit_code": 1,
                },
            }

        pending = {
            "qualified_tool": qualified_tool,
            "tool_name": tool_name,
            "args": args,
            "call_hash": call_hash,
            "created_at": time.time(),
            "expires_at": time.time() + APPROVAL_TTL_SECONDS,
        }
        state["pending"][scope] = pending
        state["approved"].pop(scope, None)
        _save_state(state)

    shown_args = json.dumps(_redact(args), indent=2, sort_keys=True, ensure_ascii=True)
    impact = _TOOL_IMPACTS.get(tool_name, "This call can change GBrain or CKB state.")
    question = (
        f"Approve this exact GBrain call once?\n\n"
        f"Tool: `{tool_name}`\n"
        f"Arguments:\n```json\n{shown_args}\n```\n"
        f"Impact: {impact}\n"
        "Risk: the call may change stored knowledge, configuration, or a running job. "
        "Rollback depends on GBrain version history or backups. "
        "Approve once runs only this exact call; Deny leaves it unexecuted."
    )
    return {
        "allowed": False,
        "result": {
            "ask_user": {
                "question": question,
                "options": [
                    {"label": "Approve once", "description": "Run only this exact tool call."},
                    {"label": "Deny", "description": "Do not run the call."},
                ],
                "multi": False,
            },
            "output": f"Awaiting exact one-time approval for `{tool_name}`.",
            "exit_code": 0,
        },
    }


def _resolve_ckb_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = CKB_ROOT / path
    resolved = path.resolve(strict=False)
    root = CKB_ROOT.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Target path is outside CKB: {raw_path}") from exc
    return resolved


def _agent_chain(targets: Iterable[Path]) -> list[Path]:
    root = CKB_ROOT.resolve(strict=False)
    found = {root / "AGENTS.md"}
    for target in targets:
        current = target if target.is_dir() else target.parent
        while True:
            candidate = current / "AGENTS.md"
            if candidate.is_file():
                found.add(candidate)
            if current == root:
                break
            try:
                current.relative_to(root)
            except ValueError:
                break
            current = current.parent
    return sorted(found, key=lambda item: (len(item.parts), str(item).casefold()))


def _tokens(text: str) -> set[str]:
    return {
        token.casefold()
        for token in _TOKEN_RE.findall(text or "")
        if token.casefold() not in _STOPWORDS
    }


def _matching_sections(text: str, wanted: set[str], limit: int = 8000) -> str:
    sections = re.split(r"(?=^#{1,3}\s)", text, flags=re.MULTILINE)
    matched = []
    for section in sections:
        lowered = section.casefold()
        score = sum(1 for token in wanted if token in lowered)
        if score:
            matched.append((score, section.strip()))
    matched.sort(key=lambda item: item[0], reverse=True)
    output = []
    size = 0
    for _, section in matched:
        if not section:
            continue
        remaining = limit - size
        if remaining <= 0:
            break
        output.append(section[:remaining])
        size += min(len(section), remaining)
    return "\n\n".join(output)


def run_ckb_preflight(
    content: str,
    session_id: Optional[str],
    owner: Optional[str],
) -> Dict[str, Any]:
    """Read the CKB root contract, index, DOX chain, and relevant lessons."""
    try:
        request = json.loads(content or "{}")
    except (json.JSONDecodeError, TypeError):
        request = {}
    if not isinstance(request, dict):
        request = {}
    task = str(request.get("task") or "").strip()
    raw_targets = request.get("target_paths") or [str(CKB_ROOT)]
    if isinstance(raw_targets, str):
        raw_targets = [raw_targets]
    try:
        targets = [_resolve_ckb_path(str(path)) for path in raw_targets[:20]]
    except ValueError as exc:
        return {"error": str(exc), "exit_code": 1}

    required = [
        CKB_ROOT / "GLOBAL_AI_ROUTER.md",
        CKB_ROOT / "index.md",
        *_agent_chain(targets),
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        return {
            "error": "Required CKB preflight file unavailable: " + ", ".join(missing),
            "exit_code": 1,
        }

    blocks = []
    loaded = []
    for path in required:
        text = path.read_text(encoding="utf-8", errors="replace")
        loaded.append(str(path))
        blocks.append(f"## FILE: {path}\n{text}")

    lessons_root = CKB_ROOT / "📦 Lessons_learned"
    lesson_files = sorted(lessons_root.rglob("*.md")) if lessons_root.is_dir() else []
    wanted = _tokens(task + " " + " ".join(str(path) for path in targets))
    wanted.update({"approval", "ckb", "gbrain", "mcp", "odysseus", "preflight"})
    selected_lessons = []
    lesson_budget = 24000
    for path in lesson_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        excerpts = _matching_sections(text, wanted, limit=min(8000, lesson_budget))
        if excerpts:
            selected_lessons.append(str(path))
            blocks.append(f"## RELEVANT LESSONS: {path}\n{excerpts}")
            lesson_budget -= len(excerpts)
            if lesson_budget <= 0:
                break

    scope = _scope_key(session_id, owner)
    if scope:
        with _STATE_LOCK:
            state = _load_state()
            state["preflight"][scope] = {
                "completed_at": time.time(),
                "task": task,
                "target_paths": [str(path) for path in targets],
                "loaded_files": loaded + selected_lessons,
            }
            _save_state(state)

    summary = (
        f"CKB preflight complete. Read {len(required)} required files and scanned "
        f"{len(lesson_files)} Lessons Learned files; {len(selected_lessons)} supplied "
        f"relevant sections. Targets: {', '.join(str(path) for path in targets)}."
    )
    return {
        "output": summary + "\n\n" + "\n\n".join(blocks),
        "exit_code": 0,
        "preflight": {
            "required_files": loaded,
            "lesson_files_scanned": len(lesson_files),
            "relevant_lesson_files": selected_lessons,
            "target_paths": [str(path) for path in targets],
        },
    }
