# Production Deployment Procedure

## Overview
All services (Alpha Terminal + NS-1/2/3/4) share a single monorepo and single release branch. One release branch = one version for ALL services. No per-service release branches.

---

## Directory Structure
```
/Users/chuck/Project_Alpha_POC/
├── Project_Sequoia/
│   ├── QA_terminal/          # Alpha Terminal QA (feature branch)
│   └── terminal/             # Alpha Terminal PROD (release branch)
├── Project_Nine_Street/
│   ├── NS_1_QA/              # NS-1 QA (feature branch)
│   ├── NS-2_QA/              # NS-2 QA (feature branch)
│   ├── NS-2_PROD/            # NS-2 PROD (release branch)
│   ├── NS-3_QA/              # NS-3 QA (feature branch)
│   ├── NS-3_PROD/            # NS-3 PROD (release branch)
│   ├── NS-4_QA/              # NS-4 QA (feature branch)
│   ├── NS-4_PROD/            # NS-4 PROD (release branch)
│   ├── NS_1_QA/              # Legacy NS-1 QA
│   └── ... (shared configs, scripts, docs)
```

---

## Service Inventory

| Service | QA Dir | PROD Dir | QA Port | PROD Port | Launchd Job (QA) | Launchd Job (PROD) |
|---|---|---|---|---|---|---|
| Alpha Terminal | `Project_Sequoia/QA_terminal` | `Project_Sequoia/terminal` | 9099 | 9098 | `com.alpha.terminal.qa` | `com.alpha.terminal.prod` |
| NS-1 | `NS_1_QA` / `NS-1_QA` | `NS-1_PROD` | 9219 | 9218 | `com.ninestreet.ns1.qa` | `com.ninestreet.ns1.prod` |
| NS-2 | `NS-2_QA` | `NS-2_PROD` | 9229 | 9228 | `com.ninestreet.ns2.qa` | `com.ninestreet.ns2.prod` |
| NS-3 | `NS-3_QA` | `NS-3_PROD` | 9237 | 9236 | `com.ninestreet.ns3.qa` | `com.ninestreet.ns3.prod` |
| NS-4 | `NS-4_QA` | `NS-4_PROD` | 9241 | 9240 | `com.ninestreet.ns4.qa` | `com.ninestreet.ns4.prod` |

---

## Branch Strategy
| Environment | Branch Pattern | Purpose |
|---|---|---|
| QA | `feature/vX.Y` | Development, testing, commits allowed |
| PROD | `release/vX.Y` | Stable, no commits, deployment source |

**Critical rule:** Single release branch = single version for ALL services. No per-service release branches.

---

## Deployment Procedure

### Pre-Deployment Checklist
- [ ] All QA testing passed on `feature/vX.Y`
- [ ] Release branch `release/vX.Y` created from `feature/vX.Y`
- [ ] Release branch pushed to origin
- [ ] Walk-forward backtest run for NS-2 (if applicable)
- [ ] Changelog updated

### Deployment Steps

```bash
# 1. Verify current state
git status                          # must be clean
git fetch origin

# 2. Checkout release branch locally
git checkout release/vX.Y           # e.g., release/v2.0

# 3. Deploy to ALL PROD directories
git checkout release/vX.Y -- Project_Sequoia/terminal
# NS-1 PROD (if directory exists)
# git checkout release/vX.Y -- Project_Nine_Street/NS_1_PROD
git checkout release/vX.Y -- Project_Nine_Street/NS-2_PROD
git checkout release/vX.Y -- Project_Nine_Street/NS-3_PROD
git checkout release/vX.Y -- Project_Nine_Street/NS-4_PROD

# 4. Restart ALL PROD launchd services
launchctl kickstart -k gui/$(id -u)/com.alpha.terminal.prod
launchctl kickstart -k gui/$(id -u)/com.ninestreet.ns1.prod
launchctl kickstart -k gui/$(id -u)/com.ninestreet.ns2.prod
launchctl kickstart -k gui/$(id -u)/com.ninestreet.ns3.prod
launchctl kickstart -k gui/$(id -u)/com.ninestreet.ns4.prod

# 5. Verify ALL PROD health endpoints
curl -s http://localhost:9098/health   # Alpha Terminal PROD
curl -s http://localhost:9218/health   # NS-1 PROD
curl -s http://localhost:9228/health   # NS-2 PROD
curl -s http://localhost:9236/health   # NS-3 PROD
curl -s http://localhost:9240/health   # NS-4 PROD

# 6. Return to feature branch for continued development
git checkout feature/vX.Y
```

