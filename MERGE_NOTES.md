# Merge notes: upstream catch-up

## Upstream commit merged

- Upstream: `pewdiepie-archdaemon/odysseus`, default branch `dev`
- Commit: `c21980f5a6d1cb84db8c8ab0f488cefc7764eb3f` (2026-11-03, "fix(auth): revoke API tokens when an admin is demoted")
- Shared base: upstream `b6158f37` (2026-06-09). The fork's history was a re-created copy of upstream with no shared commit IDs, so git found no merge base. Fork commit `7690860a` has the exact same tree as upstream `b6158f37`. The merge commit records upstream `dev` as its second parent, so future merges have a real base.

## Conflicts and how each was settled

| File | What clashed | How it was settled |
|---|---|---|
| `README.md` | Fork note vs. upstream's new wordmark header | Upstream wordmark first, then the fork note, then upstream's README. Upstream's README test needs the wordmark in the first 15 lines. |
| `requirements-optional.txt` | Fork added `playwright`; upstream bumped `markitdown` to 0.1.6 and added `psd-tools` | Kept all three. |
| `src/agent_tools.py` | Upstream deleted it (moved to the `src/agent_tools/` package). The fork had added `ckb_preflight` to `TOOL_TAGS`. | Took the delete. Added `ckb_preflight` to `TOOL_TAGS` in `src/tool_types.py`, where the set lives now. |
| `src/tool_security.py` | Upstream added `BUILTIN_EMAIL_TOOLS`, `get_workspace`, `host_shell`, `manage_bg_jobs`. The fork had unblocked own-account tools and added the GBrain MCP pass-through. | Took every upstream block. **Kept fork behavior:** `manage_memory`, `manage_documents`, `manage_settings` and `manage_tasks` stay allowed for non-admins. `mcp__gbrain01__*` still passes to `ckb_guardrails`. All other MCP tools stay blocked. |
| `src/tool_index.py` | Fork's `ckb_preflight` description vs. upstream's new `ui_control` text | Kept both. `ckb_preflight` stays in `ALWAYS_AVAILABLE`. |
| `src/tool_execution.py` | Upstream rewrote the dispatcher with request-authority, resource-identity and journal gates, and moved `update_plan` into the handler registry. | Took upstream's flow. Re-added `ckb_preflight` as a dispatch branch. Re-added `check_gbrain_call` in the MCP branch, after argument parsing and before `mcp.call_tool`. Upstream's authority gates still run first, so the guard is one more check, not a replacement. |
| `src/agent_loop.py` (5 hunks) | Tool prompt text, datetime prompt, approval resume, relevant-tool set, schema building | Took upstream's text and kept the `ckb_preflight` prompt line. Kept the CKB policy-prompt injection. Kept the approval-response handling before intent classification. The approved GBrain tool is added to the relevant tools after upstream's ambiguous-turn clamp, so "Approve once" still works. Upstream moved schema building into `_tool_schemas_for_route`. That helper filtered MCP schemas by RAG relevance again. **Kept fork behavior:** full MCP schemas are always passed, so GBrain tools are never filtered out. |

Fork additions that merged with no conflict: `src/ckb_guardrails.py`, `docker-compose.vps.yml`, `gdrive-mcp-custom/`, `src/tool_schemas.py` (`ckb_preflight` schema), `src/action_intents.py` (GBrain routing patterns), `src/mcp_oauth.py` (900 s wait), `Dockerfile` (`PLAYWRIGHT_BROWSERS_PATH`), `docker-compose.yml` (`SEARXNG_HOST_PORT`).

Google Calendar sync: no fork-only files exist for it. It uses upstream's CalDAV integration, which came through the merge unchanged.

## Test changes

- `tests/test_ckb_guardrails.py`
  - The fixture now names `ashton` as admin. Upstream now treats the pre-setup window as non-admin, so the old tests failed at the admin check. The guard itself is unchanged.
  - The dispatcher test now passes upstream's required `security_context` and `request_authority`. Its fake MCP manager now reports a resource identity, as upstream's own fakes do.
  - Added 9 small tests for rules that had none: deny path, session required, approval expiry, approval per session, path outside CKB, missing required file, missing router, public blocklist split, and `ckb_preflight` wiring.
- `tests/test_review_regressions.py`: one upstream assertion said `manage_tasks` must be blocked for non-admins. It now asserts the fork's policy that the four own-account tools are not blocked. This follows the fork rule, not a new weakening; upstream has blocked `manage_tasks` since v1.0.

- `tests/test_user_time.py`: two upstream tests check that the system prompt is exactly the base prompt. The fork always prepends the CKB policy prompt. Both tests now stub that prompt out so they still check upstream's assembly. The fork prompt only changes when the CKB files change, so upstream's goal of a stable cached prompt prefix still holds.

## Fixes for new upstream checks

- `src/tool_capabilities.py`: upstream now requires every tool to have a capability class. `ckb_preflight` is registered as a private read with untrusted output. That is the stricter of the two classes that fit.
- `docker-compose.vps.yml`: upstream now requires a default for every compose variable. `OAUTH_REDIRECT_BASE_URL` and `APP_PUBLIC_URL` now default to empty, which is what compose already did when they were unset.
- `src/agent_loop.py`: the CKB policy prompt is only prepended when it is not empty. In real use it is never empty.

## Other changes

- `.gitignore` and `.dockerignore` now ignore `.env.gdrive`. `docker-compose.vps.yml` reads Google Drive OAuth settings from that file, and it was not ignored before. No such file is in the repo.

## Upstream additions the fork may want

- Request-authority and resource-identity gates on every tool call (`src/agent_runtime/`). These now wrap the CKB guard too.
- `BUILTIN_EMAIL_TOOLS`: one source of truth that blocks every email tool for non-admins.
- API tokens are revoked when an admin is demoted, and username and privilege validation got stricter (latest upstream commits).
- New tool package `src/agent_tools/` with a handler registry. A cleaner home for `ckb_preflight` later would be a registry handler, not a branch in `tool_execution.py`.
- New frontend (`frontend/src`), skills (`resources/skills`, `services/skills`), model-provider specs, and 8 new CI workflows in `.github/workflows`.
- `website/setup.md` setup guide.

## Full test suite

Run with Python 3.13 and `mcp` 1.30.0, the pinned `mcp<2` line. The container came with `mcp` 2.3.0, which breaks test collection for the email, memory and RAG MCP servers.

| Tree | Failed | Errors | Passed |
|---|---|---|---|
| Upstream `dev` alone | 80 | 58 | 17482 |
| This branch | 80 | 58 | 17503 |

The failing tests are the same set on both trees. The merge adds no new failure. Most of them are browser and editor UI tests that need a setup this container does not have. They were not touched.
