# Redeploy plan: live Odysseus server → new `main` (2026-11-05)

## Verdict

**Needs two things first, then ready to deploy with this plan.**

1. **Step 0: check the two uncommitted server edits** (10 minutes, read-only). If either edit holds a change that `main` doesn't have and you still want, put it into `main` through a pull request first. If both can be dropped, go ahead.
2. **Google Drive: decide before you deploy.** On the new `main`, the AI can't use the Google Drive tools at all (upstream's new safety gate, see `REVIEW_2026-11-05.md` finding 3). The follow-up pull request with the Drive exception and the one-approval-click change isn't merged yet. **My call: wait for that follow-up to merge, then deploy that `main` with this plan.** That gives one downtime instead of two, and Drive never goes dark. If you'd rather deploy now, Drive stays off in chat until the follow-up lands. Everything else works.

The plan itself has no open questions. It handles four traps that would bite a plain `git pull && docker compose up` (all explained in Part 1):

- **Trap 1:** the new compose file would pull upstream's public image, so your fork's code (CKB guard, GBrain rules) would vanish.
- **Trap 2:** the default port changed from 7000 to 7011. Your reverse proxy would point at nothing.
- **Trap 3:** ChromaDB now keeps its data in a different folder. Your current Chroma data probably isn't in the Docker volume at all, so a container recreate would wipe it.
- **Trap 4:** `docker compose down` would delete the Google Drive container, and with it its saved Google login.

**Expected downtime: about 10–20 minutes.** Book a 45-minute quiet window. See Part 4.

**Live facts, read from the server on 2026-11-05 (read-only). Re-run Step 2 on the day; these can change.**

| Fact | Value |
|---|---|
| App data | bind mount `/opt/odysseus/data` → `/app/data` (the normal case) |
| Ports | app `127.0.0.1:7000`, Chroma `127.0.0.1:8100`, SearXNG `127.0.0.1:8083`, ntfy `127.0.0.1:8091`, Drive MCP `127.0.0.1:3001` |
| Port 8080 | used by a different program, not Odysseus |
| Reverse proxy | LiteSpeed sends the site to `localhost:7000` |
| Chroma | **Case A**: collections sit in the container's `/data`; the volume at `/chroma/chroma` is empty. Step 6's copy is required. |
| `.env` | `APP_PORT=7000` already set; `SECURE_COOKIES=true`; `SEARXNG_HOST_PORT=8083`; `COMPOSE_FILE=docker-compose.yml:docker-compose.vps.yml` (same files as `$DC`) |
| Git | branch `main` at `790bca7`; no `secrets.env` |
| Space | 97 GB free on `/`; `/opt/odysseus/data` is 7.7 GB |

Commits: the server runs `790bca7` (2026-06-18). Today's `main` is `f0c7223`. That's 2,837 commits of upstream work plus the fork merge, and 2,692 changed files. At deploy time, use whatever `main` is then (the Drive follow-up will move it), and write that commit down as `NEW`.

---

## Part 1. What the server needs (diff `790bca7` → `main`)

### 1a. Things that change how the server runs

| What | Before (`790bca7`) | After (`main`) | What you do |
|---|---|---|---|
| **App image** | `build: .`, image named `odysseus-odysseus` | `image: ${ODYSSEUS_IMAGE:-ghcr.io/odysseus-dev/odysseus:latest}` **plus** `build: .` | **Trap 1.** Compose would pull upstream's public image, which has none of your fork code. Set `ODYSSEUS_IMAGE=odysseus-fork:<NEW>` in `.env` and build that image yourself (Step 4). Never run `docker compose pull` without naming services. |
| **App port** | `${APP_PORT:-7000}` | `${APP_PORT:-7011}` | **Trap 2.** If `.env` has no `APP_PORT`, the host port moves to 7011 and the reverse proxy breaks. Add `APP_PORT=7000` (Step 7). |
| **ChromaDB** | `chromadb/chroma:latest`, volume at `/chroma/chroma` | `chroma:1.5.9` (pinned digest), volume at **`/data`** | **Trap 3.** Chroma 1.x images save to `/data`. The old mount at `/chroma/chroma` catches nothing on a 1.x image. If your `latest` pulled was 1.x (likely in June 2026), your Chroma data sits in the container's own layer, not the volume, and dies on recreate. Step 2 checks which case you're in. Step 6 moves the data into the volume. |
| **SearXNG** | `2026.5.31-7159b8aed` | `2026.10.24-ef0ec03da` (pinned digest) | Runs a new settings-migration script at boot (`scripts/migrate_searxng_settings.py`, mounted read-only). Nothing to do. Back up its volume anyway. |
| **ntfy** | `binwiederhier/ntfy` (latest) | `v2.32.0` (pinned digest) | Nothing to do. |
| **Google Drive MCP** (`gdrive-mcp`) | in `docker-compose.vps.yml` | **unchanged** (same build, port `127.0.0.1:3001`) | **Trap 4.** Don't run `docker compose down`. Only recreate the services that changed. Its Google login lives inside the container. Back it up anyway (Step 3e). |
| **App base image** | `python:3.12-slim` | `python:3.14.9-slim`, multi-stage; adds Chromium, Noto CJK fonts, network tools, Docker CLI, `agent-browser` | Much bigger image (expect 3–5 GB). The first build is slow (20–40 min). Build it **before** the quiet window, while the old app still runs. Needs about 15 GB free under `/var/lib/docker`. |
| **New services** | — | none added to the default stack | The `docker/host-docker.yml`, `docker/host-network.yml` and `docker/host-workspace.yml` overlays are opt-in. Don't use them. |
| **New volumes** | `searxng-data`, `chromadb-data`, `ntfy-cache`, `npx-cache` | same names | No new volumes. Only the Chroma mount *path* changed. |
| **Data folders** | `./data`, `./logs` hard-coded | `${APP_DATA_DIR:-./data}`, `${APP_LOGS_DIR:-./logs}` | Leave them unset and they stay `./data` and `./logs`. If your app data really is in a named Docker volume (see Step 2), set `APP_DATA_DIR` to that volume's host path. |
| **Health endpoints** | `/api/health`, `/api/ready` | same, plus `/api/version` and `/api/busy` | Used in Part 3 checks. |

