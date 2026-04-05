// Update time
setInterval(() => {
    const timeEl = document.getElementById('time');
    if (timeEl) timeEl.textContent = new Date().toLocaleTimeString();
}, 1000);

let currentView = 'treemap';
let treemapInstance = null;
let quotesData = {};

let currentPeriod = '1D';

// Drilldown variables
let currentDrilldownTicker = null;
let drilldownHoldingsData = null;
let drilldownQuotesData = null;

function switchPeriod(period) {
    currentPeriod = period;
    document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
    event.target.classList.add('active');
    
    // Only re-render treemap since tabular already shows all periods
    if (currentView === 'treemap') {
        renderTreemap();
    }
}

function switchView(view) {
    currentView = view;
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.view-container').forEach(c => c.classList.remove('active'));
    
    event.target.classList.add('active');
    document.getElementById(view + 'View').classList.add('active');
    
    renderData();
}

async function loadData() {
    try {
        // Default to Asset Class ETFs
        let tickersArray = ['AGG', 'TLT', 'SPY', 'IBIT', 'DBC', 'GLD', 'EEM', 'FXI', 'IJH', 'IWM', 'QQQ', 'VEA'];
        
        try {
            if (localStorage.getItem('heatmap_watchlist')) {
                const stored = JSON.parse(localStorage.getItem('heatmap_watchlist'));
                if (stored && stored.length > 0) tickersArray = [...new Set([...tickersArray, ...stored])];
            }
        } catch(e) {}
        
        // Convert to comma-separated string
        const tickers = tickersArray.join(',');
        
        const res = await fetch(`/api/quotes?tickers=${tickers}`);
        quotesData = await res.json();
        
        renderData();
    } catch (e) {
        console.error('Heatmap load error:', e);
    }
}

function renderData() {
    if (currentView === 'treemap') {
        renderTreemap();
    } else {
        renderTabular();
    }
}

// Generates a hex intensity color from Red to Green
function getIntensityColorHex(value) {
    if (value === 0.0 || Math.abs(value) < 0.1) return '#30363d'; // Neutral Grey (around 0)
    
    if (value > 0.0) {
        if (value >= 10.0) return '#0d4a1b'; // Deep Dark Green
        if (value >= 5.0) return '#1a752b';  // Dark Green
        if (value >= 2.0) return '#238636';  // Medium Green
        return '#3fb950';                    // Light Green (0.1 to 2.0)
    } else {
        if (value <= -10.0) return '#8a1c1a'; // Deep Dark Red
        if (value <= -5.0) return '#b52522';  // Dark Red
        if (value <= -2.0) return '#da3633';  // Medium Red
        return '#ff7b72';                     // Light Red (-0.1 to -2.0)
    }
}

function renderTreemap() {
    if (treemapInstance) {
        treemapInstance.destroy();
    }
    
    let chartData = [];
    
    Object.values(quotesData).filter(q => !q.error && q.price).forEach(q => {
        // 1. Calculate Size (Market Cap or pseudo-cap for ETFs via Volume * Price)
        let size = q.market_cap;
        if (!size && q.volume && q.price) size = q.volume * q.price * 10; // Scale ETF size up a bit for visibility
        if (!size) size = 1e9; // Base 1B if data completely missing
        
        let perf = 0;
        if (currentPeriod === '1D') perf = q.change_pct || q.ret_1d || 0;
        else if (currentPeriod === '1W') perf = q.ret_1w || 0;
        else if (currentPeriod === '1M') perf = q.ret_1m || 0;
        else if (currentPeriod === '3M') perf = q.ret_3m || 0;
        else if (currentPeriod === 'YTD') perf = q.ret_ytd || 0;
        else if (currentPeriod === '1Y') perf = q.ret_1y || 0;
        
        chartData.push({
            x: `${q.ticker}\n${perf > 0 ? '+' : ''}${perf.toFixed(2)}%`,
            y: size,
            fillColor: getIntensityColorHex(perf)
        });
    });

    // Sort to make treemap layout cleaner (largest items top-left)
    chartData.sort((a, b) => b.y - a.y);

    const options = {
        series: [{
            data: chartData
        }],
        legend: { show: false },
        chart: {
            height: 600,
            type: 'treemap',
            toolbar: { show: false },
            background: 'transparent',
            animations: { enabled: false },
            events: {
                dataPointSelection: function(event, chartContext, config) {
                    const dataPoint = config.w.config.series[config.seriesIndex].data[config.dataPointIndex];
                    const ticker = dataPoint.x.split('\n')[0].trim();
                    loadDrilldown(ticker);
                }
            }
        },
        dataLabels: {
            enabled: true,
            style: {
                fontSize: '14px',
                fontWeight: 'bold',
                colors: ['#ffffff']
            }
        },
        plotOptions: {
            treemap: {
                enableShades: false,
                useFillColorAsBg: true
            }
        },
        stroke: {
            show: true,
            width: 2,
            colors: ['#0d1117']
        },
        tooltip: {
            theme: 'dark',
            y: {
                formatter: function(val) {
                    return val >= 1e12 ? (val/1e12).toFixed(2)+'T' : (val/1e9).toFixed(2)+'B';
                }
            }
        }
    };

    treemapInstance = new ApexCharts(document.querySelector("#treemapChart"), options);
    treemapInstance.render();
}

