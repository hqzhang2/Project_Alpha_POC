# L1 Production Support Runbook — Trading Strategy Engine (QA)

**Audience:** Level-1 production support. You do **not** write or change code.
Your job is to **monitor** the systems, **confirm they are up**, and **restart**
them when asked or when they are down. This document tells you *what* each
component is, *where its code lives* (so you can point engineers at it), and the
exact commands to check health and to bring a system down or up.

> **Golden rule:** If a restart does not fix it within 5 minutes, or you are
> unsure, **escalate to an engineer**. Do not guess, do not edit files.

---

## 1. The big picture (what the user sees)

The "Trading Strategy Engine" is a set of small web applications that run on a
single Mac mini (the "QA server"). Users open one web page — the **Portal** —
and it shows four tabs side by side:

| Tab in the Portal | What it is | Internal name |
|-------------------|-----------|---------------|
| **Alpha Terminal** | Main strategy/analytics dashboard | Alpha Terminal QA |
| **NS-1** | Nine Street strategy dashboard #1 | NS-1 QA |
| **NS-3** | Nine Street strategy dashboard #3 | NS-3 QA |
| **NS-4** | Nine Street strategy dashboard #4 | NS-4 QA |

Each tab is a separate program running on its own **port number**. The Portal
just displays them together in one window.

There are **5 programs** in total (the Portal + the 4 dashboards behind its tabs).

---

## 2. The components at a glance

| # | Component | Port | What it does | Where the code lives |
|---|-----------|------|--------------|----------------------|
| 1 | **Portal** | 8000 | The window users open; shows the 4 tabs | `Project_Nine_Street/portal.py` |
| 2 | **Alpha Terminal QA** | 9099 | Alpha strategy dashboard | `Project_Sequoia/QA_terminal/server.py` |
| 3 | **NS-1 QA** | 9219 | Nine Street dashboard #1 | `Project_Nine_Street/NS_1_QA/server_qa.py` |
| 4 | **NS-3 QA** | 9237 | Nine Street dashboard #3 | `Project_Nine_Street/NS-3_QA/qa_server.py` |
| 5 | **NS-4 QA** | 9241 | Nine Street dashboard #4 | `Project_Nine_Street/NS-4_QA/qa_server.py` |

All code lives under the project folder:
`/Users/chuck/Project_Alpha_POC/`

> You never need to open these files. They are listed so you can tell an
> engineer *exactly* which component is affected.

---

## 3. How to check if everything is healthy (monitoring)

Run this **one block of commands** in a terminal. It checks all 5 programs.

```bash
curl -s -o /dev/null -w "Portal       :8000  %{http_code}\n" http://localhost:8000/
curl -s -o /dev/null -w "Alpha Term   :9099  %{http_code}\n" http://localhost:9099/
curl -s -o /dev/null -w "NS-1         :9219  %{http_code}\n" http://localhost:9219/health
curl -s -o /dev/null -w "NS-3         :9237  %{http_code}\n" http://localhost:9237/health
curl -s -o /dev/null -w "NS-4         :9241  %{http_code}\n" http://localhost:9241/health
```

**How to read the result:** each line ends in a number.
- `200` = that program is **up and healthy**. ✅
- anything else (`000`, `404`, `500`, connection refused) = that program is
  **down or broken**. ⚠️

Also check the Alpha Terminal's detail status (optional but useful):
```bash
curl -s http://localhost:9099/health
```
A healthy reply looks like: `{"status": "ok", ...}`.

### Quick "is the user-facing page up?" check
Open a browser to `http://localhost:8000/` — if the Portal loads with all four
tabs showing data (not blank, not "Error"), the system is serving users.

---

## 4. Bringing a system DOWN (stop)

Only stop a component when told to, or when it is misbehaving and an engineer
has authorized a restart.

### Alpha Terminal QA (port 9099) — preferred method
This one is managed automatically by the OS and will try to restart itself.
To stop it cleanly:
```bash
launchctl unload ~/Library/LaunchAgents/com.alpha.terminal.qa.plist
```

### Portal, NS-1, NS-3, NS-4 — manual stop
These are not auto-managed, so you stop them by their port. Replace `PORT`
with the port from the table in Section 2 (8000, 9219, 9237, or 9241):
```bash
lsof -ti :PORT | xargs kill
```
Example — stop NS-3 (port 9237):
```bash
lsof -ti :9237 | xargs kill
```

