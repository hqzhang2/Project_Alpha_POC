#!/bin/bash
# restart_stale_services.sh — code-staleness watchdog for launchd services.
#
# WHY (2026-08-10, Hong): two incidents where a service kept running code
# from BEFORE new commits landed — A_T 9099 returned an HTML 404 for
# /api/regime ("Unexpected token '<'"), NS-5 QA 9251 returned no regime key
# ("axis disabled"). Root cause in both: the launchd process predated the
# commits and nothing restarted it.
#
# FIX (Option 2): every 60s, for each service, compare the newest *.py mtime
# in its code directory (plus the shared common/ package) against the running
# process start time. If code is newer than the process → kickstart the job.
# Only *.py files are watched — logs/caches/DBs never trigger a restart.
#
# NOTE: kickstart handles CODE changes. Plist ENV changes still need
# bootout+bootstrap (kickstart does not reload EnvironmentVariables).
#
# Install as a launchd timer job:
#   com.ninestreet.code-watchdog.plist  (StartInterval 60)

set -uo pipefail

LOG="$HOME/Library/Logs/ns_code_watchdog.log"
COMMON_DIR="/Users/chuck/Project_Alpha_POC/common"
PY="/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3"

# label|entry_py|common_flag  (entry paths exactly as in each plist's
# ProgramArguments; common_flag=1 ONLY for services that import the shared
# common/ package — verified 2026-08-10: A_T + NS-5 do, NS-1..4 don't)
SERVICES=(
  "com.alpha.terminal.qa|/Users/chuck/Project_Alpha_POC/Project_Sequoia/QA_terminal/server.py|1"
  "com.alpha.terminal.prod|/Users/chuck/Project_Alpha_POC/Project_Sequoia/terminal/server.py|1"
  "com.ninestreet.ns1.qa|/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS_1_QA/server_qa.py|0"
  "com.ninestreet.ns1.prod|/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-1_PROD/server_qa.py|0"
  "com.ninestreet.ns2.qa|/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-2_QA/qa_server.py|0"
  "com.ninestreet.ns2.prod|/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-2_PROD/qa_server.py|0"
  "com.ninestreet.ns3.qa|/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-3_QA/qa_server.py|0"
  "com.ninestreet.ns3.prod|/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-3_PROD/backend/main.py|0"
  "com.ninestreet.ns4.qa|/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-4_QA/qa_server.py|0"
  "com.ninestreet.ns4.prod|/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-4_PROD/backend/main.py|0"
  "com.ninestreet.ns5.qa|/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-5_QA/qa_server.py|1"
  "com.ninestreet.ns5.prod|/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-5_PROD/qa_server.py|1"
  "com.ninestreet.portal.qa|/Users/chuck/Project_Alpha_POC/Project_Nine_Street/portal.py|0"
)

now_epoch=$(date +%s)

for entry in "${SERVICES[@]}"; do
  label="${entry%%|*}"
  rest="${entry#*|}"
  entry_py="${rest%%|*}"
  use_common="${rest##*|}"
  srcdir="$(dirname "$entry_py")"

  # ── 1. Running PID? (skip if launchd has it stopped) ──
  pid=$(launchctl list "$label" 2>/dev/null | "$PY" -c "
import sys, json, re
txt = sys.stdin.read()
m = re.search(r'\"PID\"\s*=\s*(\d+)', txt)
print(m.group(1) if m else '')
" 2>/dev/null)
  [ -n "$pid" ] || continue

  # ── 2. Process start epoch ──
  lstart=$(ps -p "$pid" -o lstart= 2>/dev/null)
  [ -n "$lstart" ] || continue
  start_epoch=$(date -j -f "%a %b %d %T %Y" "$lstart" "+%s" 2>/dev/null)
  [ -n "$start_epoch" ] || continue

  # ── 3. Newest *.py mtime in service dir (+ shared common/ if used) ──
  newest=0
  dirs=("$srcdir")
  [ "$use_common" = "1" ] && dirs+=("$COMMON_DIR")
  for d in "${dirs[@]}"; do
    [ -d "$d" ] || continue
    m=$(find "$d" -name "*.py" -type f -exec stat -f %m {} \; 2>/dev/null | sort -n | tail -1)
    [ -n "$m" ] && [ "$m" -gt "$newest" ] && newest=$m
  done
  [ "$newest" -gt 0 ] || continue

  # ── 4. Stale? (code newer than process) → kickstart ──
  if [ "$newest" -gt "$start_epoch" ]; then
    echo "$(date '+%F %T') kickstart $label (code $newest > proc $start_epoch, srcdir $srcdir)" >> "$LOG"
    launchctl kickstart -k "gui/$(id -u)/$label" 2>/dev/null
  fi
done
