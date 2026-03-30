#!/bin/bash
#
# Alpha Terminal Deployment Script
# Supports QA testing on alternate port and rollback capability
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TERMINAL_DIR="$SCRIPT_DIR"
PYTHON_ENV="$SCRIPT_DIR/../project_alpha_env/bin/python"
SERVER_SCRIPT="$TERMINAL_DIR/server.py"

# Ports
PROD_PORT=9098
QA_PORT=9099

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Check if server is running on a port
check_port() {
    local port=$1
    if curl -s "http://localhost:$port/dashboard.html" > /dev/null 2>&1; then
        return 0  # running
    else
        return 1  # not running
    fi
}

# Kill server on specific port
kill_port() {
    local port=$1
    log_info "Stopping server on port $port..."
    # Find and kill the process using the port
    fuser -k $port/tcp 2>/dev/null || true
    sleep 1
}

# Start server on port
start_server() {
    local port=$1
    log_info "Starting Alpha Terminal on port $port..."
    cd "$TERMINAL_DIR"
    nohup $PYTHON_ENV $SERVER_SCRIPT > "server_${port}.log" 2>&1 &
    local pid=$!
    sleep 3
    
    if check_port $port; then
        log_info "Server started successfully on port $port (PID: $pid)"
        echo $pid > ".server_${port}.pid"
        return 0
    else
        log_error "Failed to start server on port $port"
        return 1
    fi
}

# Get current active port
get_active_port() {
    if check_port $PROD_PORT; then
        echo $PROD_PORT
    elif check_port $QA_PORT; then
        echo $QA_PORT
    else
        echo "none"
    fi
}

# Usage
usage() {
    echo "Alpha Terminal Deployment Script"
    echo ""
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  start-qa        - Start server on QA port ($QA_PORT) for testing"
    echo "  start-prod      - Start server on production port ($PROD_PORT)"
    echo "  stop            - Stop server on specified port"
    echo "  status          - Show status of QA and Production servers"
    echo "  deploy          - Deploy to production (stop QA if running, start prod)"
    echo "  rollback        - Rollback to previous version (swap ports)"
    echo "  swap            - Swap between QA and Production"
    echo ""
    echo "Examples:"
    echo "  $0 start-qa     # Test new version on QA port"
    echo "  $0 deploy       # Release to production"
    echo "  $0 rollback     # Rollback to previous version"
}

case "$1" in
    start-qa)
        if check_port $QA_PORT; then
            log_warn "QA server already running on port $QA_PORT"
        else
            start_server $QA_PORT
        fi
        echo ""
        echo "Access QA at: http://localhost:$QA_PORT"
        ;;
    
    start-prod)
        if check_port $PROD_PORT; then
            log_warn "Production server already running on port $PROD_PORT"
        else
            start_server $PROD_PORT
        fi
        echo ""
        echo "Access Production at: http://localhost:$PROD_PORT"
        ;;
    
    stop)
        local port=${2:-$PROD_PORT}
        kill_port $port
        ;;
    
    status)
        echo "Alpha Terminal Status:"
        echo "====================="
        echo ""
        echo "Production (port $PROD_PORT): $(check_port $PROD_PORT && echo 'RUNNING' || echo 'STOPPED')"
        echo "QA (port $QA_PORT): $(check_port $QA_PORT && echo 'RUNNING' || echo 'STOPPED')"
        echo ""
        active=$(get_active_port)
        if [ "$active" != "none" ]; then
            echo "Active on: http://localhost:$active"
        fi
        ;;
    
    deploy)
        log_info "Deploying to production..."
        
        # Stop QA if running
        if check_port $QA_PORT; then
            log_info "Stopping QA server..."
            kill_port $QA_PORT
        fi
        
        # Start production
        if check_port $PROD_PORT; then
            log_warn "Production already running, restarting..."
            kill_port $PROD_PORT
            sleep 2
        fi
        
        start_server $PROD_PORT
        log_info "Deployment complete!"
        echo "Production URL: http://localhost:$PROD_PORT"
        ;;
    
    rollback)
        log_info "Rolling back..."
        
        # Swap: if prod is running, switch to QA; if QA running, switch to prod
        if check_port $PROD_PORT; then
            log_info "Switching from Production to QA..."
            kill_port $PROD_PORT
            sleep 2
            start_server $QA_PORT
            echo "Rolled back to QA: http://localhost:$QA_PORT"
        elif check_port $QA_PORT; then
            log_info "Switching from QA to Production..."
            kill_port $QA_PORT
            sleep 2
            start_server $PROD_PORT
            echo "Rolled back to Production: http://localhost:$PROD_PORT"
        else
            log_error "No server running to rollback"
        fi
        ;;
    
    swap)
        if check_port $PROD_PORT && check_port $QA_PORT; then
            log_error "Both ports running, cannot swap. Stop one first."
        elif check_port $PROD_PORT; then
            log_info "Swapping Production -> QA..."
            kill_port $PROD_PORT
            sleep 2
            start_server $QA_PORT
        elif check_port $QA_PORT; then
            log_info "Swapping QA -> Production..."
            kill_port $QA_PORT
            sleep 2
            start_server $PROD_PORT
        else
            log_error "No server running"
        fi
        ;;
    
    *)
        usage
        ;;
esac