> **Stop-then-start in one move:** to restart a service cleanly, `unload`
> first, then `load`. This avoids an "Address already in use" race if the old
> process has not yet released the port:
> ```bash
> launchctl unload ~/Library/LaunchAgents/com.ninestreet.ns3.qa.plist
> launchctl load  ~/Library/LaunchAgents/com.ninestreet.ns3.qa.plist
> ```
> (Substitute the correct label per Section 5.) The plists already sleep 2s
> before binding to let the prior socket release.

---

## 5. Bringing a system UP (start)

> **Important — use these exact commands.** Starting a program the "wrong
> way" (e.g. double-clicking a file, or a plain `python3` command) can cause
> it to crash. Always use the commands below.

### Alpha Terminal QA (port 9099) — preferred method
```bash
launchctl load ~/Library/LaunchAgents/com.alpha.terminal.qa.plist
```
Then verify with the Section 3 check (port 9099 should return `200`).

### Alpha Terminal QA (port 9099)
```bash
launchctl load ~/Library/LaunchAgents/com.alpha.terminal.qa.plist
```

### Portal (port 8000)
```bash
launchctl load ~/Library/LaunchAgents/com.ninestreet.portal.qa.plist
```

### NS-1 (port 9219)
```bash
launchctl load ~/Library/LaunchAgents/com.ninestreet.ns1.qa.plist
```

### NS-2 (port 9229)
```bash
launchctl load ~/Library/LaunchAgents/com.ninestreet.ns2.qa.plist
```

### NS-3 (port 9237)
```bash
launchctl load ~/Library/LaunchAgents/com.ninestreet.ns3.qa.plist
```

### NS-4 (port 9241)
```bash
launchctl load ~/Library/LaunchAgents/com.ninestreet.ns4.qa.plist
```

After starting any of the above, wait ~3 seconds and run the Section 3 check
to confirm it returns `200`.

> **Manual start (fallback only):** if `launchctl load` fails, you may start
> a service by hand, but you must use the clean-environment command so it does
> not crash:
> ```bash
> cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/<DIR> && \
> env -u PYTHONPATH /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3 <SCRIPT>.py &
> ```
> (NS-1 dir `NS_1_QA` script `server_qa.py`; NS-2/3/4 dir `NS-2_QA`/`NS-3_QA`/
> `NS-4_QA` script `qa_server.py`; Portal dir `Project_Nine_Street` script
> `portal.py`.)

---

## 6. Common situations & what to do

| Symptom | Likely cause | L1 action |
|---------|--------------|-----------|
| All tabs blank in Portal | Portal (8000) down | `launchctl load` Portal (Section 5) |
| One tab blank, others fine | That one dashboard down | `launchctl load` that service (Section 5) |
| "Error: HTTP 500" inside a tab | That dashboard's data service errored | `launchctl unload` then `launchctl load` that service; if it returns, escalate |
| Health check returns `000` for a port | Program not running | `launchctl load` that service (Section 5) |
| Service keeps dying (restarts repeatedly) | Fatal startup error | Check its log in `Project_Nine_Street/logs/`; escalate with the error |

> **All services are auto-managed by launchd** (`RunAtLoad` + `KeepAlive`), so
> they start on boot and **auto-restart if they crash**. After a Mac reboot,
> every service (Alpha Terminal, Portal, NS-1/2/3/4) comes back on its own.
> You normally only intervene when a service is in a crash loop or a data
> source errors.

---

## 7. Logs (for engineers — you just point them here)

If you escalate, tell the engineer which component and include the log
location:

| Component | Log location |
|-----------|--------------|
| Alpha Terminal QA | `Project_Sequoia/QA_terminal/logs/` |
| Portal / NS-1 / NS-3 / NS-4 | No log folder yet — engineer checks the terminal window where it was started |

You do **not** need to read these. Just mention the path when you escalate.

---

## 8. Escalation

Escalate to an engineer when:
- A restart does not bring a component back within 5 minutes.
- The same component keeps going down.
- You see `500` errors that return after a restart.
- You are unsure about any step.

When you escalate, give them:
1. **Which component** (Portal / Alpha Terminal / NS-1 / NS-3 / NS-4).
2. **What the health check showed** (the `200` / `000` / `500` number).
3. **What you already tried** (e.g., "restarted NS-3, still 000").

---

*Document scope: monitoring, start/stop, component identification. No code
changes are performed by L1 support.*
