#!/bin/zsh
# Project Alpha: Daily Report Automation Runner
export PATH="/opt/homebrew/opt/postgresql@18/bin:$PATH"
export EMAIL_USER="munger6c@gmail.com"
export EMAIL_PASSWORD="vvqwcmdgjyhfpjhp"
cd /Users/chuck/.openclaw/workspace
/usr/bin/python3 reports_engine_pg.py >> /Users/chuck/.openclaw/workspace/automation.log 2>&1