### 1b. Environment variables

Upstream now gives every compose variable a default, so **none of the new ones has to be set.** Only three need a decision:

| Variable | Decision for this server |
|---|---|
| `ODYSSEUS_IMAGE` | **Set** to `odysseus-fork:<NEW>` (Trap 1). |
| `APP_PORT` | **Set** to `7000` if it's missing (Trap 2). |
| `SECURE_COOKIES` | The default went from `false` to empty, meaning "Secure on HTTPS, not on HTTP". This server's `.env` sets `SECURE_COOKIES=true` (read 2026-11-05), so nothing changes. Leave it. |

New variables you can leave unset (they default safely): `COMPANION_BASE_URL`, `ODYSSEUS_QWEN_NATIVE_COMPACT_BUILTINS`, `ODYSSEUS_QWEN_SUPPRESS_LOCAL_CONTEXT`, `ODYSSEUS_CAPTURE_MODEL_REQUESTS`, `ODYSSEUS_MCP_EMAIL_OWNER`, `ODYSSEUS_LOCAL_MCP_READ_TOOLS`, `ODYSSEUS_LOCAL_MCP_WRITE_TOOLS` (no help for Drive: it only covers local "stdio" MCP servers, and Drive is a web service), the eight new `*_MAX_BYTES` upload limits, `ODYSSEUS_TTS_CACHE_MAX_BYTES`, `ODYSSEUS_WORKSPACE_HOST_ROOT` / `_CONTAINER_ROOT` / `_DEFAULT`, `ODYSSEUS_FIRST_TOKEN_TIMEOUT`, `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET` / `_REDIRECT_URI` (only for "Connect with Google" email), `ODYSSEUS_TOOL_APPROVAL_GATE` (keep the default, on), `APP_DATA_DIR`, `APP_LOGS_DIR`.

Fork variables: `docker-compose.vps.yml` now writes `OAUTH_REDIRECT_BASE_URL` and `APP_PUBLIC_URL` with empty defaults. Same behavior as before. `CKB_ROOT=/app/ckb` and the `/root/CKB:/app/ckb:ro` mount are unchanged. `.env.gdrive` is now in `.gitignore` and `.dockerignore`, so it can't slip into git or into the image.

No variable was renamed or removed.

### 1c. Database migrations

- **The app database is SQLite** (`DATABASE_URL` default `sqlite:///./data/app.db`). Migrations run **by themselves when the app starts** (`core/database.py`). There's no separate command.
- `main` has 61 migration steps; `790bca7` had 44. The new ones add columns and tables: session generation settings, memory/skill injection flags, model tool modes, cookbook serve sessions, session working folder and endpoint, total cost, gallery captions, calendar source and CalDAV sync columns, recurrence exceptions, email OAuth columns, task authority, note gallery link. Plus three data fixes: one default email account per owner, seeding the email account, and scrubbing old media out of the chat full-text index.
- **They only go forward.** Old code usually ignores extra columns, but the data fixes change rows. **So undo = restore the database backup**, not "run old code on the new database". The undo path in Part 3 does exactly that.
- The memory vector index rebuilds itself from the database if the Chroma memory collection is empty. Log line: `Rebuilt memory vector index from N existing entries`. So a Chroma loss hurts document search (RAG) more than memory.
- The **June SQLite backup is too old to be useful.** Keep it, but don't use it for rollback. Step 3 makes a fresh one.

