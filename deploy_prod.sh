#!/bin/bash
# deploy_prod.sh - Deploy release branch to all PROD dirs, restart, verify.
# Usage: ./deploy_prod.sh [release-branch]   (default: release/v2.0)
# NOTE: run from repo root. Per project rule: PROD is ONLY updated via this
# formal deploy (release branch checkout into _PROD dirs + launchd restart).
# No mid-cycle PROD upgrades.
set -euo pipefail

RELEASE="${1:-release/v2.0}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "== Deploying $RELEASE to all PROD directories =="
git fetch origin "$RELEASE" 2>/dev/null || true

SERVICES=(
    "Project_Sequoia/terminal"
    "Project_Nine_Street/NS-1_PROD"
    "Project_Nine_Street/NS-2_PROD"
    "Project_Nine_Street/NS-3_PROD"
    "Project_Nine_Street/NS-4_PROD"
)
for s in "${SERVICES[@]}"; do
    if git cat-file -e "$RELEASE:$s" 2>/dev/null; then
        git checkout "$RELEASE" -- "$s"
        echo "  checked out $s"
    else
        echo "  WARN: $s not present on $RELEASE - skipped"
    fi
done

echo "== Restarting PROD launchd services =="
for job in com.alpha.terminal.prod com.ninestreet.ns1.prod com.ninestreet.ns2.prod \
           com.ninestreet.ns3.prod com.ninestreet.ns4.prod; do
    if launchctl list 2>/dev/null | grep -q "$job"; then
        launchctl kickstart -k "gui/$(id -u)/$job"
        echo "  restarted $job"
    else
        echo "  WARN: $job not loaded - skipping"
    fi
done

echo "== Verifying health endpoints =="
sleep 3
for port in 9098 9218 9228 9236 9240; do
    if curl -sf -m 5 "http://localhost:$port/health" >/dev/null; then
        echo "  Port $port: OK"
    else
        echo "  Port $port: FAILED"
        exit 1
    fi
done
echo "Deployment complete."
