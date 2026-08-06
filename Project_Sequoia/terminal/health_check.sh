#!/usr/bin/env bash
# Health check script - runs after daily restart at 00:05
# Validates all critical endpoints and services

set -euo pipefail

LOG_FILE="/Users/chuck/Project_Alpha_POC/Project_Sequoia/QA_terminal/logs/health_check_$(date +%Y%m%d).log"
API_BASE="http://localhost:9099"

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

check_endpoint() {
    local name="$1"
    local url="$2"
    local expected_code="${3:-200}"
    
    local response_code
    response_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$url" || echo "000")
    
    if [[ "$response_code" == "$expected_code" ]]; then
        log "✅ $name: HTTP $response_code"
        return 0
    else
        log "❌ $name: HTTP $response_code (expected $expected_code)"
        return 1
    fi
}

check_json_endpoint() {
    local name="$1"
    local url="$2"
    local jq_filter="${3:-.status}"
    
    local response
    response=$(curl -s --max-time 10 "$url" 2>/dev/null || echo '{}')
    
    if echo "$response" | jq -e "$jq_filter" >/dev/null 2>&1; then
        log "✅ $name: JSON valid"
        return 0
    else
        log "❌ $name: Invalid JSON or missing field '$jq_filter'"
        return 1
    fi
}

main() {
    log "=== Alpha Terminal QA Health Check Started ==="
    log "Environment: QA (port 9099)"
    log "API Base: $API_BASE"
    
    local failed=0
    
    # Core endpoints
    check_endpoint "Health Check" "$API_BASE/api/health" || ((failed++))
    check_endpoint "Dashboard" "$API_BASE/" || ((failed++))
    check_endpoint "OMON Page" "$API_BASE/omon.html" || ((failed++))
    check_endpoint "Options Screener" "$API_BASE/screener.html" || ((failed++))
    check_endpoint "Financial Analysis" "$API_BASE/financials.html" || ((failed++))
    check_endpoint "Ratio Analysis" "$API_BASE/ratio.html" || ((failed++))
    check_endpoint "Prediction" "$API_BASE/prediction.html" || ((failed++))
    
    # API endpoints with expected JSON structure validation
    check_json_endpoint "Quotes API" "$API_BASE/api/quotes?tickers=SPY" ".SPY" || ((failed++))
    check_json_endpoint "Chart API" "$API_BASE/api/chart?ticker=SPY&tf=1D" ".labels" || ((failed++))
    check_json_endpoint "Ratio API" "$API_BASE/api/ratio?t1=XLE&t2=SPY&tf=1Y&sma=20" ".ratio" || ((failed++))
    check_json_endpoint "SEC Financials" "$API_BASE/api/sec/financials?ticker=AAPL&periods=4&type=Q" ".income" || ((failed++))
    check_json_endpoint "Options Chain" "$API_BASE/api/options?ticker=SPY&expiry=2026-07-17" ".calls" || ((failed++))
    check_json_endpoint "Option Screener" "$API_BASE/api/screen?ticker=SPY" ".results" || ((failed++))
    check_json_endpoint "Expirations" "$API_BASE/api/expirations?ticker=SPY" ".expirations" || ((failed++))
    check_json_endpoint "Estimates" "$API_BASE/api/estimates?ticker=AAPL" ".summary" || ((failed++))
    check_json_endpoint "Predictions" "$API_BASE/api/prediction" ".[0]" || ((failed++))
    check_json_endpoint "ETF Holdings" "$API_BASE/api/etf-holdings?ticker=SPY&limit=10" ".holdings" || ((failed++))
    check_json_endpoint "News Top" "$API_BASE/api/news/top?cat=general" ". | length >= 0" || ((failed++))
    check_json_endpoint "News CN" "$API_BASE/api/news/cn" ".[0]" || ((failed++))
    
    # Services check
    log "=== Checking Other Services ==="
    
    # Alpha Terminal PROD (port 9098)
    if check_endpoint "Alpha Terminal PROD" "http://localhost:9098/api/health" 2>/dev/null; then
        log "✅ Alpha Terminal PROD: Running"
    else
        log "ℹ️ Alpha Terminal PROD: Not running (expected if not started)"
    fi
    
    # NS-1 (port 9199)
    if check_endpoint "NS-1" "http://localhost:9199" 2>/dev/null; then
        log "✅ NS-1: Running"
    else
        log "ℹ️ NS-1: Not running"
    fi
    
    # NS-3 (port 9206)
    if check_endpoint "NS-3 Backend" "http://localhost:9206/api/health" 2>/dev/null; then
        log "✅ NS-3 Backend: Running"
    else
        log "ℹ️ NS-3 Backend: Not running"
    fi
    
    # NS-4 (port 9210)
    if check_endpoint "NS-4" "http://localhost:9210" 2>/dev/null; then
        log "✅ NS-4: Running"
    else
        log "ℹ️ NS-4: Not running"
    fi
    
    # Portal (port 8000)
    if check_endpoint "Portal" "http://localhost:8000" 2>/dev/null; then
        log "✅ Portal: Running"
    else
        log "ℹ️ Portal: Not running"
    fi
    
    # Summary
    log "=== Health Check Complete ==="
    log "Failed checks: $failed"
    
    if [[ $failed -eq 0 ]]; then
        log "🎉 All critical checks PASSED"
        exit 0
    else
        log "⚠️  $failed check(s) FAILED"
        exit 1
    fi
}

main "$@"