### 1d. Startup and build checks that are new

- **Build time:** the Dockerfile now checks CA certificates and the Brotli library (`scripts/check_ca_trust.py`, `scripts/check_brotli.py`). If the build fails there, the error says which check. It's a build-host problem (an old cached base image), not a code problem. Fix it with `docker build --pull`.
- **Boot:** the entrypoint repairs file ownership in a smarter way, skipping `data`, `logs`, `.ssh`, `.cache` and `.local`. The first boot after an image change can take a few extra seconds.
- **Boot order:** the app still waits for SearXNG's healthcheck (up to about 110 s) and for Chroma to start.
- **New in the app:** upstream's per-request tool allow-list and approval gate (`src/agent_runtime/`). After a GBrain read, a second GBrain call in the same chat shows an extra approval card. That's expected (review finding 5).

### 1e. Files that moved (matters for the server edits)

- `src/agent_tools.py` → **package `src/agent_tools/`**. The fork's `ckb_preflight` entry in `TOOL_TAGS` now lives in `src/tool_types.py`.
- `update_plan` moved into the handler registry, and the tool dispatcher in `src/tool_execution.py` was rewritten.
- Schema building in `src/agent_loop.py` moved into `_tool_schemas_for_route`.
- `src/ckb_guardrails.py` gained `fork_request_tools` and `fork_contract_tools`, the hooks that let `ckb_preflight` and GBrain through the new gate.
- `docs/` content moved to `website/`.

---

## Part 2. The two uncommitted server edits

The server has hand edits in `src/agent_loop.py` and `src/ckb_guardrails.py` that were never committed. `git checkout` of `main` would refuse or clash, and the new image is built from git, so **these edits won't be in the new app unless they're in `main`.**

### Step 0: read-only check (run any time before the window)

All of these only read, except `git fetch` (updates the remote-tracking ref, never your files) and the throwaway worktree in `/tmp` (removed at the end).

```bash
mkdir -p /root/deploy-backup-2026-11-05 && chmod 700 /root/deploy-backup-2026-11-05
```

```bash
cd /opt/odysseus && git status --short && git rev-parse --abbrev-ref HEAD && git log -1 --oneline
```

```bash
cd /opt/odysseus && git diff 790bca7 -- src/agent_loop.py src/ckb_guardrails.py > /root/deploy-backup-2026-11-05/server-uncommitted.patch && git diff --stat 790bca7 -- src/agent_loop.py src/ckb_guardrails.py
```

```bash
cd /opt/odysseus && git fetch origin main
```

Is each edit already in `main`? Test on a throwaway copy of `main`:

```bash
cd /opt/odysseus && git worktree add --detach /tmp/ody-main-check origin/main
```

```bash
cd /tmp/ody-main-check && git apply --check -v /root/deploy-backup-2026-11-05/server-uncommitted.patch; echo "forward exit=$?"
```

```bash
cd /tmp/ody-main-check && git apply --check -R -v /root/deploy-backup-2026-11-05/server-uncommitted.patch; echo "reverse exit=$?"
```

Read each edit side by side with `main`:

```bash
cd /opt/odysseus && git diff origin/main -- src/ckb_guardrails.py
```

```bash
cd /opt/odysseus && git diff 790bca7 -- src/agent_loop.py
```

For any line in an `agent_loop.py` hunk, check whether `main` already has it:

```bash
cd /opt/odysseus && git grep -n -F 'PASTE A DISTINCTIVE LINE FROM THE HUNK' origin/main -- src/
```

Clean up the throwaway copy:

```bash
cd /opt/odysseus && git worktree remove /tmp/ody-main-check
```

### The rule: keep or drop, hunk by hunk

Go through every hunk (block of changes) in `server-uncommitted.patch`:

1. **Reverse check passes, or `git grep` finds the line in `main`** → it's **already in `main`. Drop it.**
2. **It's debugging** (print or log lines, a timeout bumped while testing, commented-out code) → **drop it.**
3. **It works around something the merge now handles.** Examples: letting `ckb_preflight` or `mcp__gbrain01__*` through, approval resume, keeping MCP schemas unfiltered, anything that used to live in `src/agent_tools.py`. `main` does this already through `fork_request_tools`, `fork_contract_tools`, `src/tool_types.py` and `_tool_schemas_for_route` → **drop it.**
4. **It's a real behavior change you still want, and `main` doesn't have it** → **keep it, but only through git:** a branch, a commit, a pull request into `main`, tests run (`python3 -m pytest tests/test_ckb_guardrails.py -q`), merged, **then** deploy. Never hand-edit files on the server again; the next deploy would wipe them silently.
5. **Not sure what a hunk does** → treat it as rule 4. It costs one small PR, not an outage.

