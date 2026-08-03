# Production Deployment Procedure

> **Model A (trunk-based)** — adopted 2026-08-02. Single monorepo, single trunk `main`,
> PROD deploys from immutable release **tags**. Supersedes the old feature/release-branch model.

---

## 1. Branch Strategy (Model A)

| Environment | Source | Purpose |
|---|---|---|
| **Trunk** | `main` (protected) | Single integration line, always deployable. Merged via PR only. |
| QA | `main` | QA services run trunk content; validated before each release |
| PROD | **tag** `vX.Y.Z` | Immutable release snapshot. PROD runs ONE release older than QA. |

**Critical rules:**
- Single monorepo, single trunk (`main`). No per-service release branches.
- Feature work: short-lived `feature/*` branches → PR → merge to `main`. **No direct pushes to `main`.**
- Releases: `git tag -a vX.Y.Z` on `main` → `deploy_prod.sh vX.Y.Z` checks out the tag into `_PROD` dirs.
- `master` kept in sync with `main` (legacy default branch; both point at the same tip).
- PROD is ONLY updated via the formal deploy (tag checkout + launchd reload). **No mid-cycle PROD upgrades.**

**Branch protection (DONE 2026-08-02):** ruleset `Main_branch_rules` (id 20258917), enforcement **active**,
target `refs/heads/main`, `current_user_can_bypass: never`:
- deletion blocked · non-fast-forward (force push) blocked
- pull_request required: 1 approval, dismiss stale reviews on push
- required_status_checks: context `"once ci exists"` (placeholder — update to the real CI check name when Actions is added)

---

## 2. Service Inventory

| Service | QA Dir | PROD Dir | QA Port | PROD Port | Launchd Job (QA) | Launchd Job (PROD) |
|---|---|---|---|---|---|---|
| Alpha Terminal | `Project_Sequoia/QA_terminal` | `Project_Sequoia/terminal` | 9099 | 9098 | `com.alpha.terminal.qa` | `com.alpha.terminal.prod` |
| NS-1 | `NS_1_QA` | `NS-1_PROD` | 9219 | 9218 | `com.ninestreet.ns1.qa` | `com.ninestreet.ns1.prod` |
| NS-2 | `NS-2_QA` | `NS-2_PROD` | 9229 | 9228 | `com.ninestreet.ns2.qa` | `com.ninestreet.ns2.prod` |
| NS-3 | `NS-3_QA` | `NS-3_PROD` | 9237 | 9236 | `com.ninestreet.ns3.qa` | `com.ninestreet.ns3.prod` |
| NS-4 | `NS-4_QA` | `NS-4_PROD` | 9241 | 9240 | `com.ninestreet.ns4.qa` | `com.ninestreet.ns4.prod` |
| Portal | — | `Project_Nine_Street/portal.py` (single, env toggle) | — | 8000 | — | `com.ninestreet.portal.qa` |

- **All servers are stdlib `http.server`** (FastAPI/pydantic_core is broken on every interpreter on this
  machine — do not reintroduce FastAPI). NS-3_PROD + NS-4_PROD were ported from FastAPI in v2.2 (P7-A).
- **Interpreter for all NS/Alpha services:** CLT py3.9
  `/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3`.
  NS-1 used the repo venv `project_alpha_env` until 2026-08-02 — **that venv is gone** (was accidentally
  tracked in git; removed by the trunk fast-forward). Never depend on it. The `ai.openclaw.*` jobs use
  `~/.openclaw/workspace/` — separate sandbox, not ours.
- **Runtime state files are gitignored** (NS-2_PROD `ns2_signal_cache.json`, `ns2_watchlist.json`, etc.).
  Never commit service-generated JSON.

---

## 3. Deployment Procedure (tag-based)

### Pre-Deployment Checklist
- [ ] Working tree clean (`git status`); all QA tests green on `main`
- [ ] Walk-forward OOS regression green for any algo change (NS-2/NS-3 harnesses)
- [ ] Release tag `vX.Y.Z` created on `main` (`git tag -a vX.Y.Z -m "release vX.Y.Z"`)
- [ ] Tag pushed to origin
- [ ] Changelog updated

