#!/bin/zsh
# Project Nine Street - QA Environment Deployment Script
# STRICT PORT ENFORCEMENT: QA is ALWAYS 9199

export ENV="QA"
export PORT=9199
export DB_SCHEMA="ns_qa_db"

echo "Deploying Project Nine Street to QA Environment..."
echo "Environment: $ENV | Port: $PORT | Schema: $DB_SCHEMA"

# Kill existing process on port 9199
lsof -ti:9199 | xargs kill -9 2>/dev/null || true

# Start the Nine Street Dashboard server in the background
nohup python3 server.py > server_9199.log 2>&1 &
echo "Server started on port $PORT"