Default if nobody can say what the edits were for: **drop both.** The patch file stays in the backup folder, so nothing is lost. The *running* old image keeps them for rollback (Step 3d).

`*.pre-c4` and `*.bak.*` files: untracked leftovers. They don't block the checkout. Step 3 copies them into the backup folder. Leave them in place.

---

## Part 3. The plan

Notation used below:

```bash
cd /opt/odysseus && git fetch origin main
BK=/root/deploy-backup-2026-11-05
DC="docker compose -f docker-compose.yml -f docker-compose.vps.yml"
NEW=$(git rev-parse --short origin/main)   # write this down; today it is f0c7223
```

`BK`, `DC` and `NEW` are shell variables. Re-run these lines in every new terminal.

### Before the window (no downtime, the old app keeps running)

**Step 1. Space and versions.**

```bash
df -h /var/lib/docker /opt /root && docker compose version && du -sh /opt/odysseus/data /opt/odysseus/data/* 2>/dev/null | sort -h | tail
```

Need ≥15 GB free for Docker, and room in `/root` for the data backup (the `data` size minus `huggingface` and `local`).

**Step 2. Find where the data really lives.** Write down the answers.

```bash
docker inspect odysseus-odysseus-1 --format '{{range .Mounts}}{{.Type}} {{.Name}} {{.Source}} -> {{.Destination}}{{println}}{{end}}'
```

- The line ending in `-> /app/data` is the app data. If its type is `bind` and the source is `/opt/odysseus/data`, that's the normal case, and the commands below use it as written. If its type is `volume`, use that volume's `Source` path wherever you see `/opt/odysseus/data`, and in Step 7 also set `APP_DATA_DIR=<that Source path>` (and `APP_LOGS_DIR` if logs are in a volume too).

```bash
docker port odysseus-odysseus-1
```

This shows the live host port (expected `127.0.0.1:7000`).

```bash
docker volume ls | grep odysseus
```

Expect `odysseus_chromadb-data`, `odysseus_searxng-data`, `odysseus_ntfy-cache`, `odysseus_npx-cache`.

**Which Chroma case are you in?**

```bash
docker image inspect $(docker inspect odysseus-chromadb-1 --format '{{.Image}}') --format '{{.Created}} {{index .Config.Labels "org.opencontainers.image.version"}}'
```

```bash
docker cp odysseus-chromadb-1:/data - 2>/dev/null | tar -tv | head -5; echo ---; docker cp odysseus-chromadb-1:/chroma/chroma - 2>/dev/null | tar -tv | head -5
```

The version label may print empty (it did on 2026-11-05); the two listings below decide the case.

- **Case A:** `/data` lists `data/chroma.sqlite3`. The live data is in the container layer. Step 6 copies it into the volume.
- **Case B:** only `/chroma/chroma` lists `chroma/chroma.sqlite3`. The data is already in the volume and becomes `/data` automatically. No copy needed.
- **Neither:** Chroma is empty. Nothing to save.

Count collections now, so you can compare later:

```bash
curl -s http://127.0.0.1:8100/api/v2/tenants/default_tenant/databases/default_database/collections_count; echo; curl -s http://127.0.0.1:8100/api/v1/collections | head -c 400; echo
```

One of the two answers (v2 on 1.x, v1 on older). Write down the number or the collection names.

**Was playwright built in?** (decides one build flag)

```bash
docker run --rm --entrypoint pip $(docker inspect odysseus-odysseus-1 --format '{{.Image}}') show playwright >/dev/null 2>&1 && echo "OPTIONAL=true" || echo "OPTIONAL=false"
```

**Step 3 (part 1). Tag the current images**, so the undo path has them even after prune or pull:

```bash
docker tag $(docker inspect odysseus-odysseus-1 --format '{{.Image}}') odysseus-rollback:790bca7
docker tag $(docker inspect odysseus-gdrive-mcp-1 --format '{{.Image}}') gdrive-mcp-rollback:790bca7
docker tag $(docker inspect odysseus-chromadb-1 --format '{{.Image}}') chroma-rollback:pre-deploy
docker tag $(docker inspect odysseus-ntfy-1 --format '{{.Image}}') ntfy-rollback:pre-deploy
docker tag $(docker inspect odysseus-searxng-1 --format '{{.Image}}') searxng-rollback:pre-deploy
```

Check: the next command lists five images.