### Steps
```bash
git checkout main && git pull --ff-only
git fetch origin --tags

# tag (if not already done)
git tag -a v2.3.0 -m "release v2.3.0"
git push origin v2.3.0

# deploy: checks out the TAG into all _PROD dirs, reloads launchd, verifies health
./deploy_prod.sh v2.3.0
```

`deploy_prod.sh` (repo root, tag-based): verifies the tag exists (fetches tags if missing, aborts if
absent), checks out `Project_Sequoia/terminal` + `NS-1_PROD` + `NS-2_PROD` + `NS-3_PROD` + `NS-4_PROD`
from the tag, then **bootout/bootstrap** every PROD launchd job (see §5 — kickstart alone is not enough),
then health-checks ports 9098/9218/9228/9236/9240.

**Rollback:** `./deploy_prod.sh <previous-tag>` — e.g. `./deploy_prod.sh v2.2.0`.

---

## 4. Git Operations & GitHub Auth (pitfalls learned)

- **Pushes use a short-lived GitHub App installation token** (`~/github_installation_token.txt`,
  ~1h expiry, mint via `~/.hermes/agents/hermes/workspace/gen_github_token.py`).
  Push with an explicit tokenized URL and **scrub the token from output**:
  ```bash
  TOKEN=$(tr -d '[:space:]' < ~/github_installation_token.txt)
  git push "https://x-access-token:${TOKEN}@github.com/hqzhang2/Project_Alpha_POC.git" feature/x:feature/x \
    2>&1 | sed "s/${TOKEN}/<TOKEN>/g"
  ```
- **NEVER use `-u` with a tokenized URL** — it writes the token into `.git/config`. After any tokenized
  push, re-set upstream to the clean remote: `git branch --set-upstream-to=origin/<branch> <branch>`,
  and ensure `git remote set-url origin https://github.com/hqzhang2/Project_Alpha_POC.git` (no token).
  Verify: `grep -c "x-access-token\|ghs_" .git/config` must be `0`.
- **Tokenized pushes do not update local remote-tracking refs** — always `git fetch origin` before
  comparing local vs origin, or `git status -sb` shows bogus ahead/behind counts.
- **The GitHub App has push/contents scope but NOT Administration** — it cannot manage branch
  protection (403 `Resource not accessible by integration`). Protection changes are done manually
  in the GitHub UI by the owner (see §1 — already applied).
- New feature branch from trunk: `git checkout -b feature/x main`, push as above, then
  `git branch --set-upstream-to=origin/feature/x feature/x`.
- When cutting a release tag: `git tag -a vX.Y.Z` on `main` — tags never drift; branches do.

---

## 5. Launchd Management (critical)

### Reloading jobs — bootout/bootstrap, NOT kickstart alone
The classic trap: the on-disk plist can differ from the **loaded** job (launchd caches the loaded
definition). A `kickstart -k` restarts the *old* loaded definition — e.g. Alpha Terminal PROD ran
`QA_terminal/server.py` for weeks while the plist said `terminal/server.py`. Always reload:

```bash
launchctl bootout   gui/$(id -u)/com.ninestreet.ns1.prod
sleep 1
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ninestreet.ns1.prod.plist
launchctl kickstart -k gui/$(id -u)/com.ninestreet.ns1.prod   # bootstrap auto-starts; kickstart optional
```

### Editing plists safely
- **Back up first:** `mkdir -p ~/Library/LaunchAgents/backup-<date> && cp *.plist backup-<date>/`.
- Plists use `ProgramArguments = [bash, -lc, "<one big command string>"]`. To change the interpreter
  or script, **edit the command string in place** (`args[2] = args[2].replace(old, new)`), never
  replace the array element — replacing the element destroys the whole command (happened 2026-08-02:
  NS-1 crash-looped exit 127 until rebuilt from backup).
- After editing: `plutil -lint` (or reload via plistlib) and verify the full command string is intact.

### Verifying what a job actually runs
```bash
launchctl print gui/$(id -u)/<job> | sed -n '/arguments = {/,/}/p'   # loaded definition
ps -p $(lsof -iTCP:<port> -sTCP:LISTEN -n | tail -1 | awk '{print $2}') -o command=
```

---

## 6. Runtime Troubleshooting (learned 2026-08-02)

