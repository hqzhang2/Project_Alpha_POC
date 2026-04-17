#!/bin/zsh
# Project Nine Street - PROD Environment Deployment Script
# STRICT PORT ENFORCEMENT: PROD is ALWAYS 9198
# WARNING: NEVER BYPASS THIS SCRIPT. DO NOT MANUALLY START SERVERS.

export ENV="PROD"
export PORT=9198
export DB_SCHEMA="ns_prod_db"

echo "Deploying Project Nine Street to PRODUCTION Environment..."
echo "Environment: $ENV | Port: $PORT | Schema: $DB_SCHEMA"

# TODO: Add Python startup commands for the Nine Street Dashboard
# python3 ns_dashboard.py --port $PORT --env $ENV