```bash
docker images | grep -E 'rollback'
```

**Step 4. Build the new app image off to the side** (20–40 min, no downtime):

```bash
cd /opt/odysseus && git worktree add --detach /opt/odysseus-build $NEW
```

```bash
docker build --pull -t odysseus-fork:$NEW --build-arg INSTALL_OPTIONAL=false /opt/odysseus-build
```

If Step 2 printed `OPTIONAL=true`, use `INSTALL_OPTIONAL=true`. Success ends with `naming to docker.io/library/odysseus-fork:<NEW>`. Then check the fork code is in it:

```bash
docker run --rm --entrypoint python odysseus-fork:$NEW -c "import src.ckb_guardrails as g; print('fork ok', hasattr(g, 'fork_request_tools'))"
```

Expect `fork ok True`.

**Step 5. Pre-pull the pinned helper images** (no downtime):

```bash
docker pull docker.io/chromadb/chroma:1.5.9@sha256:1e0b73a187a28757c572acba508c46f48c9e8b0acaf5c20e6d95cdedce1acdf6
docker pull docker.io/searxng/searxng:2026.10.24-ef0ec03da@sha256:7c7cbe509429e82755104b2a95f52085a98633c95d53f44e4e905fa3a59f08c8
docker pull docker.io/binwiederhier/ntfy:v2.32.0@sha256:59250e1d8be2ee2186c4560322f625faef22ace380331f05b63f8842dbd62602
docker pull docker.io/library/alpine:3.20
```

Pulling by digest doesn't move the `latest` tags the old stack uses.

### Quiet window

Pick a time nobody's chatting, no scheduled tasks or mail polling matter, and no Cookbook download or image job is running. Tell users the app will be down for up to 45 minutes. Just before you start, check that nothing heavy is running (old code has no `/api/busy`; look at the logs):

```bash
docker logs --since 5m odysseus-odysseus-1 2>&1 | tail -20
```

### In the window

**Step 3 (part 2). Stop the app and Chroma, then back up.** SearXNG, ntfy and Drive keep running.

```bash
cd /opt/odysseus && $DC stop odysseus chromadb
```

a) **App data** (consistent, because the app is stopped). The caches `huggingface` and `local` are left out: they can be downloaded again and they're huge.

```bash
tar -C /opt/odysseus -czf $BK/app-data.tgz --exclude=data/huggingface --exclude=data/local data && ls -lh $BK/app-data.tgz
```

**Check it opens:**

```bash
mkdir -p $BK/verify && tar -C $BK/verify -xzf $BK/app-data.tgz data/app.db && ls $BK/verify/data
```

```bash
docker run --rm -v $BK/verify:/v:z --entrypoint python odysseus-rollback:790bca7 -c "import sqlite3; c=sqlite3.connect('/v/data/app.db'); print(c.execute('pragma integrity_check').fetchone()[0], 'sessions=', c.execute('select count(*) from sessions').fetchone()[0])"
```

Expect `ok sessions= <a number>`. Anything else: stop here and restart the old app (`$DC start chromadb odysseus`).

b) **Chroma.** Back up both places, whichever case you're in:

```bash
docker cp odysseus-chromadb-1:/data - > $BK/chroma-container-data.tar 2>/dev/null; ls -lh $BK/chroma-container-data.tar
```

```bash
docker run --rm -v odysseus_chromadb-data:/v:ro -v $BK:/b:z alpine:3.20 tar -C /v -czf /b/chroma-volume.tgz . && ls -lh $BK/chroma-volume.tgz
```

**Check it opens** (case A uses the `.tar`, case B the `.tgz`):

```bash
mkdir -p $BK/verify/chroma && tar -C $BK/verify/chroma -xf $BK/chroma-container-data.tar 2>/dev/null; tar -C $BK/verify/chroma -xzf $BK/chroma-volume.tgz; find $BK/verify/chroma -name chroma.sqlite3
```

```bash
for f in $(find $BK/verify/chroma -name chroma.sqlite3); do docker run --rm -v $f:/c.db:z --entrypoint python odysseus-rollback:790bca7 -c "import sqlite3; c=sqlite3.connect('/c.db'); print('$f', c.execute('pragma integrity_check').fetchone()[0], 'collections=', c.execute('select count(*) from collections').fetchone()[0])"; done
```

Expect `ok` and a collection count that matches Step 2 for the case you're in.

c) **.env files and secrets:**

```bash
cd /opt/odysseus && tar -czf $BK/env-files.tgz .env .env.gdrive $(ls secrets.env 2>/dev/null) && chmod 600 $BK/env-files.tgz
```

**Check:**