function renderTabular() {
    const container = document.getElementById('tabularContainer');
    
    let html = `<table class="tabular-table">
        <thead>
            <tr>
                <th style="text-align:left">Ticker</th>
                <th>Price</th>
                <th>1D</th>
                <th>1W</th>
                <th>1M</th>
                <th>3M</th>
                <th>YTD</th>
                <th>1Y</th>
            </tr>
        </thead>
        <tbody>
    `;
    
    // Sort alphabetically
    const sortedQuotes = Object.values(quotesData)
        .filter(q => !q.error)
        .sort((a, b) => a.ticker.localeCompare(b.ticker));
    
    sortedQuotes.forEach(q => {
        // The properties mapping from our Quotes API
        const periods = [
            { key: 'change_pct', fallback: 'ret_1d' },
            { key: 'ret_1w' },
            { key: 'ret_1m' },
            { key: 'ret_3m' },
            { key: 'ret_ytd' },
            { key: 'ret_1y' }
        ];
        
        html += `<tr>
            <td class="ticker">${q.ticker}</td>
            <td class="price">$${q.price?.toFixed(2) || '-'}</td>
        `;
        
        periods.forEach(p => {
            const val = q[p.key] ?? q[p.fallback] ?? 0;
            const color = getIntensityColorHex(val);
            const sign = val > 0 ? '+' : '';
            html += `<td class="perf" style="background-color: ${color};">
                ${sign}${val.toFixed(2)}%
            </td>`;
        });
        
        html += `</tr>`;
    });
    
    html += `</tbody></table>`;
    container.innerHTML = html;
}

function addHeatmapTicker() {
    const input = document.getElementById('heatmapAddTicker');
    const symbol = input.value.trim().toUpperCase();
    if (!symbol) return;
    
    let current = [];
    try {
        if (localStorage.getItem('heatmap_watchlist')) {
            current = JSON.parse(localStorage.getItem('heatmap_watchlist'));
        }
    } catch(e) {}
    
    if (!current.includes(symbol)) {
        current.push(symbol);
        localStorage.setItem('heatmap_watchlist', JSON.stringify(current));
        loadData();
    }
    input.value = '';
}

async function loadDrilldown(ticker) {
    currentDrilldownTicker = ticker;
    switchView('drilldown');
    
    document.getElementById('drilldownTitle').innerText = `Loading Top 50 Holdings for ${ticker}...`;
    document.getElementById('drilldownContainer').innerHTML = '<p style="color: #8b949e; margin-top: 20px;">Fetching ETF funds data...</p>';
    
    try {
        const hRes = await fetch(`/api/etf-holdings?ticker=${ticker}&limit=50`);
        drilldownHoldingsData = await hRes.json();
        
        if (drilldownHoldingsData.error || !drilldownHoldingsData.holdings) {
            document.getElementById('drilldownTitle').innerText = `${ticker} Holdings`;
            document.getElementById('drilldownContainer').innerHTML = `<p style="color: #ff7b72; margin-top: 20px;">Could not load ETF holdings: ${drilldownHoldingsData.error || 'Unknown error'}</p>`;
            return;
        }
        
        document.getElementById('drilldownTitle').innerText = `Fetching quotes for ${ticker} Holdings...`;
        
        const holdingTickers = Object.keys(drilldownHoldingsData.holdings).join(',');
        const qRes = await fetch(`/api/quotes?tickers=${holdingTickers}`);
        drilldownQuotesData = await qRes.json();
        
        renderDrilldown();
    } catch (e) {
        document.getElementById('drilldownTitle').innerText = `${ticker} Holdings Error`;
        document.getElementById('drilldownContainer').innerHTML = `<p style="color: #ff7b72; margin-top: 20px;">Error: ${e.message}</p>`;
    }
}

function renderDrilldown() {
    document.getElementById('drilldownTitle').innerText = `${currentDrilldownTicker} - Top 50 Weighted Holdings`;
    const container = document.getElementById('drilldownContainer');
    
    let html = `<table class="tabular-table">
        <thead>
            <tr>
                <th style="text-align:left">Ticker</th>
                <th>Weight %</th>
                <th>Price</th>
                <th>1D</th>
                <th>1W</th>
                <th>1M</th>
                <th>3M</th>
                <th>YTD</th>
                <th>1Y</th>
            </tr>
        </thead>
        <tbody>
    `;
    
    // Sort by weight descending
    const sortedTickers = Object.entries(drilldownHoldingsData.holdings)
        .sort((a, b) => b[1] - a[1]);
        
    sortedTickers.forEach(([ticker, weight]) => {
        const q = drilldownQuotesData[ticker] || { ticker, error: true };
        
        const periods = [
            { key: 'change_pct', fallback: 'ret_1d' },
            { key: 'ret_1w' },
            { key: 'ret_1m' },
            { key: 'ret_3m' },
            { key: 'ret_ytd' },
            { key: 'ret_1y' }
        ];
        
        html += `<tr>
            <td class="ticker">${ticker}</td>
            <td style="color: #8b949e; font-family: 'Monaco', monospace; background: #0d1117;">${(weight * 100).toFixed(2)}%</td>
            <td class="price">$${q.price?.toFixed(2) || '-'}</td>
        `;
        
        if (q.error || !q.price) {
            html += `<td colspan="6" style="color: #8b949e;">Data unavailable</td></tr>`;
            return;
        }
        
        periods.forEach(p => {
            const val = q[p.key] ?? q[p.fallback] ?? 0;
            const color = getIntensityColorHex(val);
            const sign = val > 0 ? '+' : '';
            html += `<td class="perf" style="background-color: ${color};">
                ${sign}${val.toFixed(2)}%
            </td>`;
        });
        
        html += `</tr>`;
    });
    
    html += `</tbody></table>`;
    container.innerHTML = html;
}

// Init immediately and set auto-refresh
loadData();
setInterval(loadData, 60000);