### Automated Deploy Script
```bash
#!/bin/bash
# deploy_prod.sh - Deploy release branch to all PROD dirs
set -euo pipefail

RELEASE="${1:-release/v2.0}"
SERVICES=(
    "Project_Sequoia/terminal"
    "Project_Nine_Street/NS-1_PROD"
    "Project_Nine_Street/NS-2_PROD"
    "Project_Nine_Street/NS-3_PROD"
    "Project_Nine_Street/NS-4_PROD"
)

echo "Deploying $RELEASE to all PROD directories..."
git checkout "$RELEASE" -- "${SERVICES[@]}"

echo "Restarting PROD services..."
launchctl kickstart -k gui/$(id -u)/com.alpha.terminal.prod
launchctl kickstart -k gui/$(id -u)/com.ninestreet.ns1.prod
launchctl kickstart -k gui/$(id -u)/com.ninestreet.ns2.prod
launchctl kickstart -k gui/$(id -u)/com.ninestreet.ns3.prod
launchctl kickstart -k gui/$(id -u)/com.ninestreet.ns4.prod

echo "Verifying health..."
sleep 3
for port in 9098 9218 9228 9236 9240; do
    if curl -sf "http://localhost:$port/health" >/dev/null; then
        echo "  Port $port: OK"
    else
        echo "  Port $port: FAILED"
        exit 1
    fi
done
echo "Deployment complete."
```

### Rollback Procedure
```bash
# 1. Identify previous release tag
git tag -l "release/v*" | sort -V | tail -2

# 2. Deploy previous release
./deploy_prod.sh release/vX.Y-1

# 3. Or manually checkout previous release
git checkout release/vX.Y-1 -- "${SERVICES[@]}"
# restart services, verify health
```

---

## Environment Isolation (Launchd & Monitoring)

### Launchd Separation
- Each environment has **separate launchd jobs** (see table above)
- Separate log files per environment:
  - QA: `/Users/chuck/Project_Alpha_POC/Project_Nine_Street/logs/ns2.out.log`
  - PROD: `/Users/chuck/Project_Alpha_POC/Project_Nine_Street/logs/ns2_prod.out.log`
- Separate working directories (`WorkingDirectory` in plist)
- Separate environment variables (`ENV=QA` vs `ENV=PROD`)

### Monitoring Separation
| Aspect | QA | PROD |
|---|---|---|
| Health endpoint | `http://localhost:9099/health` | `http://localhost:9098/health` |
| Log files | `*_qa.out.log` | `*_prod.out.log` |
| Metrics | Separate dashboards | Separate dashboards |
| Alerting | Dev team | On-call + dev team |

---

## Configuration Management

### Secrets
- **Never commit** secrets to git
- Use `github_installation_token.txt` (gitignored) for GitHub auth
- Environment-specific `.env` files (gitignored) for API keys
- Launchd `EnvironmentVariables` for runtime config

### Environment Variables
```plist
<key>EnvironmentVariables</key>
<dict>
    <key>PORT</key><string>9228</string>
    <key>ENV</key><string>PROD</string>
    <key>PYTHONPATH</key><string></string>
</dict>
```

---

## Post-Deployment Verification

### Automated Checks
- [ ] All `/health` endpoints return `{"status":"ok"}`
- [ ] NS-2 walk-forward gate status matches expectations
- [ ] Logs show clean startup (no errors in `*_prod.err.log`)
- [ ] Ports listening on correct interfaces

### Manual Spot-Checks
- [ ] NS-2 dashboard loads at `http://localhost:9228/`
- [ ] Alpha Terminal dashboard loads at `http://localhost:9098/`
- [ ] NS-3/NS-4 dashboards accessible
- [ ] No cross-environment contamination (QA data not in PROD logs)

---

## Rollback Procedure

```bash
# 1. Identify previous release tag
git tag -l "release/v*" | sort -V | tail -2

# 2. Deploy previous release
./deploy_prod.sh release/vX.Y-1

# 3. Or manually checkout previous release
git checkout release/vX.Y-1 -- "${SERVICES[@]}"
# restart services, verify health
```

---

## Emergency Contacts & Escalation

| Severity | Response Time | Escalation |
|---|---|---|
| PROD down | < 15 min | Page on-call + dev lead |
| Degraded performance | < 1 hr | Dev team |
| NS-2 gate false positive | < 4 hr | Quant team + dev |

---

## Directory Reference

```
/Users/chuck/Project_Alpha_POC/
├── Project_Sequoia/
│   ├── QA_terminal/      ← Alpha Terminal QA (feature branch)
│   └── terminal/         ← Alpha Terminal PROD (release branch)
├── Project_Nine_Street/
│   ├── NS_1_QA/          ← NS-1 QA (feature branch)
│   ├── NS-2_QA/          ← NS-2 QA (feature branch)
│   ├── NS-2_PROD/        ← NS-2 PROD (release branch)
│   ├── NS-3_QA/          ← NS-3 QA (feature branch)
│   ├── NS-3_PROD/        ← NS-3 PROD (release branch)
│   ├── NS-4_QA/          ← NS-4 QA (feature branch)
│   ├── NS-4_PROD/        ← NS-4 PROD (release branch)
│   ├── NS_1_QA/          ← Legacy NS-1 QA
│   └── ... (shared configs, scripts, docs)
```

---

*Version 1.0 - 2026-07-30 - Initial production deployment procedure*