```bash
tar -xzOf $BK/env-files.tgz .env | diff - /opt/odysseus/.env && tar -xzOf $BK/env-files.tgz .env.gdrive | diff - /opt/odysseus/.env.gdrive && echo "env backup matches"
```

d) **Code state, the edits and the leftovers:**

```bash
cd /opt/odysseus && git rev-parse --abbrev-ref HEAD > $BK/old-branch.txt && cp -p src/agent_loop.py src/ckb_guardrails.py $BK/ && git ls-files -o --exclude-standard | grep -E '\.pre-c4$|\.bak\.' | xargs -r cp --parents -p -t $BK/
```

The old *running* behavior, edits included, is kept in the image tag `odysseus-rollback:790bca7`.

e) **Small volumes and the Drive login:**

```bash
docker run --rm -v odysseus_searxng-data:/v:ro -v $BK:/b:z alpine:3.20 tar -C /v -czf /b/searxng-data.tgz .
docker cp odysseus-gdrive-mcp-1:/root/.config - > $BK/gdrive-config.tar 2>/dev/null; ls -lh $BK/searxng-data.tgz $BK/gdrive-config.tar
```

`gdrive-config.tar` may be tiny or empty if the Drive tokens live elsewhere. That's fine; the container isn't recreated.

**Step 6. Switch the code to `main`.**

```bash
cd /opt/odysseus && git checkout -- src/agent_loop.py src/ckb_guardrails.py && git checkout -B main $NEW && git log -1 --oneline && git status --short
```

Expect `f0c7223` (or your `NEW`), and `git status` showing only the untracked leftovers. `.env.gdrive` is now ignored.

**Chroma data move (Case A only).** This empties the volume (already backed up in `chroma-volume.tgz`) and puts the live data in:

```bash
docker run --rm -v odysseus_chromadb-data:/v -v $BK:/b:ro,z alpine:3.20 sh -c 'find /v -mindepth 1 -delete && tar -C /v -xf /b/chroma-container-data.tar --strip-components=1 && ls -la /v'
```

Expect `chroma.sqlite3` plus folders with long ID names. **Case B:** do nothing. **Neither:** do nothing.

**Step 7. Edit `.env`** (backed up in 3c):

```bash
cd /opt/odysseus && grep -q '^APP_PORT=' .env || echo 'APP_PORT=7000' >> .env
```

```bash
cd /opt/odysseus && sed -i '/^ODYSSEUS_IMAGE=/d' .env && echo "ODYSSEUS_IMAGE=odysseus-fork:$NEW" >> .env
```

```bash
cd /opt/odysseus && $DC config --quiet && echo "compose ok" && $DC config --images
```

Only run `APP_PORT=7000` if Step 2 showed port 7000; otherwise use the port it showed. `config --images` must list `odysseus-fork:<NEW>`, **not** `ghcr.io/odysseus-dev/...`. If it lists ghcr, stop and fix `.env`.

**Step 8. Start.** No `down`, no `pull`, no build:

```bash
cd /opt/odysseus && $DC up -d --no-build odysseus chromadb searxng ntfy
```

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
```

Expect all five containers up. `odysseus-gdrive-mcp-1` should show its **old** uptime (not recreated). `odysseus-searxng-1` should turn `(healthy)` within about 2 minutes.

```bash
cd /opt/odysseus && git worktree remove /opt/odysseus-build
```

### Checks after deploy

Run in order. **Any red item → undo path.**

1. **Health:**

```bash
curl -fsS http://127.0.0.1:7000/api/health; echo
```

Expect `{"status":"healthy",...}`.

2. **Readiness:**

```bash
curl -sS http://127.0.0.1:7000/api/ready; echo
```

Expect `"ready": true` and `"database": {"ok": true}`.

3. **Version:**

```bash
curl -sS http://127.0.0.1:7000/api/version; echo
```

Expect a new version number (not the June one).

4. **Log lines that mean success:**

```bash
docker logs --since 15m odysseus-odysseus-1 2>&1 | grep -E 'Application startup complete|Uvicorn running on|MemoryVectorStore initialized|Rebuilt memory vector index'
```

Expect `Uvicorn running on http://0.0.0.0:7000` and `Application startup complete.`. **Bad signs:**

```bash
docker logs --since 15m odysseus-odysseus-1 2>&1 | grep -E 'Traceback|CRITICAL|OperationalError|database is locked|no such column' | head
```

This should print nothing. A few `WARNING` lines about optional features (TTS, GPU, email not set up) are normal.

5. **Public URL:** open the site through the reverse proxy. The login page loads and you can log in. A login loop means a `SECURE_COOKIES` mismatch (see 1b).

6. **Chroma kept its data:**

