#!/bin/bash
# deploy_prod.sh - Deploy a release TAG to all PROD dirs, restart, verify.
# Usage: ./deploy_prod.sh [tag]   (default: v2.2.0)
# NOTE: run from repo root. Per project rule: PROD is ONLY updated via this
# formal deploy (release TAG checkout into _PROD dirs + launchd restart).
# No mid-cycle PROD upgrades. Trunk-based model: main = trunk, PROD = last tag.
set -euo pipefail

RELEASE="${1:-v2.2.0}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Ensure the tag exists locally (fetch tags; tag names never drift)
if ! git rev-parse -q --verify "refs/tags/$RELEASE" >/dev/null; then
    git fetch origin --tags 2>/dev/null || true
fi
if ! git rev-parse -q --verify "refs/tags/$RELEASE" >/dev/null; then
    echo "ERROR: tag $RELEASE not found (git tag -l). Aborting."
    exit 1
fi

echo "== Deploying tag $RELEASE to all PROD directories =="

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
        echo "  WARN: $s not present in $RELEASE - skipped"
    fi
done

echo "== Reloading PROD launchd jobs (bootout/bootstrap: plists may have changed) =="
for job in com.alpha.terminal.prod com.ninestreet.ns1.prod com.ninestreet.ns2.prod \
           com.ninestreet.ns3.prod com.ninestreet.ns4.prod; do
    if [ -f "$HOME/Library/LaunchAgents/$job.plist" ]; then
        launchctl bootout "gui/$(id -u)/$job" 2>/dev/null || true
        sleep 1
        launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$job.plist" 2>/dev/null \
            && echo "  reloaded $job" || echo "  WARN: $job bootstrap failed"
    else
        echo "  WARN: $job plist missing - skipping"
    fi
done

echo "== Verifying health endpoints =="
sleep 6
for port in 9098 9218 9228 9236 9240; do
    if curl -sf -m 8 "http://localhost:$port/health" >/dev/null; then
        echo "  Port $port: OK"
    else
        echo "  Port $port: FAILED"
        exit 1
    fi
done
echo "Deployment complete."