### Symptom: service accepts TCP but never answers HTTP
All servers are **single-threaded `http.server`** — one request at a time. A yfinance call stalled on
Yahoo's rate limiter (HTTP 429) blocks the whole server, including `/health` and static dashboards.
Signs: `nc -z` connects OK; `curl -v` connects and sends but no response; process alive with CPU time.

**Fix:** restart the job (§5 reload). Rate-limit stalls recur under bursty verification — verify
sparingly, prefer the 5-min server cache, and use `curl -4 -m <long>` (also: `curl localhost` may race
IPv6 `::1` vs IPv4 — use `-4` explicitly when a port binds IPv4 only).

### Symptom: launchd job exit 127 (command not found)
The interpreter or script path in the plist is broken. Reproduce directly:
`env -u PYTHONPATH <interp> <script>` from the service dir; check `launchctl print ... | grep "last exit"`.
Common cause: a referenced venv was deleted (see §2 venv note) or a plist edit corrupted the command.

### Health-check matrix (after any deploy)
```bash
for spec in "9098 Alpha-PROD" "9099 Alpha-QA" "9218 NS1-PROD" "9219 NS1-QA" \
            "9228 NS2-PROD" "9229 NS2-QA" "9236 NS3-PROD" "9237 NS3-QA" \
            "9240 NS4-PROD" "9241 NS4-QA"; do
  set -- $spec
  echo -n "port $1 ($2): "; curl -4 -s -m 20 http://127.0.0.1:$1/health; echo
done
curl -4 -s http://127.0.0.1:8000/ | grep -c "Trading Strategy Engine"   # portal
```
Notes: Alpha QA (9099) serves its dashboard HTML at `/health` (no JSON health route — that's normal).
NS-3 PROD health returns `{"status":"ok","service":"NS-3"}`; NS-1's includes live VIX data (slow first hit).

---

## 7. Post-Deployment Verification

- [ ] All 5 PROD `/health` endpoints 200 (`{status: ok}`)
- [ ] Each PROD port's process command shows the **`_PROD` dir** script (not a QA file) —
      this is the exact regression class that silently shipped QA mocks on PROD ports before v2.2
- [ ] Dashboards load: 9098 `/dashboard.html`, 9218 `/`, 9228 `/`, 9236 `/ns3_dashboard.html`, 9240 `/`
- [ ] Portal on 8000 shows all 5 strategy tabs + env toggle; PROD tab points at 90xx/92xx PROD ports
- [ ] Logs clean (`~/Library/LaunchAgents` stderr paths / service logs, no tracebacks)

---

## 8. Emergency / Escalation

| Severity | Response Time | Escalation |
|---|---|---|
| PROD down | < 15 min | On-call + dev lead |
| Degraded performance | < 1 hr | Dev team |
| NS-2 gate false positive | < 4 hr | Quant team + dev |

**Quick rollback:** `./deploy_prod.sh <previous-tag>` — immutable tags make this deterministic.

---

## 9. Directory Reference

```
/Users/chuck/Project_Alpha_POC/
├── Project_Sequoia/
│   ├── QA_terminal/          # Alpha Terminal QA (trunk)
│   └── terminal/             # Alpha Terminal PROD (from release tag)
├── Project_Nine_Street/
│   ├── NS_1_QA/              # NS-1 QA (trunk)
│   ├── NS-1_PROD/            # NS-1 PROD (from release tag)
│   ├── NS-2_QA/              # NS-2 QA (trunk)
│   ├── NS-2_PROD/            # NS-2 PROD (from release tag)
│   ├── NS-3_QA/              # NS-3 QA (trunk) - validated 3-tier algo
│   ├── NS-3_PROD/            # NS-3 PROD (from release tag) - stdlib backend
│   ├── NS-4_QA/              # NS-4 QA (trunk)
│   ├── NS-4_PROD/            # NS-4 PROD (from release tag) - stdlib backend
│   ├── portal.py             # Portal (single, env toggle built in)
│   └── test_ns_smoke.py      # QA+PROD smoke suite (import + health for all servers)
├── deploy_prod.sh            # Tag-based deploy (repo root)
└── common/                   # Shared lib (re-exports: fit_hmm, rsi, macd, ...)
```

---

*Version 2.0 — 2026-08-02 — Model A trunk-based + tag deploys; consolidated all v2.2-cycle lessons
(branch protection ruleset, launchd bootout/bootstrap, plist-edit safety, venv removal, runtime stalls).*