```bash
curl -s http://127.0.0.1:8100/api/v2/heartbeat; echo; curl -s http://127.0.0.1:8100/api/v2/tenants/default_tenant/databases/default_database/collections_count; echo
```

The count matches Step 2. Then ask the AI a question about an uploaded document; it finds it.

7. **SearXNG:**

```bash
curl -s 'http://127.0.0.1:8083/search?q=odysseus&format=json' | head -c 200; echo
```

Expect JSON with `"results"`. Use port 8083 (`SEARXNG_HOST_PORT` in `.env`). Port 8080 on this server belongs to another program, so a check on 8080 would test the wrong thing.

8. **ntfy:**

```bash
curl -s http://127.0.0.1:8091/v1/health; echo
```

Expect `{"healthy":true}`.

9. **CKB preflight from the shell** (read-only; `None` session means it writes no state):

```bash
docker exec odysseus-odysseus-1 python -c "from src.ckb_guardrails import run_ckb_preflight as p; r=p('{\"task\":\"deploy check\"}', None, None); print(r.get('error') or r['output'].splitlines()[0])"
```

Expect `CKB preflight complete. Read N required files and scanned M Lessons Learned files; ...`. An error naming `GLOBAL_AI_ROUTER.md` or `index.md` means `/root/CKB` isn't mounted.

10. **CKB preflight in chat** (as admin `ashton`): *"Run ckb_preflight for the task 'deploy check' on /app/ckb."* The tool runs and reports the files it read. If it says *"outside server request authority"*, the fork fix isn't in the image (Step 4 check). Undo.

11. **GBrain search in chat** (as admin, new chat): *"Search gbrain for <a topic you know is in it>."* An `mcp__gbrain01__...` search tool runs and returns real pages. A *second* GBrain search in the same chat shows an extra approval card: that's expected (review finding 5). A refusal or web search instead of GBrain: undo.

12. **Google Drive:** without the follow-up PR, a Drive request returns *"outside server request authority"*. That's expected and **not** a reason to undo. With the follow-up merged and deployed, a Drive search works with one approval click.

13. **Write it down** (Upgrade Run rule): update `C:\CKB\Machines\vps-srv1178016.md` with the new commit, the image name `odysseus-fork:<NEW>`, Chroma's new data path `/data`, and the backup folder. Then run one normal day of use before calling it done.

14. **Clean up after 7 days** of good running (not before):

```bash
docker image rm odysseus-rollback:790bca7 gdrive-mcp-rollback:790bca7 chroma-rollback:pre-deploy ntfy-rollback:pre-deploy searxng-rollback:pre-deploy
```

Keep `$BK` for 30 days, then delete it. It holds `data/ssh`, the app's private SSH keys, so keep the folder `chmod 700` and never copy it off the server.

### Undo path: back to `790bca7`

Use it if any check above is red and a quick fix isn't obvious. **Anything users did after the deploy is lost** (chats, notes and so on since the deploy), because the database goes back to the backup. Time: 10–15 minutes.

```bash
cd /opt/odysseus && BK=/root/deploy-backup-2026-11-05 && DC="docker compose -f docker-compose.yml -f docker-compose.vps.yml"
```

U1. Stop the new services (not Drive):

```bash
$DC stop odysseus chromadb searxng ntfy && $DC rm -f odysseus chromadb searxng ntfy
```

U2. Code back to the old commit, with the old edits:

```bash
git checkout -B $(cat $BK/old-branch.txt) 790bca7 && cp -p $BK/agent_loop.py $BK/ckb_guardrails.py src/ && git log -1 --oneline
```

If `old-branch.txt` says `HEAD` (it was detached), run `git checkout --detach 790bca7` instead.

U3. `.env` back:

```bash
tar -C /opt/odysseus -xzf $BK/env-files.tgz
```

U4. App data back. The new migrations changed rows, so restore, don't reuse. The current data is moved aside first, not deleted:

```bash
mv /opt/odysseus/data /opt/odysseus/data.failed-deploy && tar -C /opt/odysseus -xzf $BK/app-data.tgz && mv /opt/odysseus/data.failed-deploy/huggingface /opt/odysseus/data.failed-deploy/local /opt/odysseus/data/ 2>/dev/null; ls /opt/odysseus/data
```

U5. Old images back under the names the old compose file expects:

```bash
docker tag odysseus-rollback:790bca7 odysseus-odysseus:latest && docker tag chroma-rollback:pre-deploy docker.io/chromadb/chroma:latest && docker tag ntfy-rollback:pre-deploy docker.io/binwiederhier/ntfy:latest
```

The SearXNG pin in the old compose file (`2026.5.31-7159b8aed`) is still on disk.

U6. Chroma back:

- **Case B (or neither):** the volume goes back as it was:

```bash
docker run --rm -v odysseus_chromadb-data:/v -v $BK:/b:ro,z alpine:3.20 sh -c 'find /v -mindepth 1 -delete && tar -C /v -xzf /b/chroma-volume.tgz'
```

- **Case A:** the old setup kept data in the container layer, which is gone. So put the backup in a new volume and mount it where old Chroma looks (`/data`):

```bash
docker volume create odysseus_chromadb-rollback && docker run --rm -v odysseus_chromadb-rollback:/v -v $BK:/b:ro,z alpine:3.20 tar -C /v -xf /b/chroma-container-data.tar --strip-components=1
```

```bash
printf 'services:\n  chromadb:\n    volumes:\n      - chromadb-rollback:/data\nvolumes:\n  chromadb-rollback:\n    external: true\n    name: odysseus_chromadb-rollback\n' > /opt/odysseus/docker-compose.rollback.yml && DC="$DC -f docker-compose.rollback.yml"
```

U7. SearXNG settings back (the new migration may have rewritten them):

```bash
docker run --rm -v odysseus_searxng-data:/v -v $BK:/b:ro,z alpine:3.20 sh -c 'find /v -mindepth 1 -delete && tar -C /v -xzf /b/searxng-data.tgz'
```

U8. Start the old stack:

```bash
$DC up -d --no-build odysseus chromadb searxng ntfy && docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
```

U9. Check:

```bash
curl -fsS http://127.0.0.1:7000/api/health; echo; curl -sS http://127.0.0.1:7000/api/ready; echo
```

Then log in, open a recent chat, and run one GBrain search.

**Walking the undo path in words, step by step:**

- **U1** removes only the four new containers. Drive was never touched, so its login survives.
- **U2** puts the files back exactly as the old app had them, edits included. That matters only for a later rebuild: the image from U5 already has that code baked in.
- **U3** brings back the old `.env`, without `ODYSSEUS_IMAGE` and with the old `APP_PORT` situation, so the reverse proxy sees the same port as before.
- **U4** is the important one. The old code meets the database exactly as it left it, so no "unknown column" or half-migrated rows. The big model caches are carried over instead of re-downloaded. The failed data stays in `data.failed-deploy`, in case someone needs a message from it.
- **U5** makes sure compose finds the *old* images under the old names. The old compose file has no `image:` line for the app, so it looks for `odysseus-odysseus:latest`. `--no-build` in U8 stops it from rebuilding from source, which could pick up a different base image.
- **U6** gives old Chroma its exact old data. In case A it even improves on the old setup: the data now sits in a volume and survives the next recreate.
- **U7** gives the old SearXNG its old settings file.
- **U8 and U9:** the same checks as forward, against the old app. `/api/health` and `/api/ready` exist on `790bca7` too.

**Rehearse it before the window (needs no downtime):** after Steps 3–5, check that the five `*-rollback` tags exist (`docker images | grep rollback`), that `app-data.tgz` passed its `integrity_check`, that `env-files.tgz` matched, and that `git cat-file -t 790bca7` prints `commit` on the server. Those are the four things the undo path depends on. If all four are true, the undo path will work.

---

## Part 4. Downtime estimate

| Phase | Users affected? | Time |
|---|---|---|
| Steps 0–5: edit check, inspect, tag, build, pull | No | 30–60 min (mostly the build) |
| Step 3 part 2: stop, back up, check | **Yes** | 3–8 min (grows with the size of `data/`) |
| Steps 6–7: code switch, Chroma move, `.env` | **Yes** | 1–3 min |
| Step 8: start (SearXNG healthy, then the app runs its migrations and rebuilds the full-text index) | **Yes** | 2–6 min, up to 10 on a big chat history |
| Checks 1–12 | Site is up | 5–10 min |
| **Total downtime** | | **about 10–20 min** |
| If the undo path is needed | **Yes** | +10–15 min |

**Book a 45-minute quiet window.** It covers the worst case, including a full undo.

---

## Summary for the deploy day

1. Step 0 (edits) is done and the Drive follow-up is merged, or you've decided to go without it.
2. Before the window: inspect (Step 2), tag (Step 3 part 1), build `odysseus-fork:<NEW>` (Step 4), pull pins (Step 5), rehearse the undo checks.
3. In the window: stop app and Chroma, back up and check each backup, switch git, move Chroma data (case A), set `APP_PORT` and `ODYSSEUS_IMAGE`, confirm `config --images`, start with `up -d --no-build` (never `down`, never a bare `pull`).
4. Checks: health, ready, logs, public login, Chroma count, SearXNG, ntfy, CKB preflight in shell and chat, GBrain search.
5. Any red item → undo path U1–U9.
